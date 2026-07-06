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
)
from cafef_fallback import fetch_cafef_balance_sheet_5y

SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']

# Năm mục tiêu: 2021–2025
_THIS_YEAR   = datetime.today().year
TARGET_YEARS = set(range(2021, _THIS_YEAR))   # {2021,2022,2023,2024,2025}


# ─────────────────────────────────────────────────────────────────────────────
# FIX 1: normalize_to_billion_vnd — threshold đúng để không bỏ sót tỷ lẻ
# ─────────────────────────────────────────────────────────────────────────────
def normalize_to_billion_vnd(series):
    """
    Chuẩn hoá Series về đơn vị tỷ VNĐ.

    FIX: threshold cũ (> 1e11) bỏ sót nhiều trường hợp:
      - vnstock VCI trả về đồng (VD: 53_246_478_000_000 = 53,246 tỷ) → > 1e12 mới đúng
      - KBS/DNSE đôi khi trả về triệu (VD: 53_246_478 = 53,246 tỷ) → > 1e8
    Dùng heuristic dựa trên ORDER OF MAGNITUDE của median để detect đơn vị.
    """
    if series is None or series.empty:
        return series

    clean = pd.to_numeric(series, errors='coerce').dropna()
    if clean.empty:
        return series

    median_abs = clean.abs().median()

    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            val = float(val)
            if median_abs > 5e11:          # đang ở đồng (VD: 53e12 đồng = 53k tỷ)
                return round(val / 1e9, 2)
            if median_abs > 5e8:           # đang ở triệu (VD: 53e9 triệu = 53k tỷ)
                return round(val / 1e3, 2)
            return round(val, 2)           # đã ở tỷ
        except Exception:
            return None

    return series.map(_to_ty).dropna()


def normalize_net_profit_with_anchor(net_profit_raw, equity_series, roe_series):
    """Chuẩn hoá net_profit dùng equity * roe% làm điểm neo."""
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
        power    = round(np.log10(ratio))
        divisor  = 10 ** power
        fixed[year] = round(raw_val / divisor, 2)

    return pd.Series(fixed)


# ─────────────────────────────────────────────────────────────────────────────
# TCBS fallback — lấy năm 2021 bị thiếu
# ─────────────────────────────────────────────────────────────────────────────
def _tcbs_fetch_year(ticker: str, year: int) -> dict:
    """Gọi TCBS public API lấy income + ratio cho 1 năm. Không raise."""
    import requests, time as _time
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    base    = f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/finance/{ticker}"
    result  = {}

    for endpoint, fields in [
        ('income-statement?yearly=1&page=0&size=10', [
            ('revenue',    ['netInterestIncome','netRevenue','revenue','salesRevenue']),
            ('net_profit', ['postTaxProfit','netProfit','netIncome']),
            ('eps',        ['eps','earningPerShare']),
        ]),
        ('financialratio?yearly=1&page=0&size=10', [
            ('bvps', ['bvps']),
            ('roe',  ['roe']),
            ('roa',  ['roa']),
        ]),
    ]:
        try:
            r = requests.get(f"{base}/{endpoint}", headers=headers, timeout=10)
            r.raise_for_status()
            rows = r.json() if isinstance(r.json(), list) else r.json().get('data', [])
            for row in rows:
                if str(row.get('year') or row.get('fiscalYear') or '')[:4] != str(year):
                    continue
                for field, keys in fields:
                    if field not in result:
                        for k in keys:
                            if row.get(k) is not None:
                                try:
                                    result[field] = round(float(row[k]), 4)
                                except Exception:
                                    pass
                                break
                break
        except Exception:
            pass
        _time.sleep(0.15)

    return result


def _gapfill_from_tcbs(
    ticker, equity_s, total_assets_s,
    revenue_s, net_profit_s, eps_s, bvps_s, roe_s, roa_s,
):
    """
    FIX 2: Bù từng năm lẻ còn thiếu trong TARGET_YEARS (không chỉ khi toàn bộ rỗng).
    Với mỗi năm thiếu → gọi TCBS → tính thêm ROE/ROA từ LNST/VCSH/TS nếu vẫn thiếu.
    """
    def _missing(s, year):
        if s is None or s.empty: return True
        val = s.get(year)
        return val is None or (isinstance(val, float) and (pd.isna(val) or val == 0.0))

    def _set(s, year, val):
        if val is None: return s
        if s is None or (hasattr(s,'empty') and s.empty):
            return pd.Series({year: val}, dtype=float)
        s = s.copy(); s[year] = val
        return s.sort_index()

    for year in sorted(TARGET_YEARS):
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

        # Tính ROE/ROA từ dữ liệu hiện có nếu vẫn thiếu
        np_val = net_profit_s.get(year) if not _missing(net_profit_s, year) else None
        eq_val = equity_s.get(year)      if (equity_s is not None and not equity_s.empty) else None
        ta_val = total_assets_s.get(year) if (total_assets_s is not None and not total_assets_s.empty) else None

        if np_val and eq_val and eq_val > 0 and _missing(roe_s, year):
            roe_s = _set(roe_s, year, round(np_val / eq_val * 100, 2))
        if np_val and ta_val and ta_val > 0 and _missing(roa_s, year):
            roa_s = _set(roa_s, year, round(np_val / ta_val * 100, 2))

    return revenue_s, net_profit_s, eps_s, bvps_s, roe_s, roa_s


