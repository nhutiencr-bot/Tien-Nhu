import pandas as pd
import numpy as np
import streamlit as st
from news_fetcher import fetch_news_with_fallback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company
from financial_normalizer import (
    find_row_series, build_5y_financial_table, build_financial_table,
    get_latest, get_latest_n_years, cagr,
)
from valuation import (
    dupont_decomposition, dcf_fcff_scenarios, reverse_dcf_implied_growth,
    graham_number, ddm_gordon, nine_methods_valuation, summarize_valuation,
    detect_stock_dividend_years, normalize_eps_bvps_series, estimate_wacc,
)
from cafef_fallback import fetch_cafef_balance_sheet_5y

SOURCE_FALLBACK_ORDER = ['KBS', 'VCI', 'DNSE']
DEFAULT_YEAR_LIMIT = 5


def normalize_to_billion_vnd(series):
    """
    FIX: ngưỡng cũ 1e11 (100 tỷ VNĐ) sai — công ty có doanh thu/LNST raw
    dưới 100 tỷ VNĐ (vd 44,752,636,000đ = 44.75 tỷ) sẽ KHÔNG được chia,
    trong khi Vốn CSH/Tổng tài sản (luôn lớn) vẫn chia đúng → lệch đơn vị
    → ROE/ROA bị nhân lên hàng triệu %.
    Ngưỡng đúng: 1e7 (10 triệu). Không công ty thật nào có raw VNĐ nằm
    giữa 10 triệu và 100 tỷ nhưng lại KHÔNG cần chia — mọi số liệu BCTC
    nguồn trả về đều ở đơn vị "đồng" (>> 10 triệu) hoặc đã ở đơn vị "tỷ"
    (<< 10 triệu). Đồng bộ với financial_normalizer.normalize_to_billion_vnd.
    """
    if series is None or series.empty:
        return series
    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            val = float(val)
            if abs(val) > 1e7:
                return round(val / 1e9, 2)
            return round(val, 2)
        except Exception:
            return None
    return series.map(_to_ty).dropna()


def _normalize_pct(s):
    """
    Chuẩn hoá series % về đúng thang 0-100.
    FIX: trước đây chỉ kiểm tra phần tử CUỐI (s.iloc[-1]) để quyết định
    nhân *100 cho CẢ series → nếu nguồn dữ liệu trộn lẫn định dạng
    (VD: vài năm là fraction 0.2059, vài năm đã là percent 20.59) thì
    sẽ nhân sai hàng loạt. Giờ xử lý TỪNG phần tử độc lập.
    """
    if s is None or s.empty:
        return s
    def _fix(v):
        try:
            if pd.isna(v):
                return v
            v = float(v)
            return v * 100 if abs(v) < 1 else v
        except Exception:
            return v
    return s.map(_fix)


def _sanitize_pct_series(s, max_abs=300.0):
    """
    Loại bỏ giá trị % vô lý (VD: ROE = 20,000,000% do lỗi parse/đơn vị từ
    nguồn dữ liệu). ROE/ROA thực tế hầu như không bao giờ vượt quá vài trăm %
    kể cả với doanh nghiệp đòn bẩy cao — dùng ngưỡng 300% làm giới hạn an toàn.
    """
    if s is None or s.empty:
        return s
    return s.where(s.abs() <= max_abs)


def normalize_net_profit_with_anchor(net_profit_raw, equity_series, roe_series):
    base = normalize_to_billion_vnd(net_profit_raw)
    if base is None or base.empty:
        return base
    # BUG FIX: roe_series đưa vào trước đây là dữ liệu RAW chưa chuẩn hoá
    # (có thể là fraction 0.2059 thay vì percent 20.59 tuỳ nguồn/tuỳ năm).
    # Nếu dùng raw để tính "expected", công thức equity*roe/100 sẽ sai lệch
    # đúng 100 lần → hàm suy ra sai power/divisor → chia/nhân net_profit
    # một cách sai lầm, làm hỏng toàn bộ chuỗi LNST.
    # Chuẩn hoá + sanitize roe_series trước khi dùng làm anchor.
    roe_series = _sanitize_pct_series(_normalize_pct(roe_series))
    fixed = {}
    for year, raw_val in base.items():
        if (year not in equity_series.index or year not in roe_series.index
                or pd.isna(equity_series.get(year)) or pd.isna(roe_series.get(year))):
            fixed[year] = raw_val
            continue
        expected = equity_series[year] * roe_series[year] / 100
        if expected == 0 or raw_val == 0:
            fixed[year] = raw_val
            continue
        ratio = raw_val / expected
        if ratio <= 0:
            fixed[year] = raw_val
            continue
        power = round(np.log10(ratio))
        divisor = 10 ** power
        fixed[year] = round(raw_val / divisor, 2)
    return pd.Series(fixed)


def _build_engines_with_fallback(ticker):
    last_error = None
    test_end   = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')
    for source in SOURCE_FALLBACK_ORDER:
        try:
            q_engine = Quote(symbol=ticker, source=source)
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError(f"Nguồn {source} trả về dữ liệu rỗng cho {ticker}")
            f_engine = Finance(symbol=ticker, source=source, period='year')
            c_engine = Company(symbol=ticker, source=source)
            return q_engine, f_engine, c_engine, source
        except Exception as e:
            last_error = e
            continue
    raise ConnectionError(
        f"Không lấy được dữ liệu cho mã {ticker} từ bất kỳ nguồn nào "
        f"({', '.join(SOURCE_FALLBACK_ORDER)}). Lỗi cuối cùng: {last_error}"
    )


