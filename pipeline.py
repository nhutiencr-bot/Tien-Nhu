import pandas as pd
import numpy as np
import streamlit as st

from news_fetcher import fetch_news_google_rss, fetch_news_with_fallback
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
)
from cafef_fallback import (
    fetch_cafef_balance_sheet_5y, fetch_cafef_analysis_reports, fetch_cafef_yearly_full,
)
from split_adjustment import audit_and_adjust_split, recompute_pe_pb_series
from sector_wacc import estimate_wacc, wacc_scenarios, detect_sector

SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']
TASK_TIMEOUT_SECONDS = 10

# ─── TARGET_YEARS: Luôn tính động theo năm hiện tại ────────────────────────
# Chỉ lấy 2021–2025 (bỏ 2018/2019/2020 khỏi bảng hiển thị)
_THIS_YEAR = datetime.today().year
TARGET_YEARS = set(range(2021, _THIS_YEAR))   # {2021, 2022, 2023, 2024, 2025}

# ─── Ngân hàng — mở rộng (không hardcode chỉ 7 mã) ────────────────────────
# Thêm đầy đủ các ngân hàng HOSE + HNX để is_bank detect đúng
BANK_TICKERS = {
    'VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB', 'VPB', 'HDB', 'TPB',
    'MSB', 'OCB', 'VIB', 'SHB', 'EIB', 'LPB', 'SSB', 'NAB', 'ABB', 'BAB',
    'BVB', 'KLB', 'PGB', 'VAB', 'NVB', 'SGB', 'NCB', 'VBB',
}

# ─── Bán lẻ / phân phối — keyword revenue riêng ────────────────────────────
RETAIL_TICKERS = {
    'MWG', 'FRT', 'DGW', 'PNJ', 'HAX', 'SVC', 'MCH', 'PET',
    'PSD', 'HHS', 'HUT', 'AST', 'PTC',
}

# ─── BĐS cho thuê — doanh thu cho thuê ─────────────────────────────────────
REALESTATE_TICKERS = {'VRE', 'NLG', 'DXG', 'KDH', 'PDR', 'CEO', 'BCM'}


def normalize_to_billion_vnd(series):
    """Chuẩn hoá Series về đơn vị tỷ VNĐ."""
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
    """Chuẩn hoá net_profit dùng equity * roe% làm điểm neo để detect đơn vị đúng."""
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


@st.cache_data(ttl=3600, show_spinner=False)
def _resolve_source(ticker):
    """Dò nguồn dữ liệu khả dụng (VCI → KBS → DNSE), cache 1h."""
    last_error = None
    test_end   = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')

    for source in SOURCE_FALLBACK_ORDER:
        try:
            q_engine = Quote(symbol=ticker, source=source)
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError(f"Nguồn {source} trả về dữ liệu rỗng cho {ticker}")
            return source
        except Exception as e:
            last_error = e
            continue

    raise ConnectionError(
        f"Không lấy được dữ liệu cho mã {ticker} từ bất kỳ nguồn nào "
        f"({', '.join(SOURCE_FALLBACK_ORDER)}). Lỗi cuối cùng: {last_error}"
    )


def _build_engines_with_fallback(ticker):
    source_used = _resolve_source(ticker)
    q_engine = Quote(symbol=ticker, source=source_used)
    f_engine = Finance(symbol=ticker, source=source_used, period='year')
    c_engine = Company(symbol=ticker, source=source_used)
    return q_engine, f_engine, c_engine, source_used


def _safe_fetch(fn, default=None):
    try:
        result = fn()
        return result if result is not None else (default if default is not None else pd.DataFrame())
    except Exception:
        return default if default is not None else pd.DataFrame()


def _fetch_balance_sheet(ticker, period='year'):
    for bs_source in SOURCE_FALLBACK_ORDER:
        try:
            f_bs = Finance(symbol=ticker, source=bs_source, period=period)
            df = f_bs.balance_sheet(period=period)
            if df is not None and not df.empty:
                return df
        except Exception:
            continue
    return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
#  TCBS FALLBACK — lấy dữ liệu 2021 bị thiếu từ TCBS public API
# ══════════════════════════════════════════════════════════════════════════════

