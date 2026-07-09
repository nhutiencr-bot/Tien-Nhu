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

# Thử KBS trước (hoạt động ổn định qua mọi lần test) — VCI hay fail/timeout,
# đưa lên đầu gây chờ vô ích trước khi fallback. Đây là fix tốc độ chính.
SOURCE_FALLBACK_ORDER = ['KBS', 'VCI', 'DNSE']
DEFAULT_YEAR_LIMIT = 6   # truyền limit=6 vào Finance → vnai (tier 4 kỳ) vẫn trả 5 kỳ thật
# Giải thích: vnai community = max 4 kỳ, nhưng khi limit > 4 thì trả toàn bộ data có sẵn
# (thường là 5 năm 2021-2025). Đặt 6 để chắc chắn không bị cắt mất 2021.


def normalize_to_billion_vnd(series):
    if series is None or series.empty:
        return series
    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            val = float(val)
            if abs(val) > 1e11:
                return round(val / 1e9, 2)
            return round(val, 2)
        except Exception:
            return None
    return series.map(_to_ty).dropna()


def normalize_net_profit_with_anchor(net_profit_raw, equity_series, roe_series):
    base = normalize_to_billion_vnd(net_profit_raw)
    if base is None or base.empty:
        return base
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
    """
    Ghép nhiều DataFrame BCTC (từ VCI/KBS/DNSE) thành 1 bảng duy nhất, LẤY
    HỢP các cột năm từ tất cả nguồn — thay vì chỉ dùng nguồn đầu tiên trả
    về non-empty (cách cũ khiến nếu nguồn đó thiếu 1 năm, VD 2021, thì
    năm đó mất luôn dù nguồn khác có sẵn).

    Ghép theo cột 'item' (đã chuẩn hoá lowercase/strip) — các dòng chỉ tiêu
    cùng tên ở các nguồn khác nhau sẽ được nối thêm cột năm còn thiếu.
    Ưu tiên giữ nguyên dữ liệu của DataFrame đầu tiên (nhiều cột năm nhất)
    khi trùng năm.
    """
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
    # FIX: gọi CẢ 3 NGUỒN (VCI/KBS/DNSE) rồi GHÉP cột năm lại — 3 nguồn
    # backup lẫn nhau cho từng năm cụ thể, thay vì dừng ở nguồn đầu tiên
    # non-empty (cách cũ khiến thiếu năm nếu đúng nguồn đó thiếu, dù nguồn
    # khác có sẵn năm đó).
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

        end_date   = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')

        tasks = {
            "price":      lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            "overview":   lambda: c_engine.overview(),
            "income_y":   lambda: _fetch_income_statement(ticker, source_used, period='year',    limit=DEFAULT_YEAR_LIMIT),
            "cashflow_y": lambda: _fetch_cashflow(ticker,          source_used, period='year',    limit=DEFAULT_YEAR_LIMIT),
            "ratio_y":    lambda: _fetch_ratio(ticker,             source_used, period='year',    limit=DEFAULT_YEAR_LIMIT),
            "income_q":   lambda: _fetch_income_statement(ticker,  source_used, period='quarter', limit=20),
            "ratio_q":    lambda: _fetch_ratio(ticker,             source_used, period='quarter', limit=20),
            "balance_y":  lambda: _fetch_balance_sheet(ticker,     source_used, period='year',    limit=DEFAULT_YEAR_LIMIT),
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

        revenue_series          = normalize_to_billion_vnd(fin5['revenue'])
        equity_series           = normalize_to_billion_vnd(fin5['equity'])
        total_assets_series     = normalize_to_billion_vnd(fin5['total_assets'])
        net_profit_series       = normalize_net_profit_with_anchor(
            fin5['net_profit'], equity_series, fin5['roe'])
        eps_series              = fin5['eps']
        bvps_series             = fin5['bvps']
        roe_series               = fin5['roe']
        roa_series               = fin5['roa']
        pe_series                = fin5['pe']
        pb_series                = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']

        if equity_series.empty and not total_assets_series.empty:
            total_liab_series = normalize_to_billion_vnd(find_row_series(
                df_balance,
                ['tổng cộng nợ phải trả', 'tổng nợ phải trả', 'total liabilities'],
                exclude_keywords=['vốn chủ sở hữu']))
            if not total_liab_series.empty:
                common_years = total_assets_series.index.intersection(total_liab_series.index)
                if len(common_years) > 0:
                    equity_series = (total_assets_series.loc[common_years]
                                     - total_liab_series.loc[common_years])

        if equity_series.empty or total_assets_series.empty:
            cafef_data = fetch_cafef_balance_sheet_5y(ticker, end_year=datetime.today().year)
            if equity_series.empty and not cafef_data['equity'].empty:
                equity_series = cafef_data['equity']
            if total_assets_series.empty and not cafef_data['total_assets'].empty:
                total_assets_series = cafef_data['total_assets']

        # ── Kiểm tra còn thiếu năm nào không (chỉ để cảnh báo — KHÔNG còn
        # dùng CafeF để bù nữa) ─────────────────────────────────────────────
        # Trước đây dùng fetch_cafef_yearly_full() để bù năm thiếu, nhưng
        # việc cào CafeF không ổn định (đã xác nhận sai với nhiều loại mã
        # khác nhau: ngân hàng, CTCK, vận tải...). Thay vào đó, 3 nguồn
        # vnstock (VCI/KBS/DNSE) giờ đã tự backup lẫn nhau NGAY Ở TẦNG FETCH
        # (xem _fetch_income_statement/_fetch_ratio/_fetch_cashflow/
        # _fetch_balance_sheet — gọi cả 3 nguồn rồi merge cột năm), nên
        # revenue_series/net_profit_series/equity_series/total_assets_series
        # ở đây ĐÃ bao gồm dữ liệu ghép từ cả 3 nguồn, không cần bù thêm.
        _current_year_check = datetime.today().year
        _expected_years = set(range(_current_year_check - DEFAULT_YEAR_LIMIT, _current_year_check))
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

        # ── Multiples mở rộng ─────────────────────────────────────────────
        revenue_latest = get_latest(revenue_series, default=0.0) if not revenue_series.empty else 0.0

        cfo_series_for_multiples = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['lưu chuyển tiền thuần từ hoạt động kinh doanh',
             'lưu chuyển tiền thuần từ hđkd',
             'i. lưu chuyển tiền từ hoạt động kinh doanh',
             'net cash flow from operating', 'net cash provided by operating',
             'net cash from operating activities',
             'cash flow from operating activities', 'cash flows from operating activities',
             'net cash generated from operating activities']))
        cfo_latest = get_latest(cfo_series_for_multiples, default=0.0) if not cfo_series_for_multiples.empty else 0.0

        pretax_series = normalize_to_billion_vnd(find_row_series(
            df_income,
            ['lợi nhuận trước thuế', 'tổng lợi nhuận kế toán trước thuế',
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
             'depreciation and amortization', 'depreciation & amortisation', 'depreciation']))
        if da_series.empty:
            da_series = normalize_to_billion_vnd(find_row_series(
                df_income,
                ['khấu hao tài sản cố định', 'khấu hao và phân bổ',
                 'depreciation and amortization', 'depreciation']))

        pretax_latest   = get_latest(pretax_series,   default=0.0) if not pretax_series.empty   else 0.0
        interest_latest = get_latest(interest_series, default=0.0) if not interest_series.empty else 0.0
        da_latest       = get_latest(da_series,       default=0.0) if not da_series.empty       else 0.0

        if is_bank:
            ebitda_latest  = 0.0
            revenue_latest = 0.0
        elif pretax_latest:
            ebitda_latest = abs(pretax_latest) + abs(interest_latest) + abs(da_latest)
        elif da_latest and not net_profit_series.empty:
            ebitda_latest = abs(get_latest(net_profit_series, default=0.0)) + abs(da_latest)
        else:
            ebitda_latest = 0.0

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
            "cfo_latest_billion":      cfo_latest,
            "ebitda_latest_billion":   ebitda_latest,
            "net_debt_billion":        net_debt_latest,
            "excl_extended_multiples": is_bank,
        })

        # ── Bảng 5 năm ───────────────────────────────────────────────────
        current_year_for_table = datetime.today().year
        allowed_years = set(range(current_year_for_table - DEFAULT_YEAR_LIMIT, current_year_for_table))

        years_available = sorted(
            (set(revenue_series.index)      |
             set(net_profit_series.index)   |
             set(equity_series.index)       |
             set(total_assets_series.index) |
             set(eps_series.index)          |
             set(bvps_series.index)         |
             set(roe_series.index)          |
             set(roa_series.index))
            & allowed_years
        )

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

        # net_debt_latest (tính ở khối Multiples phía trên) dùng lại cho DCF
        # để EV chính xác hơn thay vì giả định net_debt = 0.
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

        # ── Phát hiện pha loãng (cổ tức CP) + điều chỉnh EPS/BVPS lịch sử ──
        dilution_years = detect_stock_dividend_years(outstanding_shares_series)
        eps_adj, bvps_adj = normalize_eps_bvps_series(
            eps_series_filled, bvps_series_filled, outstanding_shares_series)

        # ── DDM (Gordon Growth) — chữ ký ĐÚNG theo valuation.py hiện tại:
        # nhận cả series, tự kiểm tra payout ratio, trả về tuple (giá, lý_do).
        # ⚠️ Đây là fix quan trọng nhất — bản cũ gọi ddm_gordon(dps_latest,
        # required_return=..., g=...) SAI chữ ký, gây crash TOÀN BỘ pipeline
        # (not just DDM) cho mọi mã chưa có cache.
        dps_series = fin5.get('dps', pd.Series(dtype=float))
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