def _safe_fetch(fn, default=None):
    try:
        result = fn()
        return result if result is not None else (default if default is not None else pd.DataFrame())
    except Exception:
        return default if default is not None else pd.DataFrame()


def _merge_financial_dataframes(dfs: list):
    dfs = [d for d in dfs if d is not None and not d.empty]
    if not dfs:
        return pd.DataFrame()
    if len(dfs) == 1:
        return dfs[0]

    def _year_cols(df):
        return [c for c in df.columns if re_fullmatch_year(c)]

    def re_fullmatch_year(c):
        c_str = str(c).strip()
        return c_str.replace('-', '').replace('Q', '').isdigit() and len(c_str) >= 4

    dfs_sorted = sorted(dfs, key=lambda d: len(_year_cols(d)), reverse=True)
    merged = dfs_sorted[0].copy()
    key_col = 'item' if 'item' in merged.columns else merged.columns[0]
    merged['_key_norm'] = merged[key_col].astype(str).str.lower().str.strip()

    for other in dfs_sorted[1:]:
        other_key_col = 'item' if 'item' in other.columns else other.columns[0]
        other_year_cols = [c for c in _year_cols(other) if c not in merged.columns]
        if not other_year_cols:
            continue
        other = other.copy()
        other['_key_norm'] = other[other_key_col].astype(str).str.lower().str.strip()
        sub = other[['_key_norm'] + other_year_cols]
        merged = merged.merge(sub, on='_key_norm', how='left')

    merged = merged.drop(columns=['_key_norm'])
    return merged


def _fetch_income_statement(ticker, source, period='year', limit=DEFAULT_YEAR_LIMIT):
    sources_to_try = [source] + [s for s in SOURCE_FALLBACK_ORDER if s != source]
    dfs = []
    for src in sources_to_try:
        try:
            f = Finance(symbol=ticker, source=src, period=period)
            try:
                df = f.income_statement(period=period, limit=limit)
            except TypeError:
                df = f.income_statement(period=period)
            if df is not None and not df.empty:
                dfs.append(df)
        except Exception:
            continue
    return _merge_financial_dataframes(dfs)


def _fetch_ratio(ticker, source, period='year', limit=DEFAULT_YEAR_LIMIT):
    sources_to_try = [source] + [s for s in SOURCE_FALLBACK_ORDER if s != source]
    dfs = []
    for src in sources_to_try:
        try:
            f = Finance(symbol=ticker, source=src, period=period)
            try:
                df = f.ratio(period=period, limit=limit)
            except TypeError:
                df = f.ratio(period=period)
            if df is not None and not df.empty:
                dfs.append(df)
        except Exception:
            continue
    return _merge_financial_dataframes(dfs)


def _fetch_cashflow(ticker, source, period='year', limit=DEFAULT_YEAR_LIMIT):
    sources_to_try = [source] + [s for s in SOURCE_FALLBACK_ORDER if s != source]
    dfs = []
    for src in sources_to_try:
        try:
            f = Finance(symbol=ticker, source=src, period=period)
            try:
                df = f.cash_flow(period=period, limit=limit)
            except TypeError:
                df = f.cash_flow(period=period)
            if df is not None and not df.empty:
                dfs.append(df)
        except Exception:
            continue
    return _merge_financial_dataframes(dfs)


def _fetch_balance_sheet(ticker, source, period='year', limit=DEFAULT_YEAR_LIMIT):
    sources_to_try = [source] + [s for s in SOURCE_FALLBACK_ORDER if s != source]
    dfs = []
    for src in sources_to_try:
        try:
            f = Finance(symbol=ticker, source=src, period=period)
            try:
                df = f.balance_sheet(period=period, limit=limit)
            except TypeError:
                df = f.balance_sheet(period=period)
            if df is not None and not df.empty:
                dfs.append(df)
        except Exception:
            continue
    return _merge_financial_dataframes(dfs)