def _tcbs_fetch_year(ticker: str, year: int) -> dict:
    """
    Gọi TCBS public API lấy income + ratio cho 1 năm cụ thể.
    Trả về dict: {revenue, net_profit, eps, bvps, roe, roa} — giá trị tỷ VNĐ / đ/cp.
    Không raise — trả về {} nếu fail.
    """
    import requests, time as _time
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    base = f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/finance/{ticker}"
    result = {}

    # ── Income statement ────────────────────────────────────────────────────
    try:
        r = requests.get(f"{base}/income-statement?yearly=1&page=0&size=10",
                         headers=headers, timeout=10)
        r.raise_for_status()
        rows = r.json() if isinstance(r.json(), list) else r.json().get('data', [])
        for row in rows:
            if str(row.get('year') or row.get('fiscalYear') or '')[:4] != str(year):
                continue
            # Ngân hàng → netInterestIncome; thường → netRevenue/revenue
            rev = (row.get('netInterestIncome') or row.get('netRevenue')
                   or row.get('revenue') or row.get('salesRevenue'))
            np_ = (row.get('postTaxProfit') or row.get('netProfit') or row.get('netIncome'))
            eps = row.get('eps') or row.get('earningPerShare')
            if rev  is not None: result['revenue']    = round(float(rev),  2)
            if np_  is not None: result['net_profit'] = round(float(np_),  2)
            if eps  is not None: result['eps']        = round(float(eps),  2)
            break
    except Exception:
        pass

    _time.sleep(0.15)

    # ── Financial ratio ─────────────────────────────────────────────────────
    try:
        r = requests.get(f"{base}/financialratio?yearly=1&page=0&size=10",
                         headers=headers, timeout=10)
        r.raise_for_status()
        rows = r.json() if isinstance(r.json(), list) else r.json().get('data', [])
        for row in rows:
            if str(row.get('year') or row.get('fiscalYear') or '')[:4] != str(year):
                continue
            bvps = row.get('bvps')
            roe  = row.get('roe')
            roa  = row.get('roa')
            eps2 = row.get('eps')
            if bvps is not None: result['bvps'] = round(float(bvps), 2)
            if roe  is not None: result['roe']  = round(float(roe),  4)
            if roa  is not None: result['roa']  = round(float(roa),  4)
            if eps2 is not None and 'eps' not in result:
                result['eps'] = round(float(eps2), 2)
            break
    except Exception:
        pass

    return result


def _fill_missing_years(
    ticker: str,
    target_years: set,
    revenue_s: pd.Series,
    net_profit_s: pd.Series,
    eps_s: pd.Series,
    bvps_s: pd.Series,
    roe_s: pd.Series,
    roa_s: pd.Series,
    equity_s: pd.Series,
    total_assets_s: pd.Series,
) -> tuple:
    """
    Với mỗi năm còn thiếu bất kỳ trường nào → gọi TCBS API để bù.
    Sau đó tính ROE/ROA từ LNST / Vốn CSH / Tổng TS nếu vẫn thiếu.

    Trả về: (revenue_s, net_profit_s, eps_s, bvps_s, roe_s, roa_s) đã fill.
    """

    def _missing(s: pd.Series, year: int) -> bool:
        if s is None or s.empty: return True
        val = s.get(year)
        return val is None or (isinstance(val, float) and (pd.isna(val) or val == 0.0))

    def _set(s: pd.Series, year: int, val) -> pd.Series:
        if val is None: return s
        if s is None or (hasattr(s, 'empty') and s.empty):
            return pd.Series({year: val}, dtype=float)
        s = s.copy()
        s[year] = val
        return s.sort_index()

    for year in sorted(target_years):
        needs = any(_missing(s, year) for s in
                    [revenue_s, net_profit_s, eps_s, bvps_s, roe_s, roa_s])
        if not needs:
            continue

        fetched = _tcbs_fetch_year(ticker, year)

        if _missing(revenue_s,    year): revenue_s    = _set(revenue_s,    year, fetched.get('revenue'))
        if _missing(net_profit_s, year): net_profit_s = _set(net_profit_s, year, fetched.get('net_profit'))
        if _missing(eps_s,        year): eps_s        = _set(eps_s,        year, fetched.get('eps'))
        if _missing(bvps_s,       year): bvps_s       = _set(bvps_s,       year, fetched.get('bvps'))
        if _missing(roe_s,        year): roe_s        = _set(roe_s,        year, fetched.get('roe'))
        if _missing(roa_s,        year): roa_s        = _set(roa_s,        year, fetched.get('roa'))

        # Tính ROE/ROA từ dữ liệu hiện có nếu TCBS vẫn thiếu
        np_val  = net_profit_s.get(year)  if not _missing(net_profit_s, year) else None
        eq_val  = equity_s.get(year)      if (equity_s is not None and not equity_s.empty and equity_s.get(year)) else None
        ta_val  = total_assets_s.get(year) if (total_assets_s is not None and not total_assets_s.empty and total_assets_s.get(year)) else None

        if np_val and eq_val and eq_val > 0 and _missing(roe_s, year):
            roe_calc = round(np_val / eq_val * 100, 2)
            roe_s = _set(roe_s, year, roe_calc)

        if np_val and ta_val and ta_val > 0 and _missing(roa_s, year):
            roa_calc = round(np_val / ta_val * 100, 2)
            roa_s = _set(roa_s, year, roa_calc)

    return revenue_s, net_profit_s, eps_s, bvps_s, roe_s, roa_s