def _gapfill_balance(ticker, equity_s, total_assets_s):
    """Bù Vốn CSH + Tổng TS từng năm thiếu qua CafeF."""
    def _missing(s, year):
        if s is None or s.empty: return True
        val = s.get(year)
        return val is None or (isinstance(val, float) and pd.isna(val))

    missing_eq = [y for y in TARGET_YEARS if _missing(equity_s, y)]
    missing_ta = [y for y in TARGET_YEARS if _missing(total_assets_s, y)]

    if not missing_eq and not missing_ta:
        return equity_s, total_assets_s

    cafef = fetch_cafef_balance_sheet_5y(ticker, end_year=_THIS_YEAR)
    cafef_eq = normalize_to_billion_vnd(cafef.get('equity', pd.Series(dtype=float)))
    cafef_ta = normalize_to_billion_vnd(cafef.get('total_assets', pd.Series(dtype=float)))

    for year in missing_eq:
        if cafef_eq is not None and year in cafef_eq.index:
            if equity_s is None or equity_s.empty:
                equity_s = pd.Series({year: cafef_eq[year]}, dtype=float)
            else:
                equity_s = equity_s.copy()
                equity_s[year] = cafef_eq[year]

    for year in missing_ta:
        if cafef_ta is not None and year in cafef_ta.index:
            if total_assets_s is None or total_assets_s.empty:
                total_assets_s = pd.Series({year: cafef_ta[year]}, dtype=float)
            else:
                total_assets_s = total_assets_s.copy()
                total_assets_s[year] = cafef_ta[year]

    if equity_s is not None and not equity_s.empty:
        equity_s = equity_s.sort_index()
    if total_assets_s is not None and not total_assets_s.empty:
        total_assets_s = total_assets_s.sort_index()

    return equity_s, total_assets_s


def _filter_years(s: pd.Series) -> pd.Series:
    """Chỉ giữ 2021–2025, bỏ 2018/2019/2020."""
    if s is None or s.empty: return s
    keep = [y for y in s.index if y in TARGET_YEARS]
    return s.loc[keep].sort_index() if keep else pd.Series(dtype=float)


def _build_engines_with_fallback(ticker):
    last_error = None
    test_end   = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')

    for source in SOURCE_FALLBACK_ORDER:
        try:
            q_engine = Quote(symbol=ticker, source=source)
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError(f"Nguồn {source} trả về rỗng cho {ticker}")
            f_engine = Finance(symbol=ticker, source=source, period='year')
            c_engine = Company(symbol=ticker, source=source)
            return q_engine, f_engine, c_engine, source
        except Exception as e:
            last_error = e
            continue

    raise ConnectionError(
        f"Không lấy được dữ liệu cho {ticker}. Lỗi: {last_error}"
    )


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
            df   = f_bs.balance_sheet(period=period)
            if df is not None and not df.empty:
                return df
        except Exception:
            continue
    return pd.DataFrame()