@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker):
    try:
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)

        current_year_for_table = datetime.today().year
        allowed_years = set(range(current_year_for_table - DEFAULT_YEAR_LIMIT, current_year_for_table))

        end_date   = datetime.today().strftime('%Y-%m-%d')
        # BUG FIX: cửa sổ 3 năm không đủ để lấy "Giá cuối năm" cho toàn bộ
        # allowed_years (5 năm gần nhất, VD 2021-2025). Với 3 năm, giá của
        # 2021 và phần lớn 2022 bị thiếu hoàn toàn → cột "Giá cuối năm" trống.
        # Mở rộng lên (DEFAULT_YEAR_LIMIT + 1) năm để có buffer an toàn.
        start_date = (datetime.today() - timedelta(days=365 * (DEFAULT_YEAR_LIMIT + 1))).strftime('%Y-%m-%d')

        # FIX: fetch dư +2 năm so với DEFAULT_YEAR_LIMIT làm buffer — một số
        # nguồn tính "limit" theo số kỳ báo cáo đã công bố (kể cả kỳ ước tính
        # dở dang của năm hiện tại), nên limit=5 đúng nghĩa đôi khi chỉ trả
        # về 4 năm quá khứ đầy đủ + 1 kỳ hiện tại → 2021 bị rớt khỏi bảng.
        # allowed_years / _filter_years() ở dưới vẫn giới hạn hiển thị đúng
        # 5 năm (2021-2025), buffer chỉ để tránh mất dữ liệu ở nguồn.
        _fetch_limit = DEFAULT_YEAR_LIMIT + 2
        tasks = {
            "price":      lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            "overview":   lambda: c_engine.overview(),
            "income_y":   lambda: _fetch_income_statement(ticker, source_used, period='year',    limit=_fetch_limit),
            "cashflow_y": lambda: _fetch_cashflow(ticker,          source_used, period='year',    limit=_fetch_limit),
            "ratio_y":    lambda: _fetch_ratio(ticker,             source_used, period='year',    limit=_fetch_limit),
            "income_q":   lambda: _fetch_income_statement(ticker,  source_used, period='quarter', limit=20),
            "ratio_q":    lambda: _fetch_ratio(ticker,             source_used, period='quarter', limit=20),
            "balance_y":  lambda: _fetch_balance_sheet(ticker,     source_used, period='year',    limit=_fetch_limit),
            "balance_q":  lambda: _fetch_balance_sheet(ticker,     source_used, period='quarter', limit=20),
            "news":       lambda: c_engine.news(),
        }

        results = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_key = {executor.submit(_safe_fetch, fn): key for key, fn in tasks.items()}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = pd.DataFrame()

        df_price     = results.get("price",      pd.DataFrame())
        df_overview  = results.get("overview",   pd.DataFrame())
        df_income    = results.get("income_y",   pd.DataFrame())
        df_cashflow  = results.get("cashflow_y", pd.DataFrame())
        df_ratio     = results.get("ratio_y",    pd.DataFrame())
        df_income_q  = results.get("income_q",   pd.DataFrame())
        df_ratio_q   = results.get("ratio_q",    pd.DataFrame())
        df_balance   = results.get("balance_y",  pd.DataFrame())
        df_balance_q = results.get("balance_q",  pd.DataFrame())

        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None

        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        for col in ['close', 'open', 'high', 'low']:
            df_price[f'{col}_vnd'] = df_price[col] * 1000

        is_bank = ticker in ['VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB',
                              'VPB', 'HDB', 'SHB', 'EIB', 'LPB', 'OCB', 'TPB',
                              'VIB', 'MSB', 'SSB', 'NAB', 'ABB', 'BVB']
        current_price = float(df_price['close_vnd'].iloc[-1])

        fin5 = build_5y_financial_table(df_income, df_balance, df_ratio, ticker=ticker)

        revenue_series            = normalize_to_billion_vnd(fin5['revenue'])
        equity_series             = normalize_to_billion_vnd(fin5['equity'])
        total_assets_series       = normalize_to_billion_vnd(fin5['total_assets'])
        net_profit_series         = normalize_net_profit_with_anchor(
            fin5['net_profit'], equity_series, fin5['roe'])
        eps_series                = fin5['eps']
        bvps_series               = fin5['bvps']
        roe_series                = fin5['roe']
        roa_series                = fin5['roa']
        pe_series                 = fin5['pe']
        pb_series                 = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']

        def _filter_years(s):
            if s is None or s.empty:
                return s
            return s[s.index.isin(allowed_years)]

        revenue_series        = _filter_years(revenue_series)
        equity_series         = _filter_years(equity_series)
        total_assets_series   = _filter_years(total_assets_series)
        net_profit_series     = _filter_years(net_profit_series)
        eps_series            = _filter_years(eps_series)
        bvps_series           = _filter_years(bvps_series)
        roe_series            = _filter_years(roe_series)
        roa_series            = _filter_years(roa_series)
        pe_series             = _filter_years(pe_series)
        pb_series             = _filter_years(pb_series)
        outstanding_shares_series = _filter_years(outstanding_shares_series)

        if equity_series.empty and not total_assets_series.empty:
            total_liab_series = normalize_to_billion_vnd(find_row_series(
                df_balance,
                ['tổng cộng nợ phải trả', 'tổng nợ phải trả', 'total liabilities'],
                exclude_keywords=['vốn chủ sở hữu']))
            total_liab_series = _filter_years(total_liab_series)
            if not total_liab_series.empty:
                common_years = total_assets_series.index.intersection(total_liab_series.index)
                if len(common_years) > 0:
                    equity_series = (total_assets_series.loc[common_years]
                                     - total_liab_series.loc[common_years])

        if equity_series.empty or total_assets_series.empty:
            cafef_data = fetch_cafef_balance_sheet_5y(ticker)
            if equity_series.empty and isinstance(cafef_data, dict) and not cafef_data.get('equity', pd.Series()).empty:
                equity_series = _filter_years(cafef_data['equity'])
            if total_assets_series.empty and isinstance(cafef_data, dict) and not cafef_data.get('total_assets', pd.Series()).empty:
                total_assets_series = _filter_years(cafef_data['total_assets'])

        _expected_years  = allowed_years
        _years_have = (set(revenue_series.index) | set(net_profit_series.index)
                       | set(equity_series.index) | set(total_assets_series.index))
        _missing_years = sorted(_expected_years - _years_have)
        if _missing_years:
            st.warning(f"⚠️ {ticker}: cả 3 nguồn VCI/KBS/DNSE đều không có dữ liệu năm {_missing_years}.")

        market_cap_series_raw = fin5.get('market_cap', pd.Series(dtype=float))
        market_cap_direct = get_latest(market_cap_series_raw, default=0.0)
        if market_cap_direct > 0 and current_price > 0:
            implied_check = market_cap_direct / current_price
            if not (1_000_000 <= implied_check <= 50_000_000_000):
                market_cap_direct = 0.0

        issue_share = get_latest(outstanding_shares_series, default=0.0)
        if issue_share == 0.0 and not df_overview.empty:
            for col in ['issue_share', 'outstanding_shares', 'listed_volume']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    issue_share = float(df_overview[col].iloc[0])
                    break
        if issue_share == 0.0 and not df_overview.empty and 'charter_capital' in df_overview.columns:
            try:
                issue_share = float(df_overview['charter_capital'].iloc[0]) / 10000
            except Exception:
                pass
        if market_cap_direct > 0 and current_price > 0:
            implied_from_cap = market_cap_direct / current_price
            if issue_share > 0:
                if abs(implied_from_cap - issue_share) / issue_share > 0.20:
                    issue_share = implied_from_cap
            else:
                issue_share = implied_from_cap

        # FIX: nhiều nguồn (VCI/KBS/DNSE) không trả về hàng "outstanding
        # shares" trong báo cáo tỷ lệ theo từng năm → outstanding_shares_series
        # rỗng hoàn toàn → cột "Số CP lưu hành" trống mọi năm trong bảng 5 năm.
        # Fallback: nếu không có chuỗi theo năm nhưng có issue_share (giá trị
        # mới nhất, lấy từ overview/vốn điều lệ), dùng giá trị đó cho MỌI năm
        # trong allowed_years — số CP có thể có sai lệch nhỏ ở các năm cũ nếu
        # DN có phát hành thêm/chia tách, nhưng còn hơn để trống hoàn toàn.
        if outstanding_shares_series.empty and issue_share > 0:
            outstanding_shares_series = pd.Series(
                {y: issue_share for y in sorted(allowed_years)})

        eps_latest  = get_latest(eps_series,  default=0.0)
        bvps_latest = get_latest(bvps_series, default=0.0)
        if bvps_latest == 0.0 and issue_share > 0 and not equity_series.empty:
            bvps_latest = get_latest(equity_series, default=0.0) * 1e9 / issue_share

        def _normalize_pct(s):
            if s is None or s.empty:
                return s
            return s * 100 if abs(s.iloc[-1]) < 1 else s

        roe_series = _normalize_pct(roe_series)
        roa_series = _normalize_pct(roa_series)

        market_cap = market_cap_direct if market_cap_direct > 0 else (
            current_price * issue_share if issue_share > 0 else 0.0)

        clean_metrics = {
            "is_bank":             is_bank,
            "current_price":       current_price,
            "market_cap_billion":  market_cap / 1e9,
            "pe":  (current_price / eps_latest)  if eps_latest  > 0 else 0.0,
            "pb":  (current_price / bvps_latest) if bvps_latest > 0 else 0.0,
            "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
            "source_used":         source_used,
        }

        # ── Multiples mở rộng ────────────────────────────────────────────
        revenue_latest = get_latest(revenue_series, default=0.0) if not revenue_series.empty else 0.0

        cfo_series_for_multiples = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['lưu chuyển tiền thuần từ hoạt động kinh doanh',
             'lưu chuyển tiền thuần từ hđkd',
             'i. lưu chuyển tiền từ hoạt động kinh doanh',
             'lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh',
             'lưu chuyển tiền thuần từ hoạt động sản xuất kinh doanh',
             'tiền thuần từ hoạt động kinh doanh',
             'net cash flow from operating', 'net cash provided by operating',
             'net cash from operating activities',
             'cash flow from operating activities', 'cash flows from operating activities',
             'net cash generated from operating activities',
             'cash flow from operations', 'operating cash flow']))

        # BUG FIX: dùng None thay vì 0.0 để phân biệt "không tìm được" vs "CFO = 0"
        cfo_latest = get_latest(cfo_series_for_multiples, default=None) \
            if not cfo_series_for_multiples.empty else None
        cfo_is_estimated = False

        pretax_series = normalize_to_billion_vnd(find_row_series(
            df_income,
            ['lợi nhuận trước thuế', 'tổng lợi nhuận kế toán trước thuế',
             'lợi nhuận kế toán trước thuế',
             'profit before tax', 'income before tax', 'earnings before tax']))
        interest_series = normalize_to_billion_vnd(find_row_series(
            df_income,
            ['chi phí lãi vay', 'lãi vay đã trả', 'interest expense', 'interest paid']))
        if interest_series.empty:
            interest_series = normalize_to_billion_vnd(find_row_series(
                df_cashflow, ['chi phí lãi vay', 'lãi vay đã trả', 'interest expense', 'interest paid']))
        da_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['khấu hao tài sản cố định', 'khấu hao và phân bổ',
             'khấu hao tscđ và bđsđt', 'khấu hao bất động sản đầu tư',
             'hao mòn tài sản cố định', 'chi phí khấu hao',
             'depreciation and amortization', 'depreciation & amortisation',
             'depreciation of fixed assets', 'amortisation of intangible', 'depreciation']))
        if da_series.empty:
            da_series = normalize_to_billion_vnd(find_row_series(
                df_income,
                ['khấu hao tài sản cố định', 'khấu hao và phân bổ',
                 'depreciation and amortization', 'depreciation']))

        pretax_latest   = get_latest(pretax_series,   default=0.0) if not pretax_series.empty   else 0.0
        interest_latest = get_latest(interest_series, default=0.0) if not interest_series.empty else 0.0
        da_latest       = get_latest(da_series,       default=0.0) if not da_series.empty       else 0.0

        try:
            from financial_normalizer import SECURITIES_TICKERS as _SEC_TICKERS
            is_securities = ticker in _SEC_TICKERS
        except ImportError:
            is_securities = False

        # EBITDA — theo công thức tài liệu: EBITDA = LNTT + lãi vay + khấu hao
        # Ngân hàng: không áp dụng EV/EBITDA (cấu trúc vốn hoàn toàn khác)
        # CTCK: không loại trừ lãi vay như trước — đồng nhất công thức với DN thường
        # BUG FIX: CTCK cũng dùng pretax + interest + da (interest thường gần 0 → không sai)
        if is_bank:
            ebitda_latest  = None   # BUG FIX: None thay vì 0.0 — ngân hàng không áp dụng
            revenue_latest = 0.0
        elif not pretax_series.empty:
            # Mọi DN phi ngân hàng (kể cả CTCK): LNTT + lãi vay + KH
            ebitda_latest = abs(pretax_latest) + abs(interest_latest) + abs(da_latest)
            ebitda_latest = ebitda_latest if ebitda_latest > 0 else None
        elif not net_profit_series.empty:
            # Fallback khi không có LNTT: dùng LNST + KH (proxy thô)
            _np_proxy = get_latest(net_profit_series, default=None)
            ebitda_latest = (abs(_np_proxy) + abs(da_latest)) if _np_proxy is not None else None
        else:
            ebitda_latest = None   # BUG FIX: None thay vì 0.0

        ebitda_is_estimated = (
            not is_bank
            and not is_securities  # CTCK với công thức đầy đủ không cần flag estimated
            and pretax_series.empty
            and (not net_profit_series.empty)
        )

        # BUG FIX: CFO fallback — chỉ kích hoạt khi cfo_latest là None (không tìm được)
        # Không fallback khi CFO tìm được nhưng = 0 (CFO thật bằng 0 là có nghĩa)
        if cfo_latest is None and not net_profit_series.empty:
            _np_proxy = get_latest(net_profit_series, default=None)
            if _np_proxy is not None:
                cfo_latest = abs(_np_proxy) + abs(da_latest)
                cfo_is_estimated = True
        # BUG FIX: CFO âm → không có ý nghĩa kinh tế cho P/CF → đặt None
        if cfo_latest is not None and cfo_latest <= 0:
            cfo_latest = None

        short_debt_series = normalize_to_billion_vnd(find_row_series(
            df_balance, ['vay và nợ thuê tài chính ngắn hạn', 'vay ngắn hạn', 'short-term borrowings']))
        long_debt_series = normalize_to_billion_vnd(find_row_series(
            df_balance, ['vay và nợ thuê tài chính dài hạn', 'vay dài hạn', 'long-term borrowings']))
        cash_series = normalize_to_billion_vnd(find_row_series(
            df_balance, ['tiền và các khoản tương đương tiền', 'tiền và tương đương tiền',
                         'cash and cash equivalents']))

        short_debt_latest = get_latest(short_debt_series, default=0.0) if not short_debt_series.empty else 0.0
        long_debt_latest  = get_latest(long_debt_series,  default=0.0) if not long_debt_series.empty  else 0.0
        cash_latest_val   = get_latest(cash_series,       default=0.0) if not cash_series.empty       else 0.0
        net_debt_latest   = (short_debt_latest + long_debt_latest) - cash_latest_val

        clean_metrics.update({
            "revenue_latest_billion":  revenue_latest,
            "cfo_latest_billion":      cfo_latest,        # None = không có dữ liệu
            "cfo_is_estimated":        cfo_is_estimated,
            "ebitda_latest_billion":   ebitda_latest,     # None = không áp dụng / không có dữ liệu
            "ebitda_is_estimated":     ebitda_is_estimated,
            "net_debt_billion":        net_debt_latest,
            # Ngân hàng: loại trừ EV/EBITDA và P/S (cấu trúc vốn khác biệt hoàn toàn)
            # CTCK: KHÔNG loại trừ — P/CF, P/S, EV/EBITDA vẫn có ý nghĩa tham chiếu
            "excl_extended_multiples": is_bank,
        })

        # ── Bảng 5 năm ───────────────────────────────────────────────────
        # Luôn hiển thị đủ 5 năm (2021-2025) kể cả khi source thiếu data một năm.
        # Nếu chỉ union series rồi intersect allowed_years, năm bị thiếu data
        # hoàn toàn sẽ biến mất khỏi bảng. Fix: dùng allowed_years làm skeleton.
        years_available = sorted(allowed_years)

        eps_series_filled  = eps_series.copy()  if eps_series  is not None else pd.Series(dtype=float)
        bvps_series_filled = bvps_series.copy() if bvps_series is not None else pd.Series(dtype=float)
        roe_series_filled  = roe_series.copy()  if roe_series  is not None else pd.Series(dtype=float)
        roa_series_filled  = roa_series.copy()  if roa_series  is not None else pd.Series(dtype=float)

        for y in years_available:
            has_np = y in net_profit_series.index and pd.notna(net_profit_series.get(y))
            has_eq = y in equity_series.index      and pd.notna(equity_series.get(y))
            has_ta = y in total_assets_series.index and pd.notna(total_assets_series.get(y))

            if (y not in eps_series_filled.index or pd.isna(eps_series_filled.get(y))) \
                    and has_np and issue_share > 0:
                eps_series_filled[y] = net_profit_series[y] * 1e9 / issue_share
            if (y not in bvps_series_filled.index or pd.isna(bvps_series_filled.get(y))) \
                    and has_eq and issue_share > 0:
                bvps_series_filled[y] = equity_series[y] * 1e9 / issue_share
            if (y not in roe_series_filled.index or pd.isna(roe_series_filled.get(y))) \
                    and has_np and has_eq and equity_series[y] != 0:
                roe_series_filled[y] = net_profit_series[y] / equity_series[y] * 100
            if (y not in roa_series_filled.index or pd.isna(roa_series_filled.get(y))) \
                    and has_np and has_ta and total_assets_series[y] != 0:
                roa_series_filled[y] = net_profit_series[y] / total_assets_series[y] * 100

        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)']            = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)']         = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)']    = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)']              = df_5y_table['Năm'].map(eps_series_filled)
        df_5y_table['BVPS (đ)']             = df_5y_table['Năm'].map(bvps_series_filled)
        df_5y_table['ROE (%)'] = df_5y_table['Năm'].map(lambda y: roe_series_filled.get(y, None))
        df_5y_table['ROA (%)'] = df_5y_table['Năm'].map(lambda y: roa_series_filled.get(y, None))
        # Placeholder 4 cột mới — sẽ được điền đầy đủ sau khi tính eps_adj/bvps_adj (Bẫy 5B)
        # Thứ tự cột: giữ đúng thứ tự hiển thị mong muốn
        df_5y_table['LCFD HĐKD (tỷ)']      = None
        df_5y_table['Số CP lưu hành (tỷ)'] = None
        df_5y_table['ROS (%)']              = None
        df_5y_table['Giá cuối năm (đ)']     = None

        revenue_cagr    = cagr(get_latest_n_years(revenue_series,    DEFAULT_YEAR_LIMIT))
        net_profit_cagr = cagr(get_latest_n_years(net_profit_series, DEFAULT_YEAR_LIMIT))

        fundamentals_summary = {
            "revenue_cagr_pct":    revenue_cagr    * 100 if revenue_cagr    is not None else None,
            "net_profit_cagr_pct": net_profit_cagr * 100 if net_profit_cagr is not None else None,
            "eps_latest":          eps_latest,
            "bvps_latest":         bvps_latest,
            "roe_latest":          get_latest(roe_series, default=None),
            "roa_latest":          get_latest(roa_series, default=None),
        }

        # ── Bảng quý ─────────────────────────────────────────────────────
        df_quarter_table = pd.DataFrame()
        try:
            fin_q  = build_financial_table(df_income_q, df_balance_q, df_ratio_q,
                                           ticker=ticker, period='quarter')
            rev_q  = normalize_to_billion_vnd(fin_q['revenue'])
            eq_q   = normalize_to_billion_vnd(fin_q['equity'])
            ta_q   = normalize_to_billion_vnd(fin_q['total_assets'])
            np_q   = normalize_net_profit_with_anchor(fin_q['net_profit'], eq_q, fin_q['roe'])
            eps_q  = fin_q['eps']
            bvps_q = fin_q['bvps']
            roe_q  = _normalize_pct(fin_q['roe'])
            roa_q  = _normalize_pct(fin_q['roa'])
            quarters = sorted(
                set(rev_q.index) | set(np_q.index) | set(eq_q.index) | set(ta_q.index),
                key=lambda c: (int(str(c).split('-Q')[0]), int(str(c).split('-Q')[1])))
            df_quarter_table = pd.DataFrame({'_p': quarters})
            df_quarter_table['Quý'] = df_quarter_table['_p'].apply(
                lambda c: f"Q{str(c).split('-Q')[1]}/{str(c).split('-Q')[0]}")
            df_quarter_table['Doanh thu thuần (tỷ)'] = df_quarter_table['_p'].map(rev_q)
            df_quarter_table['LNST (tỷ)']            = df_quarter_table['_p'].map(np_q)
            df_quarter_table['Vốn CSH (tỷ)']         = df_quarter_table['_p'].map(eq_q)
            df_quarter_table['Tổng tài sản (tỷ)']    = df_quarter_table['_p'].map(ta_q)
            df_quarter_table['EPS (đ)']              = df_quarter_table['_p'].map(eps_q)
            df_quarter_table['BVPS (đ)']             = df_quarter_table['_p'].map(bvps_q)
            df_quarter_table['ROE (%)'] = df_quarter_table['_p'].map(lambda y: roe_q.get(y, None))
            df_quarter_table['ROA (%)'] = df_quarter_table['_p'].map(lambda y: roa_q.get(y, None))
            # ROS quý = LNST / Doanh thu × 100 (cùng đơn vị tỷ — Bẫy 2 an toàn)
            def _ros_q(p):
                r = rev_q.get(p) if p in rev_q.index else None
                n = np_q.get(p)  if p in np_q.index  else None
                if (r is not None and n is not None
                        and pd.notna(r) and pd.notna(n) and r != 0):
                    return n / r * 100
                return None
            df_quarter_table['ROS (%)'] = df_quarter_table['_p'].map(_ros_q)
            # LCFD HĐKD / Số CP / Giá cuối năm không có ý nghĩa theo quý → không thêm
            df_quarter_table = df_quarter_table.drop(columns=['_p'])
        except Exception:
            pass

        # ── DuPont ────────────────────────────────────────────────────────
        df_dupont = dupont_decomposition(
            revenue_series, net_profit_series, total_assets_series, equity_series)

        # ── DCF ───────────────────────────────────────────────────────────
        cfo_series = cfo_series_for_multiples
        capex_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['tiền chi để mua sắm', 'purchase of fixed assets',
             'capital expenditure', 'mua sắm tài sản cố định',
             'mua sắm xây dựng tài sản cố định', 'tiền chi mua sắm tscđ',
             'tiền chi ra để mua sắm, xây dựng tài sản cố định',
             'purchase of property, plant and equipment',
             'purchases of fixed assets']))

        latest_fcff = None
        if not cfo_series.empty:
            cfo_l   = get_latest(cfo_series,   default=None)
            capex_l = get_latest(capex_series, default=0.0) if not capex_series.empty else 0.0
            if cfo_l is not None:
                latest_fcff = (cfo_l - abs(capex_l)) * 1e9

        dcf_results = reverse_g = None
        if latest_fcff and latest_fcff > 0 and issue_share > 0:
            dcf_results = dcf_fcff_scenarios(
                latest_fcff=latest_fcff, shares_outstanding=issue_share,
                net_debt=net_debt_latest * 1e9)
            reverse_g = reverse_dcf_implied_growth(
                current_price=current_price, shares_outstanding=issue_share,
                latest_fcff=latest_fcff, wacc=estimate_wacc(ticker),
                net_debt=net_debt_latest * 1e9)

        graham_value = graham_number(eps_latest, bvps_latest) \
            if eps_latest > 0 and bvps_latest > 0 else None

        dilution_years = detect_stock_dividend_years(outstanding_shares_series)
        eps_adj, bvps_adj = normalize_eps_bvps_series(
            eps_series_filled, bvps_series_filled, outstanding_shares_series)

        # ── BẪY 5B FIX + 4 CỘT MỚI ──────────────────────────────────────
        # Quote.history() trả giá SPLIT-ADJUSTED (toàn bộ lịch sử scale về CP mới nhất).
        # EPS/BVPS ban đầu trong bảng = BCTC gốc (unadjusted).
        # → Phải OVERWRITE EPS/BVPS bằng eps_adj/bvps_adj (cùng base CP mới nhất với giá)
        #   để người dùng không vô tình tính PE/PB sai khi nhìn bảng.
        # Ví dụ BSR: EPS 2021 gốc = 2,166đ (base 3.10 tỷ CP)
        #             EPS 2021 adj = 2,166/1.615 = 1,341đ (base 5.007 tỷ CP, cùng base giá)
        #             Giá 2021 split-adj = 13,210đ
        #             PE đúng = 13,210 / 1,341 = 9.85x (không phải 6.10x)

        # FIX EPS/BVPS: ghi đè bằng split-adjusted (chỉ khi có dilution, tức mult ≠ 1)
        if dilution_years:  # có split/cổ tức CP trong kỳ → adjust cần thiết
            df_5y_table['EPS (đ)']  = df_5y_table['Năm'].map(
                lambda y: eps_adj.get(y, None)  if y in eps_adj.index  else None)
            df_5y_table['BVPS (đ)'] = df_5y_table['Năm'].map(
                lambda y: bvps_adj.get(y, None) if y in bvps_adj.index else None)

        # 1) LCFD HĐKD (CFO) – tỷ VND
        #    cfo_series_for_multiples đã qua normalize_to_billion_vnd → đơn vị tỷ (Bẫy 2: không ×1000)
        _cfo_s = cfo_series_for_multiples if (
            cfo_series_for_multiples is not None and not cfo_series_for_multiples.empty
        ) else pd.Series(dtype=float)
        df_5y_table['LCFD HĐKD (tỷ)'] = df_5y_table['Năm'].map(
            lambda y: _cfo_s.get(y, None) if not _cfo_s.empty else None)

        # 2) Số CP lưu hành – tỷ CP
        #    outstanding_shares_series = cổ phiếu lẻ (raw) → /1e9 = tỷ CP (Bẫy 1: đúng đơn vị)
        #    Dùng outstanding_shares_series (per-year) KHÔNG dùng issue_share (chỉ là latest)
        if outstanding_shares_series is not None and not outstanding_shares_series.empty:
            _shares_ty = outstanding_shares_series / 1e9
            df_5y_table['Số CP lưu hành (tỷ)'] = df_5y_table['Năm'].map(
                lambda y: _shares_ty.get(y, None))
        else:
            df_5y_table['Số CP lưu hành (tỷ)'] = None

        # 3) Biên LNST / ROS (%)
        #    = LNST (tỷ) / Doanh thu thuần (tỷ) × 100
        #    Cùng đơn vị tỷ/tỷ = thuần ratio, không ×1000 (Bẫy 2)
        #    Dùng LNST thuộc CĐ mẹ (net_profit_series đã qua normalize_net_profit_with_anchor — Bẫy 3)
        def _ros_annual(y):
            rev = revenue_series.get(y)    if y in revenue_series.index    else None
            np_ = net_profit_series.get(y) if y in net_profit_series.index else None
            if (rev is not None and np_ is not None
                    and pd.notna(rev) and pd.notna(np_) and rev != 0):
                return np_ / rev * 100
            return None
        df_5y_table['ROS (%)'] = df_5y_table['Năm'].map(_ros_annual)

        # 4) Giá cuối năm (đ) – split-adjusted (từ Quote.history())
        #    Bẫy 5 / 5B: KHÔNG de-adjust giá — giữ nguyên split-adjusted
        #    Lý do: EPS trong bảng cũng đã được adjust về cùng base (bước trên)
        #           → PE người dùng tự tính = Giá adj / EPS adj = ĐÚNG
        #    Bẫy 4: chỉ lấy năm có data thật trong df_price, không suy đoán
        _price_eoy: dict = {}
        if (df_price is not None and not df_price.empty
                and 'time' in df_price.columns and 'close_vnd' in df_price.columns):
            _dp = df_price[['time', 'close_vnd']].copy()
            _dp['_year'] = pd.to_datetime(_dp['time']).dt.year
            for _yr, _grp in _dp.groupby('_year'):
                _last = _grp.sort_values('time')['close_vnd'].iloc[-1]
                if pd.notna(_last) and _last > 0:
                    _price_eoy[int(_yr)] = float(_last)
        df_5y_table['Giá cuối năm (đ)'] = df_5y_table['Năm'].map(
            lambda y: _price_eoy.get(int(y), None))
        # ── KẾT THÚC BẪY 5B FIX + 4 CỘT MỚI ────────────────────────────

        dps_series = fin5.get('dps', pd.Series(dtype=float))
        dps_series = _filter_years(dps_series)
        dps_latest = get_latest(dps_series, default=0.0) if not dps_series.empty else 0.0
        ddm_value, ddm_note = ddm_gordon(dps_series, net_profit_series, ticker=ticker)

        dividend_yield_pct = (dps_latest / current_price * 100) \
            if dps_latest > 0 and current_price > 0 else None
        clean_metrics["dividend_yield_pct"] = dividend_yield_pct
        clean_metrics["dps_latest"]         = dps_latest if dps_latest > 0 else None

        valuation_methods = nine_methods_valuation(
            eps_latest=eps_latest, bvps_latest=bvps_latest,
            pe_series=pe_series, pb_series=pb_series,
            current_price=current_price,
            dcf_results=dcf_results, graham_value=graham_value, ddm_value=ddm_value,
            eps_adj=eps_adj, bvps_adj=bvps_adj,
            shares_series=outstanding_shares_series,
            net_profit_series=net_profit_series,
            dps_series=dps_series, ticker=ticker)

        valuation_summary = summarize_valuation(valuation_methods, current_price) \
            if valuation_methods else None

        valuation_package = {
            "methods":           valuation_methods,
            "summary":           valuation_summary,
            "dcf_scenarios":     dcf_results,
            "reverse_dcf_g_pct": reverse_g * 100 if reverse_g is not None else None,
            "graham_value":      graham_value,
            "ddm_value":         ddm_value,
            "ddm_note":          ddm_note,
            "dilution_years":    dilution_years,
            "pe_series":         pe_series,
            "pb_series":         pb_series,
            "bvps_series":       bvps_series_filled,
            # FIX Bẫy 5B (mixing base): EPS/BVPS đã quy về CÙNG số CP hiện tại
            # với giá (Quote.history() đã split-adjusted) — dùng để tính lại
            # PE/PB lịch sử nhất quán cho tab Định Giá PE/PB, KHÔNG dùng
            # pe_series/pb_series thô (vendor) vốn có thể lệch base qua các
            # năm có phát hành CP thưởng/chia tách.
            "eps_series_adj":    eps_adj,
            "bvps_series_adj":   bvps_adj,
            "price_series": (
                df_price.set_index('time')['close_vnd']
                .resample('YE').last()
                .rename(lambda x: x.year)
                if not df_price.empty and 'time' in df_price.columns
                   and 'close_vnd' in df_price.columns
                else pd.Series(dtype=float)
            ),
        }

        # ── Technical ─────────────────────────────────────────────────────
        if 'volume' not in df_price.columns:
            df_price['volume'] = 0
        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()
        df_price['MA20']        = df_price['close_vnd'].rolling(window=20).mean()
        latest_vol  = float(df_price['volume'].iloc[-1])
        avg_vol_20d = float(df_price['volume_ma20'].iloc[-1]) \
            if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        vol_vs_avg_pct = ((latest_vol / avg_vol_20d - 1) * 100) if avg_vol_20d > 0 else 0.0
        technical_summary = {
            "latest_volume":     latest_vol,
            "avg_volume_20d":    avg_vol_20d,
            "volume_vs_avg_pct": vol_vs_avg_pct,
            "ma20":              df_price['MA20'].iloc[-1],
            "oil_correlation":   0.74 if ticker in ['BSR', 'OIL', 'PLX', 'PVD', 'PVS', 'GAS'] else 0.0,
            "trend_signal":      "KHẢ QUAN (Uptrend)"
                                 if current_price > df_price['MA20'].iloc[-1]
                                 else "RỦI RO (Downtrend)",
        }

        # ── Tin tức ──────────────────────────────────────────────────────
        vnstock_news = []
        news_raw = _safe_fetch(lambda: c_engine.news(), default=pd.DataFrame())
        if news_raw is not None and not news_raw.empty:
            for _, row in news_raw.head(10).iterrows():
                vnstock_news.append({
                    "title":    row.get('news_title',  ''),
                    "source":   row.get('news_source', 'vnstock'),
                    "url":      row.get('news_url',    '#'),
                    "pub_date": "—",
                })
        news_list = fetch_news_with_fallback(ticker, vnstock_news)
        if not news_list:
            news_list = [{
                "title":  "Không có sự kiện bất thường trong 30 ngày.",
                "source": "Hệ thống tự động", "url": "#", "pub_date": "—"}]

        reports_pkg = None

        return (
            df_price, df_5y_table, df_quarter_table, df_balance,
            clean_metrics, technical_summary,
            news_list, fundamentals_summary, df_dupont, valuation_package,
            reports_pkg,
        )

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        return None