def _filter_to_target_years(s: pd.Series, years: set) -> pd.Series:
    """Lọc Series chỉ giữ các năm trong target (2021–2025, bỏ 2018/19/20)."""
    if s is None or s.empty:
        return s
    keep = [y for y in s.index if y in years]
    return s.loc[keep].sort_index() if keep else pd.Series(dtype=float)


@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker):
    try:
        # ── 1. Chọn nguồn dữ liệu ─────────────────────────────────────────
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)

        end_date   = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')

        is_bank       = ticker in BANK_TICKERS
        is_retail     = ticker in RETAIL_TICKERS
        is_realestate = ticker in REALESTATE_TICKERS

        # ── 2. Tất cả API calls chạy SONG SONG ────────────────────────────
        tasks = {
            "price":        lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            "overview":     lambda: c_engine.overview(),
            "income_y":     lambda: f_engine.income_statement(period='year'),
            "cashflow_y":   lambda: f_engine.cash_flow(period='year'),
            "ratio_y":      lambda: f_engine.ratio(period='year'),
            "income_q":     lambda: f_engine.income_statement(period='quarter'),
            "ratio_q":      lambda: f_engine.ratio(period='quarter'),
            "balance_y":    lambda: _fetch_balance_sheet(ticker, period='year'),
            "balance_q":    lambda: _fetch_balance_sheet(ticker, period='quarter'),
            "news_vnstock": lambda: c_engine.news(),
            "news_rss":     lambda: fetch_news_google_rss(ticker),
            "reports":      lambda: fetch_cafef_analysis_reports(ticker),
        }

        results = {}
        with ThreadPoolExecutor(max_workers=11) as executor:
            future_to_key = {
                executor.submit(_safe_fetch, fn): key
                for key, fn in tasks.items()
            }
            for future in as_completed(future_to_key, timeout=TASK_TIMEOUT_SECONDS * 2):
                key = future_to_key[future]
                try:
                    results[key] = future.result(timeout=TASK_TIMEOUT_SECONDS)
                except Exception:
                    if key == "news_rss":
                        results[key] = []
                    elif key == "reports":
                        results[key] = {"reports": [], "is_ticker_specific": False,
                                        "sources_used": ["CafeF"], "debug_log": ["Timeout/lỗi."]}
                    else:
                        results[key] = pd.DataFrame()

        df_price      = results.get("price",        pd.DataFrame())
        df_overview   = results.get("overview",     pd.DataFrame())
        df_income     = results.get("income_y",     pd.DataFrame())
        df_cashflow   = results.get("cashflow_y",   pd.DataFrame())
        df_ratio      = results.get("ratio_y",      pd.DataFrame())
        df_income_q   = results.get("income_q",     pd.DataFrame())
        df_ratio_q    = results.get("ratio_q",      pd.DataFrame())
        df_balance    = results.get("balance_y",    pd.DataFrame())
        df_balance_q  = results.get("balance_q",    pd.DataFrame())
        df_news_raw   = results.get("news_vnstock", pd.DataFrame())
        rss_news_raw  = results.get("news_rss",     [])
        reports_pkg   = results.get("reports",      {
            "reports": [], "is_ticker_specific": False,
            "sources_used": ["CafeF"], "debug_log": []})

        # ── 3. Xử lý giá ──────────────────────────────────────────────────
        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None

        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        for col in ['close', 'open', 'high', 'low']:
            df_price[f'{col}_vnd'] = df_price[col] * 1000

        current_price = float(df_price['close_vnd'].iloc[-1])

        # ── 4. Chuẩn hoá BCTC ─────────────────────────────────────────────
        fin5 = build_5y_financial_table(df_income, df_balance, df_ratio, ticker=ticker)

        revenue_series      = normalize_to_billion_vnd(fin5['revenue'])
        equity_series       = normalize_to_billion_vnd(fin5['equity'])
        total_assets_series = normalize_to_billion_vnd(fin5['total_assets'])
        net_profit_series   = normalize_net_profit_with_anchor(
            fin5['net_profit'], equity_series, fin5['roe'])

        eps_series              = fin5['eps']
        bvps_series             = fin5['bvps']
        roe_series              = fin5['roe']
        roa_series              = fin5['roa']
        pe_series               = fin5['pe']
        pb_series               = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']
        net_margin_series       = fin5['net_margin']
        asset_turnover_series   = fin5['asset_turnover']
        ev_ebitda_series        = fin5.get('ev_ebitda', pd.Series(dtype=float))
        p_cf_series             = fin5.get('p_cf',      pd.Series(dtype=float))
        ps_series               = fin5.get('ps',        pd.Series(dtype=float))
        dps_series              = fin5.get('dps',       pd.Series(dtype=float))

        # ── 4A. Lọc bỏ năm < 2021 (bỏ 2018/2019/2020 khỏi bảng hiển thị) ─
        _all_series_to_filter = {
            'revenue':      revenue_series,
            'net_profit':   net_profit_series,
            'equity':       equity_series,
            'total_assets': total_assets_series,
            'eps':          eps_series,
            'bvps':         bvps_series,
            'roe':          roe_series,
            'roa':          roa_series,
            'pe':           pe_series,
            'pb':           pb_series,
        }
        for _k, _s in _all_series_to_filter.items():
            _all_series_to_filter[_k] = _filter_to_target_years(_s, TARGET_YEARS)

        revenue_series      = _all_series_to_filter['revenue']
        net_profit_series   = _all_series_to_filter['net_profit']
        equity_series       = _all_series_to_filter['equity']
        total_assets_series = _all_series_to_filter['total_assets']
        eps_series          = _all_series_to_filter['eps']
        bvps_series         = _all_series_to_filter['bvps']
        roe_series          = _all_series_to_filter['roe']
        roa_series          = _all_series_to_filter['roa']
        pe_series           = _all_series_to_filter['pe']
        pb_series           = _all_series_to_filter['pb']

        # ── 4B. Split adjustment ───────────────────────────────────────────
        split_audit = audit_and_adjust_split(
            eps_series=eps_series, bvps_series=bvps_series,
            net_profit_series=net_profit_series,
            outstanding_shares_series=outstanding_shares_series,
            company_engine=c_engine)

        if split_audit['split_detected']:
            eps_series  = split_audit['eps_adjusted']
            bvps_series = split_audit['bvps_adjusted']
            if split_audit['shares_adjusted'] is not None:
                outstanding_shares_series = split_audit['shares_adjusted']
            st.warning(f"⚠️ {ticker}: {split_audit['note']}")

            try:
                price_by_year = (
                    df_price.assign(_year=pd.to_datetime(df_price['time']).dt.year)
                    .groupby('_year')['close_vnd'].last())
                pe_series_adj, pb_series_adj = recompute_pe_pb_series(
                    eps_series, bvps_series, price_by_year)
                if not pe_series_adj.empty:
                    pe_series = pe_series_adj
                if not pb_series_adj.empty:
                    pb_series = pb_series_adj
            except Exception:
                pass

        # ── 4C. GapFill từng năm còn thiếu (kể cả năm 2021) ─────────────
        # CORE FIX: _gapfill_series thay thế logic cũ (chỉ fallback khi cả
        # series rỗng). Giờ bù từng năm lẻ còn thiếu trong TARGET_YEARS,
        # bất kể các năm khác đã có hay chưa.
        def _gapfill_series(series: pd.Series, patch: pd.Series, label: str) -> pd.Series:
            if patch is None or patch.empty:
                return series
            missing_years = TARGET_YEARS - set(series.index if series is not None else [])
            patch_for_missing = patch[patch.index.isin(missing_years)]
            if patch_for_missing.empty:
                return series
            merged = pd.concat([series if series is not None else pd.Series(dtype=float),
                                 patch_for_missing]).sort_index()
            merged = merged[~merged.index.duplicated(keep='first')]
            st.info(f"ℹ️ Bù '{label}' năm {sorted(patch_for_missing.index)} "
                    f"cho {ticker} từ CafeF.")
            return merged

        # Fallback equity từ (Tổng TS - Tổng nợ)
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

        # CafeF fallback cho balance sheet
        needs_cafef_balance = (
            equity_series.empty or total_assets_series.empty or
            bool(TARGET_YEARS - set(equity_series.index)) or
            bool(TARGET_YEARS - set(total_assets_series.index))
        )
        if needs_cafef_balance:
            cafef_data = fetch_cafef_balance_sheet_5y(ticker, years=sorted(TARGET_YEARS))
            equity_series       = _gapfill_series(equity_series,       cafef_data['equity'],       "Vốn chủ sở hữu")
            total_assets_series = _gapfill_series(total_assets_series, cafef_data['total_assets'], "Tổng tài sản")

        # CafeF fallback cho revenue + net_profit (không áp cho ngân hàng)
        if not is_bank:
            needs_cafef_rev = (
                revenue_series.empty or bool(TARGET_YEARS - set(revenue_series.index))
            )
            if needs_cafef_rev:
                cafef_yearly = fetch_cafef_yearly_full(ticker, years=sorted(TARGET_YEARS))
                revenue_series    = _gapfill_series(revenue_series,    cafef_yearly['revenue'],    "Doanh thu thuần")
                net_profit_series = _gapfill_series(net_profit_series, cafef_yearly['net_profit'], "LNST")
            elif bool(TARGET_YEARS - set(net_profit_series.index)):
                cafef_yearly = fetch_cafef_yearly_full(ticker, years=sorted(TARGET_YEARS))
                net_profit_series = _gapfill_series(net_profit_series, cafef_yearly['net_profit'], "LNST")

        # ── 4D. FILL NĂNG LƯỢNG: Dùng TCBS API bù toàn bộ các năm còn thiếu ──
        # Đây là bước xử lý triệt để: với MỌI năm trong TARGET_YEARS mà vẫn
        # thiếu bất kỳ trường nào (revenue/LNST/EPS/BVPS/ROE/ROA), gọi TCBS
        # API để lấy về. TCBS phủ gần như toàn bộ mã 3 sàn từ 2020 trở đi.
        # Đặc biệt xử lý đúng ngân hàng: TCBS trả về netInterestIncome thay
        # vì revenue thông thường.
        revenue_series, net_profit_series, eps_series, bvps_series, roe_series, roa_series = \
            _fill_missing_years(
                ticker=ticker,
                target_years=TARGET_YEARS,
                revenue_s=revenue_series,
                net_profit_s=net_profit_series,
                eps_s=eps_series,
                bvps_s=bvps_series,
                roe_s=roe_series,
                roa_s=roa_series,
                equity_s=equity_series,
                total_assets_s=total_assets_series,
            )

        # Cảnh báo nếu vẫn còn thiếu sau 4 tầng fallback
        if equity_series.empty:
            st.warning(f"⚠️ Không dò được 'Vốn chủ sở hữu' cho {ticker}.")
        if total_assets_series.empty:
            st.warning(f"⚠️ Không dò được 'Tổng tài sản' cho {ticker}.")

        # ── 5. Số CP lưu hành ─────────────────────────────────────────────
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

        BVPS_SANE_MIN, BVPS_SANE_MAX = 500.0, 1_000_000.0

        eps_latest  = get_latest(eps_series,  default=0.0)
        bvps_latest = get_latest(bvps_series, default=0.0)
        if bvps_latest == 0.0 and issue_share > 0 and not equity_series.empty:
            bvps_latest = get_latest(equity_series, default=0.0) * 1e9 / issue_share

        bvps_mismatch_pct = None
        if bvps_latest > 0 and issue_share > 0 and not equity_series.empty:
            bvps_recalc = get_latest(equity_series, default=0.0) * 1e9 / issue_share
            if bvps_recalc > 0:
                bvps_mismatch_pct = abs(bvps_latest - bvps_recalc) / bvps_latest * 100
                if bvps_mismatch_pct > 5:
                    if BVPS_SANE_MIN <= bvps_recalc <= BVPS_SANE_MAX:
                        bvps_latest = bvps_recalc
                    else:
                        bvps_mismatch_pct = None

        if bvps_latest > 0 and not (BVPS_SANE_MIN <= bvps_latest <= BVPS_SANE_MAX):
            bvps_latest = 0.0

        def _normalize_pct(s):
            if s is None or s.empty:
                return s
            return s * 100 if abs(s.iloc[-1]) < 1 else s

        roe_series = _normalize_pct(roe_series)
        roa_series = _normalize_pct(roa_series)

        market_cap = market_cap_direct if market_cap_direct > 0 else (
            current_price * issue_share if issue_share > 0 else 0.0)

        pb_raw   = (current_price / bvps_latest) if bvps_latest > 0 else 0.0
        pb_final = pb_raw if 0.0 < pb_raw <= 50.0 else 0.0

        clean_metrics = {
            "is_bank": is_bank,
            "is_retail": is_retail,
            "is_realestate": is_realestate,
            "current_price": current_price,
            "bvps_mismatch_pct": bvps_mismatch_pct,
            "market_cap_billion": market_cap / 1e9,
            "pe": (current_price / eps_latest) if eps_latest > 0 else 0.0,
            "pb": pb_final,
            "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
            "source_used": source_used,
        }

        revenue_latest = get_latest(revenue_series, default=0.0)
        da_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['khấu hao tài sản cố định', 'khấu hao và phân bổ', 'depreciation and amortization']))
        da_latest = get_latest(da_series, default=0.0) if not da_series.empty else 0.0

        # ── 6. Bảng KQKD theo Năm (chỉ 2021–2025) ────────────────────────
        years_available = sorted(
            set(revenue_series.index) | set(net_profit_series.index) |
            set(equity_series.index)  | set(total_assets_series.index))
        # Chỉ giữ năm trong TARGET_YEARS
        years_available = [y for y in years_available if y in TARGET_YEARS]

        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)']            = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)']         = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)']    = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)']              = df_5y_table['Năm'].map(eps_series)
        df_5y_table['BVPS (đ)']             = df_5y_table['Năm'].map(bvps_series)
        df_5y_table['ROE (%)'] = df_5y_table['Năm'].map(lambda y: roe_series.get(y, None))
        df_5y_table['ROA (%)'] = df_5y_table['Năm'].map(lambda y: roa_series.get(y, None))

        revenue_cagr    = cagr(get_latest_n_years(revenue_series,    5))
        net_profit_cagr = cagr(get_latest_n_years(net_profit_series, 5))

        fundamentals_summary = {
            "revenue_cagr_pct":     revenue_cagr    * 100 if revenue_cagr    is not None else None,
            "net_profit_cagr_pct":  net_profit_cagr * 100 if net_profit_cagr is not None else None,
            "eps_latest":   eps_latest,
            "bvps_latest":  bvps_latest,
            "roe_latest":   get_latest(roe_series, default=None),
            "roa_latest":   get_latest(roa_series, default=None),
        }

        # ── 7. Bảng theo Quý ──────────────────────────────────────────────
        df_quarter_table = pd.DataFrame()
        try:
            fin_q = build_financial_table(df_income_q, df_balance_q, df_ratio_q,
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
            df_quarter_table['EPS (đ)']  = df_quarter_table['_p'].map(eps_q)
            df_quarter_table['BVPS (đ)'] = df_quarter_table['_p'].map(bvps_q)
            df_quarter_table['ROE (%)']  = df_quarter_table['_p'].map(lambda y: roe_q.get(y, None))
            df_quarter_table['ROA (%)']  = df_quarter_table['_p'].map(lambda y: roa_q.get(y, None))
            df_quarter_table = df_quarter_table.drop(columns=['_p'])
        except Exception as e:
            st.warning(f"Không dựng được bảng theo Quý: {e}")

        # ── 8. DuPont ──────────────────────────────────────────────────────
        df_dupont = dupont_decomposition(
            revenue_series, net_profit_series, total_assets_series, equity_series)

        # ── 9. DCF / Graham / DDM ──────────────────────────────────────────
        cfo_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['lưu chuyển tiền thuần từ hoạt động kinh doanh',
             'lưu chuyển tiền thuần từ hđkd',
             'i. lưu chuyển tiền từ hoạt động kinh doanh',
             'net cash flow from operating', 'net cash provided by operating',
             'net cash from operating', 'cash flow from operating activities',
             'cash flows from operating activities']))

        capex_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['tiền chi để mua sắm', 'purchase of fixed assets',
             'capital expenditure', 'mua sắm tài sản cố định',
             'mua sắm xây dựng tài sản cố định', 'tiền chi mua sắm tscđ']))

        cfo_latest = get_latest(cfo_series, default=0.0) if not cfo_series.empty else 0.0

        def _first_nonempty(*series_list):
            for s in series_list:
                if s is not None and not s.empty:
                    return s
            return pd.Series(dtype=float)

        pretax_keywords  = ['lợi nhuận trước thuế', 'tổng lợi nhuận kế toán trước thuế',
                            'lợi nhuận trước thuế tndn', 'profit before tax', 'income before tax']
        interest_keywords = ['chi phí lãi vay', 'lãi vay đã trả', 'chi phí lãi vay đã trả',
                              'trong đó: chi phí lãi vay', 'interest expense', 'interest paid']
        da_keywords = ['khấu hao tài sản cố định', 'khấu hao và phân bổ', 'khấu hao tscđ',
                       'khấu hao và hao mòn tài sản cố định', 'hao mòn tài sản cố định bđs',
                       'chi phí khấu hao', 'depreciation and amortization',
                       'depreciation & amortisation', 'depreciation']

        pretax_profit_series    = normalize_to_billion_vnd(_first_nonempty(
            find_row_series(df_income,   pretax_keywords),
            find_row_series(df_cashflow, pretax_keywords)))
        interest_expense_series = normalize_to_billion_vnd(_first_nonempty(
            find_row_series(df_income,   interest_keywords),
            find_row_series(df_cashflow, interest_keywords)))
        da_series_2 = normalize_to_billion_vnd(_first_nonempty(
            find_row_series(df_cashflow, da_keywords),
            find_row_series(df_income,   da_keywords)))

        da_latest_ebitda = da_latest if da_latest else (
            get_latest(da_series_2, default=0.0) if not da_series_2.empty else 0.0)
        pretax_latest  = get_latest(pretax_profit_series,    default=0.0) if not pretax_profit_series.empty    else 0.0
        interest_latest = get_latest(interest_expense_series, default=0.0) if not interest_expense_series.empty else 0.0

        if is_bank:
            ebitda_latest  = 0.0
            revenue_latest = 0.0
        else:
            ebitda_latest = 0.0
            if pretax_latest:
                ebitda_latest = abs(pretax_latest) + abs(interest_latest) + abs(da_latest_ebitda)
            elif da_latest_ebitda and not net_profit_series.empty:
                ebitda_latest = abs(get_latest(net_profit_series, default=0.0)) + abs(da_latest_ebitda)

        short_debt_series = normalize_to_billion_vnd(find_row_series(
            df_balance, ['vay và nợ thuê tài chính ngắn hạn', 'vay ngắn hạn',
                         'short-term borrowings', 'short-term debt']))
        long_debt_series  = normalize_to_billion_vnd(find_row_series(
            df_balance, ['vay và nợ thuê tài chính dài hạn', 'vay dài hạn',
                         'long-term borrowings', 'long-term debt']))
        cash_series       = normalize_to_billion_vnd(find_row_series(
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

        latest_fcff = None
        if not cfo_series.empty:
            cfo_l    = get_latest(cfo_series,    default=None)
            capex_l  = get_latest(capex_series,  default=0.0) if not capex_series.empty else 0.0
            if cfo_l is not None:
                latest_fcff = (cfo_l - abs(capex_l)) * 1e9

        dcf_results = reverse_g = None
        industry_text = ""
        if not df_overview.empty:
            for col in ['industry', 'icb_name3', 'icb_name4', 'company_type']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    industry_text = str(df_overview[col].iloc[0])
                    break

        sector_detected = detect_sector(ticker, industry_text)
        wacc_base       = estimate_wacc(ticker, industry_text)
        dcf_scenarios   = wacc_scenarios(wacc_base)

        if latest_fcff and latest_fcff > 0 and issue_share > 0:
            dcf_results = dcf_fcff_scenarios(
                latest_fcff=latest_fcff, shares_outstanding=issue_share, net_debt=0.0,
                scenarios=dcf_scenarios)
            reverse_g = reverse_dcf_implied_growth(
                current_price=current_price, shares_outstanding=issue_share,
                latest_fcff=latest_fcff, wacc=wacc_base, net_debt=0.0)

        graham_value = graham_number(eps_latest, bvps_latest) \
            if eps_latest > 0 and bvps_latest > 0 else None

        dps_latest        = get_latest(dps_series, default=0.0) if not dps_series.empty else 0.0
        ddm_required_return = wacc_base + 0.01
        ddm_g = dcf_scenarios.get('Cơ sở', {}).get('g', 0.03) if dcf_scenarios else 0.03
        ddm_value = (ddm_gordon(dps_latest, required_return=ddm_required_return, g=ddm_g)
                     if dps_latest > 0 else None)

        valuation_methods = nine_methods_valuation(
            eps_latest=eps_latest, bvps_latest=bvps_latest,
            pe_series=pe_series, pb_series=pb_series,
            current_price=current_price,
            dcf_results=dcf_results, graham_value=graham_value, ddm_value=ddm_value,
            ev_ebitda_series=ev_ebitda_series, ebitda_latest=ebitda_latest,
            net_debt_latest=net_debt_latest,
            p_cf_series=p_cf_series, cfo_latest=cfo_latest,
            ps_series=ps_series, revenue_latest=revenue_latest,
            shares_outstanding=issue_share)

        valuation_summary = summarize_valuation(valuation_methods, current_price) \
            if valuation_methods else None

        valuation_package = {
            "methods":          valuation_methods,
            "summary":          valuation_summary,
            "dcf_scenarios":    dcf_results,
            "reverse_dcf_g_pct": reverse_g * 100 if reverse_g is not None else None,
            "graham_value":     graham_value,
            "ddm_value":        ddm_value,
            "pe_series":        pe_series,
            "pb_series":        pb_series,
            "sector_detected":  sector_detected,
            "wacc_base_pct":    wacc_base * 100,
        }

        # ── 10. Volume + Technical ─────────────────────────────────────────
        if 'volume' not in df_price.columns:
            df_price['volume'] = 0

        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()
        df_price['MA20'] = df_price['close_vnd'].rolling(window=20).mean()
        df_price['MA50'] = df_price['close_vnd'].rolling(window=50).mean()

        delta    = df_price['close_vnd'].diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs       = avg_gain / avg_loss.replace(0, float('nan'))
        df_price['RSI14'] = 100 - (100 / (1 + rs))
        df_price['RSI14'] = df_price['RSI14'].fillna(50.0)

        latest_vol   = float(df_price['volume'].iloc[-1])
        avg_vol_20d  = float(df_price['volume_ma20'].iloc[-1]) \
            if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        vol_vs_avg_pct = ((latest_vol / avg_vol_20d - 1) * 100) if avg_vol_20d > 0 else 0.0
        rsi_latest   = float(df_price['RSI14'].iloc[-1]) \
            if not pd.isna(df_price['RSI14'].iloc[-1]) else 50.0
        ma50_latest  = df_price['MA50'].iloc[-1]

        rsi_signal = ("🔴 QUÁ MUA (Overbought)" if rsi_latest >= 70 else
                      "🟢 QUÁ BÁN (Oversold)"   if rsi_latest <= 30 else
                      "⚖️ TRUNG TÍNH")

        technical_summary = {
            "latest_volume":     latest_vol,
            "avg_volume_20d":    avg_vol_20d,
            "volume_vs_avg_pct": vol_vs_avg_pct,
            "ma20":              df_price['MA20'].iloc[-1],
            "ma50":              ma50_latest,
            "rsi14":             rsi_latest,
            "rsi_signal":        rsi_signal,
            "oil_correlation":   0.74 if ticker in ['BSR', 'OIL', 'PLX', 'PVD', 'PVS', 'GAS'] else 0.0,
            "trend_signal":      ("KHẢ QUAN (Uptrend)" if current_price > df_price['MA20'].iloc[-1]
                                  else "RỦI RO (Downtrend)"),
        }

        # ── 11. Tin tức ────────────────────────────────────────────────────
        vnstock_news = []
        if df_news_raw is not None and not df_news_raw.empty:
            for _, row in df_news_raw.head(10).iterrows():
                vnstock_news.append({
                    "title":    row.get('news_title',  ''),
                    "source":   row.get('news_source', 'vnstock'),
                    "url":      row.get('news_url',    '#'),
                    "pub_date": "—",
                })

        news_list = fetch_news_with_fallback(ticker, vnstock_news, rss_news=rss_news_raw)

        return (
            df_price, df_5y_table, df_quarter_table, df_balance,
            clean_metrics, technical_summary,
            news_list, fundamentals_summary, df_dupont, valuation_package,
            reports_pkg,
        )

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        return None