@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker):
    try:
        # ── 1. Chọn nguồn ─────────────────────────────────────────────────
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)

        end_date   = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')

        BANK_TICKERS = {
            'VCB','BID','CTG','TCB','MBB','ACB','STB','VPB','HDB','TPB',
            'MSB','OCB','VIB','SHB','EIB','LPB','SSB','NAB','ABB','BAB',
            'BVB','KLB','PGB','VAB','NVB','SGB','NCB',
        }
        is_bank = ticker in BANK_TICKERS

        # ── 2. Fetch song song ─────────────────────────────────────────────
        tasks = {
            "price":      lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            "overview":   lambda: c_engine.overview(),
            "income_y":   lambda: f_engine.income_statement(period='year'),
            "cashflow_y": lambda: f_engine.cash_flow(period='year'),
            "ratio_y":    lambda: f_engine.ratio(period='year'),
            "income_q":   lambda: f_engine.income_statement(period='quarter'),
            "ratio_q":    lambda: f_engine.ratio(period='quarter'),
            "balance_y":  lambda: _fetch_balance_sheet(ticker, period='year'),
            "balance_q":  lambda: _fetch_balance_sheet(ticker, period='quarter'),
            "news":       lambda: c_engine.news(),
        }

        results = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_key = {executor.submit(_safe_fetch, fn): key
                             for key, fn in tasks.items()}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = pd.DataFrame()

        df_price      = results.get("price",      pd.DataFrame())
        df_overview   = results.get("overview",   pd.DataFrame())
        df_income     = results.get("income_y",   pd.DataFrame())
        df_cashflow   = results.get("cashflow_y", pd.DataFrame())
        df_ratio      = results.get("ratio_y",    pd.DataFrame())
        df_income_q   = results.get("income_q",   pd.DataFrame())
        df_ratio_q    = results.get("ratio_q",    pd.DataFrame())
        df_balance    = results.get("balance_y",  pd.DataFrame())
        df_balance_q  = results.get("balance_q",  pd.DataFrame())
        df_news_raw   = results.get("news",        pd.DataFrame())

        # ── 3. Giá ────────────────────────────────────────────────────────
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

        # ── 4A. Lọc bỏ năm < 2021 ─────────────────────────────────────────
        revenue_series      = _filter_years(revenue_series)
        net_profit_series   = _filter_years(net_profit_series)
        equity_series       = _filter_years(equity_series)
        total_assets_series = _filter_years(total_assets_series)
        eps_series          = _filter_years(eps_series)
        bvps_series         = _filter_years(bvps_series)
        roe_series          = _filter_years(roe_series)
        roa_series          = _filter_years(roa_series)

        # ── 4B. Fallback equity từ (TS - Nợ) ──────────────────────────────
        if equity_series.empty and not total_assets_series.empty:
            total_liab = normalize_to_billion_vnd(find_row_series(
                df_balance,
                ['tổng cộng nợ phải trả', 'tổng nợ phải trả', 'total liabilities'],
                exclude_keywords=['vốn chủ sở hữu']))
            if total_liab is not None and not total_liab.empty:
                common = total_assets_series.index.intersection(total_liab.index)
                if len(common) > 0:
                    equity_series = (total_assets_series.loc[common]
                                     - total_liab.loc[common])

        # ── 4C. Bù balance sheet từng năm lẻ thiếu (CafeF) ───────────────
        equity_series, total_assets_series = _gapfill_balance(
            ticker, equity_series, total_assets_series)

        # ── 4D. Bù income/ratio từng năm lẻ thiếu (TCBS) — kể cả 2021 ────
        (revenue_series, net_profit_series,
         eps_series, bvps_series,
         roe_series, roa_series) = _gapfill_from_tcbs(
            ticker,
            equity_series, total_assets_series,
            revenue_series, net_profit_series,
            eps_series, bvps_series, roe_series, roa_series,
        )

        if equity_series.empty:
            st.warning(f"⚠️ Không dò được 'Vốn chủ sở hữu' cho {ticker}.")
        if total_assets_series.empty:
            st.warning(f"⚠️ Không dò được 'Tổng tài sản' cho {ticker}.")

        # ── 5. Số CP lưu hành ─────────────────────────────────────────────
        market_cap_series_raw = fin5.get('market_cap', pd.Series(dtype=float))
        market_cap_direct     = get_latest(market_cap_series_raw, default=0.0)
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
            if s is None or s.empty: return s
            return s * 100 if abs(s.iloc[-1]) < 1 else s

        roe_series = _normalize_pct(roe_series)
        roa_series = _normalize_pct(roa_series)

        market_cap = market_cap_direct if market_cap_direct > 0 else (
            current_price * issue_share if issue_share > 0 else 0.0)

        pb_raw   = (current_price / bvps_latest) if bvps_latest > 0 else 0.0
        pb_final = pb_raw if 0.0 < pb_raw <= 50.0 else 0.0

        # Extended multiples data
        cfo_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['lưu chuyển tiền thuần từ hoạt động kinh doanh',
             'lưu chuyển tiền thuần từ hđkd',
             'net cash flow from operating', 'cash flow from operating activities',
             'cash flows from operating activities']))

        capex_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['tiền chi để mua sắm', 'purchase of fixed assets',
             'capital expenditure', 'mua sắm tài sản cố định']))

        da_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['khấu hao tài sản cố định', 'khấu hao và phân bổ',
             'depreciation and amortization', 'depreciation']))

        pretax_series = normalize_to_billion_vnd(find_row_series(
            df_income,
            ['lợi nhuận trước thuế', 'tổng lợi nhuận kế toán trước thuế',
             'profit before tax', 'income before tax']))

        interest_series = normalize_to_billion_vnd(find_row_series(
            df_income,
            ['chi phí lãi vay', 'interest expense', 'interest paid']))

        cfo_latest      = get_latest(cfo_series,      default=0.0) if not cfo_series.empty      else 0.0
        da_latest       = get_latest(da_series,        default=0.0) if not da_series.empty        else 0.0
        pretax_latest   = get_latest(pretax_series,    default=0.0) if not pretax_series.empty    else 0.0
        interest_latest = get_latest(interest_series,  default=0.0) if not interest_series.empty  else 0.0
        revenue_latest  = get_latest(revenue_series,   default=0.0)

        ebitda_latest = 0.0
        if not is_bank:
            if pretax_latest:
                ebitda_latest = abs(pretax_latest) + abs(interest_latest) + abs(da_latest)
            elif da_latest and not net_profit_series.empty:
                ebitda_latest = abs(get_latest(net_profit_series, default=0.0)) + abs(da_latest)
            else:
                ebitda_latest = 0.0

        short_debt = normalize_to_billion_vnd(find_row_series(
            df_balance, ['vay và nợ thuê tài chính ngắn hạn', 'vay ngắn hạn', 'short-term borrowings']))
        long_debt  = normalize_to_billion_vnd(find_row_series(
            df_balance, ['vay và nợ thuê tài chính dài hạn', 'vay dài hạn', 'long-term borrowings']))
        cash_s     = normalize_to_billion_vnd(find_row_series(
            df_balance, ['tiền và các khoản tương đương tiền', 'cash and cash equivalents']))

        net_debt_latest = (
            (get_latest(short_debt, default=0.0) if short_debt is not None and not short_debt.empty else 0.0)
            + (get_latest(long_debt, default=0.0) if long_debt is not None and not long_debt.empty else 0.0)
            - (get_latest(cash_s,   default=0.0) if cash_s is not None and not cash_s.empty else 0.0)
        )

        clean_metrics = {
            "is_bank":               is_bank,
            "current_price":         current_price,
            "market_cap_billion":    market_cap / 1e9,
            "pe":  (current_price / eps_latest)  if eps_latest  > 0 else 0.0,
            "pb":  pb_final,
            "issue_share_million":   issue_share / 1e6 if issue_share > 0 else 0,
            "source_used":           source_used,
            "revenue_latest_billion":  revenue_latest,
            "cfo_latest_billion":      cfo_latest,
            "ebitda_latest_billion":   ebitda_latest,
            "net_debt_billion":        net_debt_latest,
            "excl_extended_multiples": is_bank,
        }

        # ── 6. Bảng năm (chỉ 2021–2025) ───────────────────────────────────
        years_available = sorted(
            (set(revenue_series.index) | set(net_profit_series.index) |
             set(equity_series.index)  | set(total_assets_series.index))
            & TARGET_YEARS
        )

        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)']            = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)']         = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)']    = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)']  = df_5y_table['Năm'].map(eps_series)
        df_5y_table['BVPS (đ)'] = df_5y_table['Năm'].map(bvps_series)
        df_5y_table['ROE (%)']  = df_5y_table['Năm'].map(lambda y: roe_series.get(y, None))
        df_5y_table['ROA (%)']  = df_5y_table['Năm'].map(lambda y: roa_series.get(y, None))

        revenue_cagr    = cagr(get_latest_n_years(revenue_series,    5))
        net_profit_cagr = cagr(get_latest_n_years(net_profit_series, 5))

        fundamentals_summary = {
            "revenue_cagr_pct":    revenue_cagr    * 100 if revenue_cagr    is not None else None,
            "net_profit_cagr_pct": net_profit_cagr * 100 if net_profit_cagr is not None else None,
            "eps_latest":   eps_latest,
            "bvps_latest":  bvps_latest,
            "roe_latest":   get_latest(roe_series, default=None),
            "roa_latest":   get_latest(roa_series, default=None),
        }

        # ── 7. Bảng quý ───────────────────────────────────────────────────
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

        # ── 9. DCF / Graham ────────────────────────────────────────────────
        latest_fcff = None
        if not cfo_series.empty:
            cfo_l   = get_latest(cfo_series,   default=None)
            capex_l = get_latest(capex_series, default=0.0) if not capex_series.empty else 0.0
            if cfo_l is not None:
                latest_fcff = (cfo_l - abs(capex_l)) * 1e9

        from valuation import estimate_wacc, wacc_scenarios, detect_sector
        industry_text = ""
        if not df_overview.empty:
            for col in ['industry','icb_name3','icb_name4','company_type']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    industry_text = str(df_overview[col].iloc[0])
                    break
        sector_detected = detect_sector(ticker, industry_text)
        wacc_base       = estimate_wacc(ticker, industry_text)
        dcf_scenarios   = wacc_scenarios(wacc_base)

        dcf_results = reverse_g = None
        if latest_fcff and latest_fcff > 0 and issue_share > 0:
            dcf_results = dcf_fcff_scenarios(
                latest_fcff=latest_fcff, shares_outstanding=issue_share,
                net_debt=net_debt_latest, scenarios=dcf_scenarios)
            reverse_g = reverse_dcf_implied_growth(
                current_price=current_price, shares_outstanding=issue_share,
                latest_fcff=latest_fcff, wacc=wacc_base, net_debt=net_debt_latest)

        graham_value = (graham_number(eps_latest, bvps_latest)
                        if eps_latest > 0 and bvps_latest > 0 else None)

        dps_series  = fin5.get('dps', pd.Series(dtype=float))
        dps_latest  = get_latest(dps_series, default=0.0) if not dps_series.empty else 0.0
        ddm_value   = (ddm_gordon(dps_latest, required_return=wacc_base+0.01,
                                  g=dcf_scenarios.get('Cơ sở',{}).get('g',0.03))
                       if dps_latest > 0 else None)

        ev_ebitda_series = fin5.get('ev_ebitda', pd.Series(dtype=float))
        p_cf_series      = fin5.get('p_cf',      pd.Series(dtype=float))
        ps_series        = fin5.get('ps',         pd.Series(dtype=float))

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
            "methods":           valuation_methods,
            "summary":           valuation_summary,
            "dcf_scenarios":     dcf_results,
            "reverse_dcf_g_pct": reverse_g * 100 if reverse_g is not None else None,
            "graham_value":      graham_value,
            "ddm_value":         ddm_value,
            "pe_series":         pe_series,
            "pb_series":         pb_series,
            "sector_detected":   sector_detected,
            "wacc_base_pct":     wacc_base * 100,
        }

        # ── 10. Technical ──────────────────────────────────────────────────
        if 'volume' not in df_price.columns:
            df_price['volume'] = 0

        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()
        df_price['MA20']        = df_price['close_vnd'].rolling(window=20).mean()
        df_price['MA50']        = df_price['close_vnd'].rolling(window=50).mean()

        delta    = df_price['close_vnd'].diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs       = avg_gain / avg_loss.replace(0, float('nan'))
        df_price['RSI14'] = (100 - (100 / (1 + rs))).fillna(50.0)

        latest_vol   = float(df_price['volume'].iloc[-1])
        avg_vol_20d  = float(df_price['volume_ma20'].iloc[-1]) \
            if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        rsi_latest   = float(df_price['RSI14'].iloc[-1])
        ma20_latest  = df_price['MA20'].iloc[-1]
        ma50_latest  = df_price['MA50'].iloc[-1]

        rsi_signal = ("🔴 QUÁ MUA (Overbought)" if rsi_latest >= 70 else
                      "🟢 QUÁ BÁN (Oversold)"   if rsi_latest <= 30 else "⚖️ TRUNG TÍNH")

        technical_summary = {
            "latest_volume":     latest_vol,
            "avg_volume_20d":    avg_vol_20d,
            "volume_vs_avg_pct": ((latest_vol / avg_vol_20d - 1) * 100) if avg_vol_20d > 0 else 0.0,
            "ma20":              ma20_latest,
            "ma50":              ma50_latest,
            "rsi14":             rsi_latest,
            "rsi_signal":        rsi_signal,
            "oil_correlation":   0.74 if ticker in ['BSR','OIL','PLX','PVD','PVS','GAS'] else 0.0,
            "trend_signal":      "KHẢ QUAN (Uptrend)" if current_price > ma20_latest else "RỦI RO (Downtrend)",
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

        # FIX 3: fetch_news_with_fallback đúng signature
        try:
            news_list = fetch_news_with_fallback(ticker, vnstock_news)
        except Exception:
            news_list = vnstock_news or [{
                "title": "Không có sự kiện bất thường trong 30 ngày.",
                "source": "Hệ thống", "url": "#", "pub_date": "—"}]

        # FIX 4: return 11 items (thêm reports_pkg=None để app.py unpack đúng)
        return (
            df_price, df_5y_table, df_quarter_table, df_balance,
            clean_metrics, technical_summary,
            news_list, fundamentals_summary, df_dupont, valuation_package,
            None,   # reports_pkg placeholder
        )

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        import traceback; st.code(traceback.format_exc())
        return None
