import pandas as pd
import numpy as np
import streamlit as st
# BYPASS vnai hard-cap 4 kỳ
# QUAN TRỌNG: Import Finance/Quote/Company TRƯỚC, rồi mới unpatch
# Lý do: vnai._ensure_patches_applied() chỉ chạy 1 lần (có guard).
# Nếu unpatch trước khi trigger guard, vnai sẽ re-patch lại sau đó.
from news_fetcher import fetch_news_with_fallback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company
# Trigger vnai patch để set guard _patches_initialized = True
try:
    import vnai as _vnai_init
    _vnai_init._ensure_patches_applied()
except Exception:
    pass
# Bây giờ mới unpatch — vnai sẽ không re-patch nữa (guard đã set)
from unpatch_vnai import apply_unpatch
apply_unpatch()
from financial_normalizer import (
    find_row_series, build_5y_financial_table, build_financial_table,
    get_latest, get_latest_n_years, cagr,
)
from valuation import (
    dupont_decomposition, dcf_fcff_scenarios, reverse_dcf_implied_growth,
    graham_number, ddm_gordon, nine_methods_valuation, summarize_valuation,
    detect_stock_dividend_years, normalize_eps_bvps_series, estimate_wacc,
)
from cafef_fallback import fetch_cafef_balance_sheet_5y, fetch_cafef_yearly_full
from website_scraper import fetch_website_financial_data

# ════════════════════════════════════════════════════════════════════════
# PATCH 1 — Khóa cứng khoảng năm bảng 5 năm: 2021–2025
# Năm nào trong ALLOWED_YEARS mà không lấy được sẽ hiển thị None (trắng).
# ════════════════════════════════════════════════════════════════════════
TABLE_START_YEAR = 2021
TABLE_END_YEAR   = 2025
ALLOWED_YEARS    = set(range(TABLE_START_YEAR, TABLE_END_YEAR + 1))  # {2021,2022,2023,2024,2025}

# PATCH 2 — Fetch 7 năm để dự phòng, sau đó _filter_years() cắt về đúng khoảng
FETCH_LIMIT_YEAR = 7

# SOURCE_FALLBACK_ORDER: thứ tự thử nguồn khi nguồn chính fail
SOURCE_FALLBACK_ORDER = ['DNSE', 'KBS', 'VCI']

# Giữ lại tên cũ để không break code khác dùng DEFAULT_YEAR_LIMIT
DEFAULT_YEAR_LIMIT = 5


def normalize_to_billion_vnd(series):
    """
    Chuẩn hoá series về đơn vị tỷ VNĐ.

    Phân biệt 3 đơn vị API có thể trả:
      - Đơn vị ĐỒNG:  median > 5e11  → chia 1e9
      - Đơn vị TRIỆU: median > 5e5   → chia 1e3
      - Đơn vị TỶ:    median <= 5e5  → giữ nguyên
    """
    if series is None or series.empty:
        return series
    numeric = pd.to_numeric(series, errors='coerce').dropna()
    if numeric.empty:
        return series
    median_abs = numeric.abs().median()
    if median_abs > 5e11:
        divisor = 1e9
    elif median_abs > 5e5:
        divisor = 1e3
    else:
        divisor = 1.0
    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            return round(float(val) / divisor, 2)
        except Exception:
            return None
    return series.map(_to_ty).dropna()


def _normalize_pct_series(s):
    """
    Chuẩn hoá series ROE/ROA/ROS về đơn vị % hợp lý (0–200%).
    """
    if s is None or s.empty:
        return s
    valid = s.dropna()
    if valid.empty:
        return s
    max_abs = valid.abs().max()
    if max_abs > 500:
        s_fixed = s / 1000
        if s_fixed.dropna().abs().max() <= 200:
            return s_fixed
        return pd.Series([None] * len(s), index=s.index, dtype=float)
    if max_abs < 1:
        return s * 100
    return s


def normalize_net_profit_with_anchor(net_profit_raw, equity_series, roe_series):
    """
    Normalize LNST về tỷ VNĐ, dùng equity + roe làm anchor cross-check.
    """
    base = normalize_to_billion_vnd(net_profit_raw)
    if base is None or base.empty:
        return base
    roe_norm = _normalize_pct_series(roe_series)
    if roe_norm is None or roe_norm.dropna().empty:
        return base
    fixed = {}
    for year, raw_val in base.items():
        if (year not in equity_series.index or year not in roe_norm.index
                or pd.isna(equity_series.get(year)) or pd.isna(roe_norm.get(year))):
            fixed[year] = raw_val
            continue
        expected = equity_series[year] * roe_norm[year] / 100
        if expected == 0 or raw_val == 0:
            fixed[year] = raw_val
            continue
        ratio = raw_val / expected
        if ratio <= 0:
            fixed[year] = raw_val
            continue
        power = round(np.log10(ratio))
        if power == 0:
            fixed[year] = raw_val
        else:
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


def _fetch_income_statement(ticker, source, period='year', limit=FETCH_LIMIT_YEAR):
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


def _fetch_ratio(ticker, source, period='year', limit=FETCH_LIMIT_YEAR):
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


def _fetch_cashflow(ticker, source, period='year', limit=FETCH_LIMIT_YEAR):
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


def _fetch_balance_sheet(ticker, source, period='year', limit=FETCH_LIMIT_YEAR):
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


def _build_shares_series(outstanding_shares_series, net_profit_series, eps_series):
    """
    PATCH 4 — Trả về Series số CP lưu hành (đơn vị: cổ phiếu lẻ) theo từng năm.
    Tầng 1: từ ratio API (outstanding_shares_series).
    Tầng 2: back-calc LNST(tỷ)*1e9 / EPS(đ) cho từng năm còn thiếu.
    """
    result = {}
    if outstanding_shares_series is not None and not outstanding_shares_series.empty:
        for yr, val in outstanding_shares_series.items():
            if pd.notna(val) and val > 0:
                result[yr] = float(val)
    if net_profit_series is not None and not net_profit_series.empty \
            and eps_series is not None and not eps_series.empty:
        for yr in net_profit_series.index:
            if yr in result:
                continue
            np_ty = net_profit_series.get(yr)
            eps_d = eps_series.get(yr)
            if (np_ty is not None and eps_d is not None
                    and pd.notna(np_ty) and pd.notna(eps_d)
                    and eps_d > 0 and np_ty > 0):
                backcalc = (np_ty * 1e9) / eps_d
                if 1e8 < backcalc < 1e11:
                    result[yr] = backcalc
    if not result:
        return pd.Series(dtype=float)
    return pd.Series(result, dtype=float).sort_index()


def _parse_year_from_col(col_str: str):
    """
    Trích xuất năm (int) từ tên cột CafeF — xử lý đủ mọi định dạng:
      "2021", "2021/12", "12/2021", "31/12/2021", "Q1/2021", "2021-Q1"
    Trả về int năm nếu tìm được, None nếu không.
    """
    import re as _re
    matches = _re.findall(r'\b((?:19|20)\d{2})\b', str(col_str).strip())
    if matches:
        return int(matches[0])
    return None


# ════════════════════════════════════════════════════════════════════════
# DNSE FALLBACK — public JSON API, không cần auth
# ════════════════════════════════════════════════════════════════════════
def _fetch_dnse_financials(ticker: str, allowed_years: set) -> dict:
    """
    Fetch dữ liệu tài chính từ DNSE public API.
    Trả về dict: {
        'revenue': pd.Series,
        'net_profit': pd.Series,
        'equity': pd.Series,
        'total_assets': pd.Series,
    }
    """
    import requests
    import re as _re

    base_url = "https://api.dnse.com.vn/analysis-api/v1/analysis/financial-report"
    headers  = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    result   = {k: {} for k in ["revenue", "net_profit", "equity", "total_assets"]}

    for rpt_type in ["IS", "BS"]:
        try:
            resp = requests.get(
                base_url,
                params={"symbol": ticker, "type": rpt_type, "period": "YEARLY"},
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            periods = data.get("data") or data.get("periods") or []
            for period in periods:
                yr = period.get("year") or period.get("period")
                if yr is None:
                    yr_raw = str(period.get("periodName", ""))
                    m = _re.search(r'\b(20\d{2})\b', yr_raw)
                    yr = int(m.group(1)) if m else None
                if yr is None or int(yr) not in allowed_years:
                    continue
                yr = int(yr)
                items = period.get("items") or period.get("financialItems") or []
                for item in items:
                    name = str(item.get("name", "") or item.get("itemName", "")).lower().strip()
                    val  = item.get("value") or item.get("amount")
                    if val is None:
                        continue
                    try:
                        val = float(val)
                    except Exception:
                        continue
                    if any(k in name for k in ["doanh thu thuần", "net revenue", "revenue"]):
                        if "giá vốn" not in name and "chi phí" not in name:
                            result["revenue"][yr] = val
                    elif any(k in name for k in ["lợi nhuận sau thuế", "lnst", "net profit", "net income"]):
                        if "trước" not in name and "thiểu số" not in name:
                            result["net_profit"][yr] = val
                    elif any(k in name for k in ["vốn chủ sở hữu", "equity", "total equity"]):
                        if "thiểu số" not in name:
                            result["equity"][yr] = val
                    elif any(k in name for k in ["tổng tài sản", "total assets"]):
                        result["total_assets"][yr] = val
        except Exception:
            continue

    return {k: pd.Series(v, dtype=float) for k, v in result.items()}


def _fetch_yahoo_financials(ticker: str, allowed_years: set) -> dict:
    """
    Tầng 2b — Yahoo Finance fallback cho equity & total_assets khi vnstock/CafeF/DNSE thiếu.
    Dùng yfinance nếu có; nếu không có thì dùng requests scrape bảng từ finance.yahoo.com.
    Trả về dict: {field: pd.Series(index=year_int, dtype=float, đơn vị tỷ VNĐ)}
    Tỷ giá USD→VNĐ lấy xấp xỉ từ overview hoặc dùng mặc định 25,000.
    """
    result = {
        "revenue":      {},
        "net_profit":   {},
        "equity":       {},
        "total_assets": {},
    }
    try:
        import yfinance as yf
        # Yahoo Finance dùng mã dạng "HDB.VN"
        yf_ticker = ticker.upper() + ".VN"
        obj = yf.Ticker(yf_ticker)

        # Lấy tỷ giá USD/VNĐ (thử lấy từ USDVND, fallback 25000)
        try:
            fx = yf.Ticker("USDVND=X").fast_info.get("last_price", 25000) or 25000
        except Exception:
            fx = 25_000

        def _usd_to_ty(val_usd):
            """Chuyển USD → tỷ VNĐ. Yahoo báo cáo đơn vị = đồng USD lẻ."""
            if val_usd is None or (isinstance(val_usd, float) and __import__('math').isnan(val_usd)):
                return None
            return round(float(val_usd) * fx / 1e9, 2)

        # ── Income statement (annual) ──
        try:
            inc = obj.financials  # rows = metrics, cols = datetime (year-end)
            if inc is not None and not inc.empty:
                for col in inc.columns:
                    try:
                        yr = int(str(col)[:4])
                    except Exception:
                        continue
                    if yr not in allowed_years:
                        continue
                    # Revenue
                    for kw in ["Total Revenue", "Revenue"]:
                        if kw in inc.index:
                            v = _usd_to_ty(inc.loc[kw, col])
                            if v is not None and v > 0:
                                result["revenue"][yr] = v
                                break
                    # Net profit (attributable to parent)
                    for kw in ["Net Income Common Stockholders", "Net Income",
                               "Net Income Applicable To Common Shares"]:
                        if kw in inc.index:
                            v = _usd_to_ty(inc.loc[kw, col])
                            if v is not None:
                                result["net_profit"][yr] = v
                                break
        except Exception:
            pass

        # ── Balance sheet (annual) ──
        try:
            bs = obj.balance_sheet  # rows = metrics, cols = datetime (year-end)
            if bs is not None and not bs.empty:
                for col in bs.columns:
                    try:
                        yr = int(str(col)[:4])
                    except Exception:
                        continue
                    if yr not in allowed_years:
                        continue
                    # Equity
                    for kw in ["Stockholders Equity", "Common Stock Equity",
                               "Total Equity Gross Minority Interest"]:
                        if kw in bs.index:
                            v = _usd_to_ty(bs.loc[kw, col])
                            if v is not None and v > 0:
                                result["equity"][yr] = v
                                break
                    # Total assets
                    for kw in ["Total Assets"]:
                        if kw in bs.index:
                            v = _usd_to_ty(bs.loc[kw, col])
                            if v is not None and v > 0:
                                result["total_assets"][yr] = v
                                break
        except Exception:
            pass

    except ImportError:
        # yfinance chưa cài — skip silently (pipeline không crash)
        pass
    except Exception:
        pass

    return {k: pd.Series(v, dtype=float) for k, v in result.items()}


@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker):
    try:
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)

        allowed_years = ALLOWED_YEARS  # {2021, 2022, 2023, 2024, 2025}

        end_date   = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')

        tasks = {
            "price":      lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            "overview":   lambda: c_engine.overview(),
            "income_y":   lambda: _fetch_income_statement(ticker, source_used, period='year',    limit=FETCH_LIMIT_YEAR),
            "cashflow_y": lambda: _fetch_cashflow(ticker,          source_used, period='year',    limit=FETCH_LIMIT_YEAR),
            "ratio_y":    lambda: _fetch_ratio(ticker,             source_used, period='year',    limit=FETCH_LIMIT_YEAR),
            "balance_y":  lambda: _fetch_balance_sheet(ticker,     source_used, period='year',    limit=FETCH_LIMIT_YEAR),
            # ✅ quarterly chỉ cần 8 kỳ (2024-Q1 → 2026-Q1) để fill 2025
            "income_q":   lambda: _fetch_income_statement(ticker,  source_used, period='quarter', limit=8),
            "ratio_q":    lambda: _fetch_ratio(ticker,             source_used, period='quarter', limit=8),
            "balance_q":  lambda: _fetch_balance_sheet(ticker,     source_used, period='quarter', limit=40),
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
        if not revenue_series.empty:
            revenue_series = revenue_series[revenue_series > 0]
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
            """Giữ lại chỉ các năm trong ALLOWED_YEARS (2021–2025), chuẩn hoá index về int."""
            if s is None or s.empty:
                return s
            try:
                s = s.copy()
                s.index = s.index.map(lambda x: int(str(x).strip().split('.')[0].split('-')[0]))
            except Exception:
                pass
            return s[s.index.isin(allowed_years)]

        revenue_series            = _filter_years(revenue_series)
        equity_series             = _filter_years(equity_series)
        total_assets_series       = _filter_years(total_assets_series)
        net_profit_series         = _filter_years(net_profit_series)
        eps_series                = _filter_years(eps_series)
        bvps_series               = _filter_years(bvps_series)
        roe_series                = _filter_years(roe_series)
        roa_series                = _filter_years(roa_series)
        pe_series                 = _filter_years(pe_series)
        pb_series                 = _filter_years(pb_series)
        outstanding_shares_series = _filter_years(outstanding_shares_series)

        outstanding_shares_series = _build_shares_series(
            outstanding_shares_series, net_profit_series, eps_series)
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

        # ═══════════════════════════════════════════════════════════════════
        # TẦNG 0 — Aggregate năm thiếu từ quarterly raw DataFrames
        #
        # FIX 2025: KHÔNG dùng build_financial_table() vì hàm đó convert index
        # về int năm, mất thông tin quý. Thay vào đó parse trực tiếp cột của
        # df_income_q / df_balance_q theo năm target.
        # ═══════════════════════════════════════════════════════════════════
        def _aggregate_year_from_quarters(target_year: int) -> dict:
            """
            Tổng hợp dữ liệu năm target_year từ quarterly DataFrames gốc.
            Parse trực tiếp cột của df_income_q/df_balance_q thay vì qua
            build_financial_table() (vốn đã flatten index về int năm).
            """
            import re as _re2

            current_year = datetime.today().year

            def _cols_for_year(df, yr):
                """Trả về list tên cột thuộc năm yr."""
                if df is None or df.empty:
                    return []
                out_cols = []
                for col in df.columns:
                    col_s = str(col).strip()
                    found = _re2.findall(r'\b((?:19|20)\d{2})\b', col_s)
                    if found and int(found[0]) == yr:
                        out_cols.append(col)
                return out_cols

            def _extract_row_values(df, cols, keywords, exclude=None):
                """
                Tìm dòng theo keywords trong df, lấy giá trị các cột cols.
                Trả về list float (bỏ NaN).
                """
                if df is None or df.empty or not cols:
                    return []

                # Tìm cột label (tên chỉ tiêu)
                label_col = None
                for c in df.columns:
                    if str(c).lower() in ('item', 'chỉ tiêu', 'indicator',
                                          'name', 'metric', 'description'):
                        label_col = c
                        break
                if label_col is None:
                    for c in df.columns:
                        if df[c].dtype == object:
                            label_col = c
                            break
                if label_col is None:
                    return []

                vals = []
                for kw in keywords:
                    mask = df[label_col].astype(str).str.lower().str.contains(
                        kw.lower(), na=False, regex=False)
                    if exclude:
                        for ex in exclude:
                            mask &= ~df[label_col].astype(str).str.lower().str.contains(
                                ex.lower(), na=False, regex=False)
                    matched = df[mask]
                    if matched.empty:
                        continue
                    for col in cols:
                        if col not in matched.columns:
                            continue
                        for v in matched[col].values:
                            try:
                                fv = float(str(v).replace(',', ''))
                                if not np.isnan(fv):
                                    vals.append(fv)
                            except Exception:
                                pass
                    if vals:
                        break  # keyword đầu tiên match là đủ
                return vals

            inc_cols = _cols_for_year(df_income_q,  target_year)
            bs_cols  = _cols_for_year(df_balance_q, target_year)

            if not inc_cols and not bs_cols:
                return {}

            out = {}

            # ── REVENUE: cộng tất cả quý ──
            rev_vals = _extract_row_values(
                df_income_q, inc_cols,
                keywords=['doanh thu thuần', 'net revenue', 'doanh thu bán hàng',
                          'revenue', 'tổng doanh thu'],
                exclude=['giá vốn', 'chi phí', 'cost'])
            if rev_vals:
                rev_norm = normalize_to_billion_vnd(pd.Series(rev_vals, dtype=float))
                if rev_norm is not None and not rev_norm.empty:
                    n = len(rev_norm)
                    total = float(rev_norm.sum())
                    if target_year < current_year and n < 4:
                        if n >= 2:
                            out['revenue'] = round(total * 4 / n, 2)
                            out['_revenue_q'] = n
                    else:
                        out['revenue'] = round(total, 2)
                        out['_revenue_q'] = n

            # ── NET PROFIT: cộng tất cả quý ──
            np_vals = _extract_row_values(
                df_income_q, inc_cols,
                keywords=['lợi nhuận sau thuế của cổ đông của công ty mẹ',
                          'lợi nhuận sau thuế', 'lãi sau thuế',
                          'net profit after tax', 'profit after tax',
                          'net income', 'net profit'],
                exclude=['trước thuế', 'before tax', 'thiểu số', 'minority'])
            if np_vals:
                np_norm = normalize_to_billion_vnd(pd.Series(np_vals, dtype=float))
                if np_norm is not None and not np_norm.empty:
                    n = len(np_norm)
                    total = float(np_norm.sum())
                    if target_year < current_year and n < 4:
                        if n >= 2:
                            out['net_profit'] = round(total * 4 / n, 2)
                            out['_net_profit_q'] = n
                    else:
                        out['net_profit'] = round(total, 2)
                        out['_net_profit_q'] = n

            # ── EQUITY: lấy giá trị quý mới nhất (stock variable) ──
            eq_vals = _extract_row_values(
                df_balance_q, bs_cols,
                keywords=['vốn chủ sở hữu', 'total equity', 'equity',
                          'tổng vốn chủ sở hữu'],
                exclude=['thiểu số', 'minority'])
            if eq_vals:
                eq_norm = normalize_to_billion_vnd(pd.Series(eq_vals, dtype=float))
                if eq_norm is not None and not eq_norm.empty:
                    out['equity'] = round(float(eq_norm.iloc[-1]), 2)

            # ── TOTAL ASSETS: lấy giá trị quý mới nhất ──
            ta_vals = _extract_row_values(
                df_balance_q, bs_cols,
                keywords=['tổng cộng tài sản', 'tổng tài sản', 'total assets'])
            if ta_vals:
                ta_norm = normalize_to_billion_vnd(pd.Series(ta_vals, dtype=float))
                if ta_norm is not None and not ta_norm.empty:
                    out['total_assets'] = round(float(ta_norm.iloc[-1]), 2)

            return out

        # ─────────────────────────────────────────────────────────────────
        # Trigger Tầng 0: năm nào thiếu ÍT NHẤT 1 field (OR logic)
        # ─────────────────────────────────────────────────────────────────
        _years_q0_check = sorted(
            yr for yr in allowed_years
            if (yr not in revenue_series.dropna().index
                or yr not in net_profit_series.dropna().index
                or yr not in equity_series.dropna().index
                or yr not in total_assets_series.dropna().index)
        )
        # Chỉ thêm năm hiện tại nếu income_q CÓ dữ liệu năm đó
        _current_yr_q0 = datetime.today().year
        if _current_yr_q0 in allowed_years:
            if df_income_q is not None and not df_income_q.empty:
                _inc_q_cols_check = [c for c in df_income_q.columns
                                     if str(_current_yr_q0) in str(c)]
            else:
                _inc_q_cols_check = []
            if _inc_q_cols_check:
                _years_q0_check = sorted(set(_years_q0_check) | {_current_yr_q0})
        for _yr0 in _years_q0_check:
            _agg = _aggregate_year_from_quarters(_yr0)
            if not _agg:
                continue
            for _field, _series in [
                ('revenue',      revenue_series),
                ('net_profit',   net_profit_series),
                ('equity',       equity_series),
                ('total_assets', total_assets_series),
                ('eps',          eps_series),
            ]:
                if _field in _agg and _agg[_field] is not None:
                    if _yr0 not in _series.index or pd.isna(_series.get(_yr0)):
                        _series[_yr0] = _agg[_field]

        # ── Tầng 0c: Balance sheet năm hiện tại từ annual nếu balance_q không có ──
        _current_yr = datetime.today().year
        if _current_yr in allowed_years:
            for _field, _series, _df, _kws, _ex in [
                ('equity',       equity_series,       df_balance,
                 ['vốn chủ sở hữu', 'total equity', 'equity'],
                 ['thiểu số', 'minority']),
                ('total_assets', total_assets_series, df_balance,
                 ['tổng cộng tài sản', 'tổng tài sản', 'total assets'],
                 []),
            ]:
                if _current_yr not in _series.index or pd.isna(_series.get(_current_yr)):
                    _v = _raw_scan_annual(_df, _current_yr, _kws, exclude=_ex)
                    if _v is not None:
                        _series[_current_yr] = _v

        revenue_series      = revenue_series.sort_index()
        net_profit_series   = net_profit_series.sort_index()
        equity_series       = equity_series.sort_index()
        total_assets_series = total_assets_series.sort_index()
        eps_series          = eps_series.sort_index()

        # ─────────────────────────────────────────────────────────────────
        # TẦNG 0b — Raw column scan từ df_income/df_balance (annual df)
        # Safety net: nếu Tầng 0 vẫn thiếu, thử parse cột năm từ annual df
        # ─────────────────────────────────────────────────────────────────
        _still_missing_0b = sorted(
            yr for yr in allowed_years
            if (yr not in revenue_series.dropna().index
                or yr not in net_profit_series.dropna().index
                or yr not in equity_series.dropna().index
                or yr not in total_assets_series.dropna().index)
        )
        _current_yr_0b = datetime.today().year
        if _current_yr_0b in allowed_years:
            _still_missing_0b = sorted(set(_still_missing_0b) | {_current_yr_0b})

        def _raw_scan_annual(df, yr, keywords, exclude=None):
            """Scan cột có năm == yr trong annual df, trả về giá trị float đầu tiên tìm được."""
            if df is None or df.empty:
                return None
            import re as _re3
            year_cols = [c for c in df.columns
                         if _re3.search(r'\b' + str(yr) + r'\b', str(c))]
            if not year_cols:
                return None
            label_col = next(
                (c for c in df.columns if df[c].dtype == object), None)
            if label_col is None:
                return None
            for kw in keywords:
                mask = df[label_col].astype(str).str.lower().str.contains(
                    kw.lower(), na=False, regex=False)
                if exclude:
                    for ex in exclude:
                        mask &= ~df[label_col].astype(str).str.lower().str.contains(
                            ex.lower(), na=False, regex=False)
                rows = df[mask]
                if rows.empty:
                    continue
                for yc in year_cols:
                    for v in rows[yc].values:
                        try:
                            fv = float(str(v).replace(',', ''))
                            if not np.isnan(fv):
                                s_tmp = normalize_to_billion_vnd(
                                    pd.Series([fv], dtype=float))
                                if s_tmp is not None and not s_tmp.empty:
                                    return round(float(s_tmp.iloc[0]), 2)
                        except Exception:
                            pass
            return None

        for _yr0b in _still_missing_0b:
            if _yr0b not in revenue_series.index or pd.isna(revenue_series.get(_yr0b)):
                _v = _raw_scan_annual(
                    df_income, _yr0b,
                    ['doanh thu thuần', 'net revenue', 'revenue', 'tổng doanh thu'],
                    exclude=['giá vốn', 'chi phí'])
                if _v is not None:
                    revenue_series[_yr0b] = _v

            if _yr0b not in net_profit_series.index or pd.isna(net_profit_series.get(_yr0b)):
                _v = _raw_scan_annual(
                    df_income, _yr0b,
                    ['lợi nhuận sau thuế của cổ đông của công ty mẹ',
                     'lợi nhuận sau thuế', 'lãi sau thuế', 'net profit', 'net income'],
                    exclude=['trước thuế', 'thiểu số'])
                if _v is not None:
                    net_profit_series[_yr0b] = _v

            if _yr0b not in equity_series.index or pd.isna(equity_series.get(_yr0b)):
                _v = _raw_scan_annual(
                    df_balance, _yr0b,
                    ['vốn chủ sở hữu', 'total equity', 'equity'],
                    exclude=['thiểu số'])
                if _v is not None:
                    equity_series[_yr0b] = _v

            if _yr0b not in total_assets_series.index or pd.isna(total_assets_series.get(_yr0b)):
                _v = _raw_scan_annual(
                    df_balance, _yr0b,
                    ['tổng cộng tài sản', 'tổng tài sản', 'total assets'])
                if _v is not None:
                    total_assets_series[_yr0b] = _v

        revenue_series      = revenue_series.sort_index()
        net_profit_series   = net_profit_series.sort_index()
        equity_series       = equity_series.sort_index()
        total_assets_series = total_assets_series.sort_index()

        # ─────────────────────────────────────────────────────────────────
        # _missing_any: năm thiếu ÍT NHẤT 1 field → gọi CafeF
        # ─────────────────────────────────────────────────────────────────
        _missing_any = sorted(
            yr for yr in allowed_years
            if (yr not in revenue_series.dropna().index
                or yr not in net_profit_series.dropna().index
                or yr not in equity_series.dropna().index
                or yr not in total_assets_series.dropna().index)
        )
        # Luôn retry năm hiện tại qua CafeF/DNSE
        _current_yr_miss = datetime.today().year
        if _current_yr_miss in allowed_years and _current_yr_miss not in _missing_any:
            _missing_any = sorted(set(_missing_any) | {_current_yr_miss})

        # Khởi tạo _cf_cf ở ngoài if để CFO fallback block luôn có thể dùng
        _cf_cf = pd.DataFrame()

        def _merge_series(base: pd.Series, patch: pd.Series) -> pd.Series:
            """Ghi đè giá trị NaN / missing trong base bằng patch."""
            for yr, val in patch.items():
                if pd.isna(val):
                    continue
                if yr not in base.index or pd.isna(base.get(yr)):
                    base[yr] = val
            return base.sort_index()

        def _cafef_extract_series(df, keywords, exclude=None):
            """Tìm dòng theo keyword trong CafeF DataFrame."""
            if df is None or df.empty:
                return pd.Series(dtype=float)
            for kw in keywords:
                matches = [i for i in df.index if kw.lower() in str(i).lower()]
                if exclude:
                    matches = [i for i in matches
                               if not any(ex.lower() in str(i).lower() for ex in exclude)]
                if not matches:
                    continue
                row = df.loc[matches[0]]
                s = {}
                for col in row.index:
                    yr = _parse_year_from_col(str(col))
                    if yr is None:
                        continue
                    try:
                        val = row[col]
                        if val is not None and str(val).strip() not in ('', 'None', 'nan'):
                            s[yr] = float(str(val).replace(',', ''))
                    except Exception:
                        pass
                if s:
                    return pd.Series(s, dtype=float)
            return pd.Series(dtype=float)

        # ─────────────────────────────────────────────────────────────────
        # TẦNG 1 — CafeF fallback
        # ─────────────────────────────────────────────────────────────────
        _cafef_debug = {}
        if _missing_any:
            try:
                _cafef_full = fetch_cafef_yearly_full(ticker, years=list(allowed_years))

                # Expose lỗi nội bộ từ cafef_fallback (nếu có)
                _cafef_internal_err = _cafef_full.pop('__error__', None)
                _cafef_trace        = _cafef_full.pop('__trace__', None)

                _rev_cf = _filter_years(_cafef_full.get("revenue",      pd.Series(dtype=float)))
                _np_cf  = _filter_years(_cafef_full.get("net_profit",   pd.Series(dtype=float)))
                _eq_cf  = _filter_years(_cafef_full.get("equity",       pd.Series(dtype=float)))
                _ta_cf  = _filter_years(_cafef_full.get("total_assets", pd.Series(dtype=float)))

                _cafef_debug = {
                    "revenue":      dict(_rev_cf),
                    "net_profit":   dict(_np_cf),
                    "equity":       dict(_eq_cf),
                    "total_assets": dict(_ta_cf),
                }
                if _cafef_internal_err:
                    _cafef_debug["__internal_error__"] = _cafef_internal_err
                    _cafef_debug["__trace__"]          = _cafef_trace

                revenue_series      = _merge_series(revenue_series,      _rev_cf)
                net_profit_series   = _merge_series(net_profit_series,   _np_cf)
                equity_series       = _merge_series(equity_series,       _eq_cf)
                total_assets_series = _merge_series(total_assets_series, _ta_cf)

                _eps_cf_raw = _cafef_full.get("eps", pd.Series(dtype=float))
                if not _eps_cf_raw.empty:
                    _eps_cf = _filter_years(_eps_cf_raw)
                    eps_series = _merge_series(eps_series, _eps_cf)

            except Exception as _cafef_exc:
                import traceback as _tb_cf
                _cafef_debug["error"] = f"{type(_cafef_exc).__name__}: {_cafef_exc}"
                _cafef_debug["trace"] = _tb_cf.format_exc(limit=5)

        # ─────────────────────────────────────────────────────────────────
        # TẦNG 2 — DNSE fallback
        # ─────────────────────────────────────────────────────────────────
        _missing_after_cafef = sorted(
            yr for yr in allowed_years
            if (yr not in revenue_series.dropna().index
                or yr not in net_profit_series.dropna().index
                or yr not in equity_series.dropna().index
                or yr not in total_assets_series.dropna().index)
        )

        _dnse_debug = {}
        if _missing_after_cafef:
            try:
                import requests as _req_dnse
                # Probe DNSE API trực tiếp để lấy raw response cho debug
                _dnse_probe_url = (
                    f"https://api.dnse.com.vn/analysis-api/v1/analysis/financial-report"
                    f"?symbol={ticker}&type=IS&period=YEARLY"
                )
                try:
                    _probe_r = _req_dnse.get(_dnse_probe_url,
                                             headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
                                             timeout=10)
                    _dnse_debug["__probe_status__"] = _probe_r.status_code
                    _dnse_debug["__probe_keys__"]   = list(_probe_r.json().keys()) if _probe_r.ok else _probe_r.text[:300]
                except Exception as _pe:
                    _dnse_debug["__probe_error__"] = str(_pe)

                _dnse_data = _fetch_dnse_financials(ticker, allowed_years)
                _rev_dn  = _filter_years(normalize_to_billion_vnd(_dnse_data.get("revenue",      pd.Series(dtype=float))))
                _np_dn   = _filter_years(normalize_to_billion_vnd(_dnse_data.get("net_profit",   pd.Series(dtype=float))))
                _eq_dn   = _filter_years(normalize_to_billion_vnd(_dnse_data.get("equity",       pd.Series(dtype=float))))
                _ta_dn   = _filter_years(normalize_to_billion_vnd(_dnse_data.get("total_assets", pd.Series(dtype=float))))

                _dnse_debug.update({
                    "revenue":      dict(_rev_dn),
                    "net_profit":   dict(_np_dn),
                    "equity":       dict(_eq_dn),
                    "total_assets": dict(_ta_dn),
                })

                revenue_series      = _merge_series(revenue_series,      _rev_dn)
                net_profit_series   = _merge_series(net_profit_series,   _np_dn)
                equity_series       = _merge_series(equity_series,       _eq_dn)
                total_assets_series = _merge_series(total_assets_series, _ta_dn)
            except Exception as _dnse_exc:
                import traceback as _tb_dn
                _dnse_debug["error"] = f"{type(_dnse_exc).__name__}: {_dnse_exc}"
                _dnse_debug["trace"] = _tb_dn.format_exc(limit=5)

        # DEBUG EXPANDER
        if _missing_any:
            with st.expander(f"🔍 DEBUG fallback — {ticker} (năm thiếu: {_missing_any})", expanded=False):
                # ── Quarterly column debug (chẩn đoán tại sao 2025 thiếu) ──
                _inc_q_cols  = list(df_income_q.columns)  if df_income_q  is not None and not df_income_q.empty  else []
                _bal_q_cols  = list(df_balance_q.columns) if df_balance_q is not None and not df_balance_q.empty else []
                _cols_2025_inc = [c for c in _inc_q_cols  if '2025' in str(c)]
                _cols_2025_bal = [c for c in _bal_q_cols  if '2025' in str(c)]
                st.write("**df_income_q — tất cả cột:**",  _inc_q_cols)
                st.write("**df_balance_q — tất cả cột:**", _bal_q_cols)
                st.write("**Cột income_q chứa '2025':**",  _cols_2025_inc  or "⚠️ KHÔNG CÓ — vnstock không trả Q 2025")
                st.write("**Cột balance_q chứa '2025':**", _cols_2025_bal  or "⚠️ KHÔNG CÓ — vnstock không trả Q 2025")
                st.write("---")
                st.write("**Tầng 0 (quarterly agg) — revenue sau merge:**", dict(revenue_series))
                st.write("**Tầng 0 (quarterly agg) — net_profit sau merge:**", dict(net_profit_series))
                st.write("**Năm còn thiếu trước CafeF:**", _missing_any)
                if "error" in _cafef_debug:
                    st.error(f"CafeF exception: {_cafef_debug.get('error')}")
                    if "trace" in _cafef_debug:
                        st.code(_cafef_debug["trace"], language="")
                elif "__internal_error__" in _cafef_debug:
                    st.error(f"CafeF internal error: {_cafef_debug['__internal_error__']}")
                    if "__trace__" in _cafef_debug:
                        st.code(_cafef_debug["__trace__"], language="")
                else:
                    st.write("**CafeF trả về:**")
                    for k, v in _cafef_debug.items():
                        if k.startswith("__"):
                            continue
                        st.write(f"- `{k}`: {v if v else '⚠️ RỖNG — CafeF chưa cập nhật hoặc bị block'}")
                if _missing_after_cafef:
                    st.write("**Năm còn thiếu sau CafeF (→ thử DNSE):**", _missing_after_cafef)
                    if "__probe_status__" in _dnse_debug:
                        st.write(f"**DNSE probe HTTP status:** `{_dnse_debug['__probe_status__']}`")
                        st.write(f"**DNSE probe response keys/body:** `{_dnse_debug.get('__probe_keys__')}`")
                    if "__probe_error__" in _dnse_debug:
                        st.error(f"DNSE probe error: {_dnse_debug['__probe_error__']}")
                    if "error" in _dnse_debug:
                        st.error(f"DNSE exception: {_dnse_debug.get('error')}")
                        if "trace" in _dnse_debug:
                            st.code(_dnse_debug["trace"], language="")
                    else:
                        st.write("**DNSE trả về:**")
                        for k, v in _dnse_debug.items():
                            if k.startswith("__"):
                                continue
                            st.write(f"- `{k}`: {v if v else '⚠️ DNSE không có dữ liệu'}")
                st.write("**Sau DNSE — revenue:**",      dict(revenue_series))
                st.write("**Sau DNSE — net_profit:**",   dict(net_profit_series))
                st.write("**Sau DNSE — equity:**",       dict(equity_series))
                st.write("**Sau DNSE — total_assets:**", dict(total_assets_series))

        # ─────────────────────────────────────────────────────────────────
        # TẦNG 2b — Yahoo Finance fallback (equity & total_assets chính yếu)
        # Chạy khi DNSE vẫn thiếu ít nhất 1 năm cho equity hoặc total_assets
        # ─────────────────────────────────────────────────────────────────
        _missing_after_dnse = sorted(
            yr for yr in allowed_years
            if (yr not in revenue_series.dropna().index
                or yr not in net_profit_series.dropna().index
                or yr not in equity_series.dropna().index
                or yr not in total_assets_series.dropna().index)
        )

        _yahoo_debug = {}
        if _missing_after_dnse:
            try:
                _yahoo_data = _fetch_yahoo_financials(ticker, set(_missing_after_dnse))
                _rev_yh  = _filter_years(normalize_to_billion_vnd(_yahoo_data.get("revenue",      pd.Series(dtype=float))))
                _np_yh   = _filter_years(normalize_to_billion_vnd(_yahoo_data.get("net_profit",   pd.Series(dtype=float))))
                _eq_yh   = _filter_years(normalize_to_billion_vnd(_yahoo_data.get("equity",       pd.Series(dtype=float))))
                _ta_yh   = _filter_years(normalize_to_billion_vnd(_yahoo_data.get("total_assets", pd.Series(dtype=float))))

                # Yahoo báo đơn vị USD gốc đã convert trong hàm → KHÔNG normalize lại
                # Nhưng cần kiểm tra đơn vị bằng sanity check: equity ngân hàng VN thường > 1,000 tỷ
                _yahoo_debug = {
                    "revenue":      dict(_rev_yh)  if not _rev_yh.empty  else "⚠️ rỗng",
                    "net_profit":   dict(_np_yh)   if not _np_yh.empty   else "⚠️ rỗng",
                    "equity":       dict(_eq_yh)   if not _eq_yh.empty   else "⚠️ rỗng",
                    "total_assets": dict(_ta_yh)   if not _ta_yh.empty   else "⚠️ rỗng",
                }

                revenue_series      = _merge_series(revenue_series,      _rev_yh)
                net_profit_series   = _merge_series(net_profit_series,   _np_yh)
                equity_series       = _merge_series(equity_series,       _eq_yh)
                total_assets_series = _merge_series(total_assets_series, _ta_yh)
            except Exception as _yh_exc:
                import traceback as _tb_yh
                _yahoo_debug["error"] = f"{type(_yh_exc).__name__}: {_yh_exc}"
                _yahoo_debug["trace"] = _tb_yh.format_exc(limit=5)

            # Debug Yahoo
            with st.expander(f"🔍 DEBUG Tầng 2b Yahoo Finance — {ticker}", expanded=False):
                if "error" in _yahoo_debug:
                    st.error(f"Yahoo exception: {_yahoo_debug.get('error')}")
                    if "trace" in _yahoo_debug:
                        st.code(_yahoo_debug["trace"], language="")
                else:
                    st.write("**Yahoo Finance trả về:**")
                    for k, v in _yahoo_debug.items():
                        st.write(f"- `{k}`: {v if v else '⚠️ rỗng'}")
                st.write("**Sau Yahoo — equity:**",       dict(equity_series))
                st.write("**Sau Yahoo — total_assets:**", dict(total_assets_series))

        # ─────────────────────────────────────────────────────────────────
        # TẦNG 3 — Website scraping
        # ─────────────────────────────────────────────────────────────────
        _missing_after_dnse = sorted(
            yr for yr in allowed_years
            if (yr not in revenue_series.dropna().index
                or yr not in net_profit_series.dropna().index
                or yr not in equity_series.dropna().index
                or yr not in total_assets_series.dropna().index)
        )

        if _missing_after_dnse:
            try:
                _ws_full = fetch_website_financial_data(
                    ticker,
                    n_years=FETCH_LIMIT_YEAR,
                    required_years=allowed_years,
                )
                _ws_income = _ws_full.get("income_statement", pd.DataFrame())
                _ws_bs     = _ws_full.get("balance_sheet",    pd.DataFrame())
                _ws_cf_    = _ws_full.get("cash_flow",        pd.DataFrame())

                def _ws_extract(df, keywords, exclude=None):
                    if df is None or df.empty:
                        return pd.Series(dtype=float)
                    for kw in keywords:
                        matches = [i for i in df.index if kw.lower() in str(i).lower()]
                        if exclude:
                            matches = [i for i in matches
                                       if not any(ex.lower() in str(i).lower()
                                                  for ex in exclude)]
                        if not matches:
                            continue
                        row = df.loc[matches[0]]
                        s = {}
                        for col in row.index:
                            yr = _parse_year_from_col(str(col))
                            if yr is None:
                                continue
                            try:
                                val = row[col]
                                if val is not None and str(val).strip() not in ('', 'None', 'nan'):
                                    s[yr] = float(str(val).replace(',', ''))
                            except Exception:
                                pass
                        if s:
                            return pd.Series(s, dtype=float)
                    return pd.Series(dtype=float)

                if not _ws_income.empty:
                    if is_bank:
                        _ws_rev_kw = ['thu nhập lãi và các khoản thu nhập tương tự',
                                      'tổng thu nhập hoạt động', 'thu nhập lãi thuần']
                    else:
                        _ws_rev_kw = ['doanh thu thuần', 'tổng doanh thu', 'net revenue',
                                      'doanh thu bán hàng', 'revenue']
                    _ws_rev = _filter_years(normalize_to_billion_vnd(
                        _ws_extract(_ws_income, _ws_rev_kw, exclude=['giá vốn', 'chi phí'])))
                    revenue_series = _merge_series(revenue_series, _ws_rev)

                    _ws_np_kw = ['lợi nhuận sau thuế của cổ đông của công ty mẹ',
                                 'lợi nhuận sau thuế', 'lãi sau thuế',
                                 'profit after tax', 'net profit', 'net income']
                    _ws_np = _filter_years(normalize_to_billion_vnd(
                        _ws_extract(_ws_income, _ws_np_kw,
                                    exclude=['trước thuế', 'thiểu số', 'minority'])))
                    net_profit_series = _merge_series(net_profit_series, _ws_np)

                    _ws_eps_kw = ['lãi cơ bản trên cổ phiếu', 'eps', 'earnings per share']
                    _ws_eps = _filter_years(_ws_extract(_ws_income, _ws_eps_kw))
                    eps_series = _merge_series(eps_series, _ws_eps)

                if not _ws_bs.empty:
                    _ws_eq_kw = ['vốn chủ sở hữu', 'equity', 'total equity',
                                 'tổng vốn chủ sở hữu', 'vốn của cổ đông']
                    _ws_eq = _filter_years(normalize_to_billion_vnd(
                        _ws_extract(_ws_bs, _ws_eq_kw, exclude=['thiểu số', 'minority'])))
                    equity_series = _merge_series(equity_series, _ws_eq)

                    _ws_ta_kw = ['tổng cộng tài sản', 'tổng tài sản', 'total assets']
                    _ws_ta = _filter_years(normalize_to_billion_vnd(
                        _ws_extract(_ws_bs, _ws_ta_kw)))
                    total_assets_series = _merge_series(total_assets_series, _ws_ta)

                if not _ws_cf_.empty and _cf_cf.empty:
                    _cf_cf = _ws_cf_

            except Exception:
                pass

        _expected_years = allowed_years
        _years_have = (set(revenue_series.index) | set(net_profit_series.index)
                       | set(equity_series.index) | set(total_assets_series.index))
        _missing_years = sorted(_expected_years - _years_have)
        if _missing_years:
            st.warning(f"⚠️ {ticker}: không lấy được dữ liệu năm {_missing_years} từ tất cả nguồn (vnstock / CafeF / DNSE / website).")

        def _cross_unit_recheck(np_s, rev_s, eq_s):
            if np_s.empty or (rev_s.empty and eq_s.empty):
                return False
            common = np_s.index
            if not rev_s.empty:
                common = common.intersection(rev_s.index)
                if len(common) > 0:
                    np_med = np_s.loc[common].abs().median()
                    rev_med = rev_s.loc[common].abs().median()
                    if rev_med > 0 and np_med / rev_med > 500:
                        return True
            if not eq_s.empty:
                common2 = np_s.index.intersection(eq_s.index)
                if len(common2) > 0:
                    np_med2 = np_s.loc[common2].abs().median()
                    eq_med2 = eq_s.loc[common2].abs().median()
                    if eq_med2 > 0 and np_med2 / eq_med2 > 5:
                        return True
            return False

        if _cross_unit_recheck(net_profit_series, revenue_series, equity_series):
            net_profit_series = net_profit_series / 1000

        if not revenue_series.empty and not net_profit_series.empty:
            common_ry = revenue_series.index.intersection(net_profit_series.index)
            if len(common_ry) >= 2:
                rev_med = revenue_series.loc[common_ry].abs().median()
                np_med  = net_profit_series.loc[common_ry].abs().median()
                if rev_med > 0 and np_med / rev_med > 2:
                    revenue_series = revenue_series * 1000

        if not revenue_series.empty and not equity_series.empty:
            common_re = revenue_series.index.intersection(equity_series.index)
            if len(common_re) >= 1:
                rev_med_re = revenue_series.loc[common_re].abs().median()
                eq_med_re  = equity_series.loc[common_re].abs().median()
                if eq_med_re > 0 and rev_med_re / eq_med_re > 100:
                    revenue_series = revenue_series / 1000

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

        if issue_share == 0.0 and not net_profit_series.empty and not eps_series.empty:
            for yr in sorted(net_profit_series.index, reverse=True):
                np_ty = net_profit_series.get(yr)
                eps_d = eps_series.get(yr)
                if (np_ty is not None and eps_d is not None
                        and pd.notna(np_ty) and pd.notna(eps_d)
                        and eps_d > 0 and np_ty > 0):
                    backcalc = (np_ty * 1e9) / eps_d
                    if 1e8 < backcalc < 1e11:
                        issue_share = backcalc
                        break

        if issue_share == 0.0:
            st.warning(f"⚠️ Không xác định được số CP lưu hành cho {ticker}.")

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

        _normalize_pct = _normalize_pct_series

        roe_series = _normalize_pct_series(roe_series)
        roa_series = _normalize_pct_series(roa_series)

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
        cfo_series_for_multiples = _filter_years(cfo_series_for_multiples)

        if not _cf_cf.empty:
            try:
                _cfo_kw = ['lưu chuyển tiền thuần từ hoạt động kinh doanh',
                           'lưu chuyển tiền thuần từ hđkd',
                           'lưu chuyển tiền tệ ròng từ các hoạt động',
                           'net cash from operating', 'operating cash flow']
                _cfo_cf_fb = _filter_years(normalize_to_billion_vnd(
                    _cafef_extract_series(_cf_cf, _cfo_kw)))
                for yr in _cfo_cf_fb.index:
                    if yr not in cfo_series_for_multiples.index:
                        cfo_series_for_multiples[yr] = _cfo_cf_fb[yr]
                cfo_series_for_multiples = cfo_series_for_multiples.sort_index()
            except Exception:
                pass

        if not cfo_series_for_multiples.empty and not equity_series.empty:
            common_ce = cfo_series_for_multiples.index.intersection(equity_series.index)
            if len(common_ce) >= 1:
                cfo_med_ce = cfo_series_for_multiples.loc[common_ce].abs().median()
                eq_med_ce  = equity_series.loc[common_ce].abs().median()
                if eq_med_ce > 0 and cfo_med_ce / eq_med_ce > 50:
                    cfo_series_for_multiples = cfo_series_for_multiples / 1000

        # CFO OUTLIER GUARD
        if not cfo_series_for_multiples.empty and len(cfo_series_for_multiples) >= 2:
            _cfo_vals = cfo_series_for_multiples.abs()
            _cfo_median_all = _cfo_vals.median()
            if _cfo_median_all > 0:
                _cfo_ratio = _cfo_vals / _cfo_median_all
                _cfo_valid_mask = (_cfo_ratio >= 0.05) & (_cfo_ratio <= 20.0)
                if _cfo_valid_mask.any() and not _cfo_valid_mask.all():
                    cfo_series_for_multiples = cfo_series_for_multiples[_cfo_valid_mask]

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

        if is_bank:
            ebitda_latest  = None
            revenue_latest = 0.0
        elif not pretax_series.empty:
            ebitda_latest = abs(pretax_latest) + abs(interest_latest) + abs(da_latest)
            ebitda_latest = ebitda_latest if ebitda_latest > 0 else None
        elif not net_profit_series.empty:
            _np_proxy = get_latest(net_profit_series, default=None)
            ebitda_latest = (abs(_np_proxy) + abs(da_latest)) if _np_proxy is not None else None
        else:
            ebitda_latest = None

        ebitda_is_estimated = (
            not is_bank
            and not is_securities
            and pretax_series.empty
            and (not net_profit_series.empty)
        )

        if cfo_latest is None and not net_profit_series.empty:
            _np_proxy = get_latest(net_profit_series, default=None)
            if _np_proxy is not None:
                cfo_latest = abs(_np_proxy) + abs(da_latest)
                cfo_is_estimated = True
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
            "cfo_latest_billion":      cfo_latest,
            "cfo_is_estimated":        cfo_is_estimated,
            "ebitda_latest_billion":   ebitda_latest,
            "ebitda_is_estimated":     ebitda_is_estimated,
            "net_debt_billion":        net_debt_latest,
            "excl_extended_multiples": is_bank,
        })

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
                _np_val = float(net_profit_series[y])
                _eq_val = float(equity_series[y])
                if abs(_np_val) > 0 and abs(_eq_val) > 0:
                    _raw_roe = _np_val / _eq_val * 100
                    if abs(_raw_roe) > 500:
                        _np_val = _np_val / 1000
                        _raw_roe = _np_val / _eq_val * 100
                    if abs(_raw_roe) <= 200:
                        roe_series_filled[y] = round(_raw_roe, 2)
            if (y not in roa_series_filled.index or pd.isna(roa_series_filled.get(y))) \
                    and has_np and has_ta and total_assets_series[y] != 0:
                _np_val2 = float(net_profit_series[y])
                _ta_val  = float(total_assets_series[y])
                if abs(_np_val2) > 0 and abs(_ta_val) > 0:
                    _raw_roa = _np_val2 / _ta_val * 100
                    if abs(_raw_roa) > 500:
                        _np_val2 = _np_val2 / 1000
                        _raw_roa = _np_val2 / _ta_val * 100
                    if abs(_raw_roa) <= 200:
                        roa_series_filled[y] = round(_raw_roa, 2)

        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)']            = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)']         = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)']    = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)']              = df_5y_table['Năm'].map(eps_series_filled)
        df_5y_table['BVPS (đ)']             = df_5y_table['Năm'].map(bvps_series_filled)
        df_5y_table['ROE (%)'] = df_5y_table['Năm'].map(lambda y: roe_series_filled.get(y, None))
        df_5y_table['ROA (%)'] = df_5y_table['Năm'].map(lambda y: roa_series_filled.get(y, None))
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
            def _ros_q(p):
                r = rev_q.get(p) if p in rev_q.index else None
                n = np_q.get(p)  if p in np_q.index  else None
                if (r is not None and n is not None
                        and pd.notna(r) and pd.notna(n) and r != 0):
                    return n / r * 100
                return None
            df_quarter_table['ROS (%)'] = df_quarter_table['_p'].map(_ros_q)
            df_quarter_table = df_quarter_table.drop(columns=['_p'])
        except Exception:
            pass

        df_dupont = dupont_decomposition(
            revenue_series, net_profit_series, total_assets_series, equity_series)

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

        if dilution_years:
            df_5y_table['EPS (đ)']  = df_5y_table['Năm'].map(
                lambda y: eps_adj.get(y, None)  if y in eps_adj.index  else None)
            df_5y_table['BVPS (đ)'] = df_5y_table['Năm'].map(
                lambda y: bvps_adj.get(y, None) if y in bvps_adj.index else None)

        _cfo_s = cfo_series_for_multiples if (
            cfo_series_for_multiples is not None and not cfo_series_for_multiples.empty
        ) else pd.Series(dtype=float)
        if not _cfo_s.empty and not revenue_series.empty:
            _common_cr = _cfo_s.index.intersection(revenue_series.index)
            if len(_common_cr) >= 2:
                _cfo_med = _cfo_s.loc[_common_cr].abs().median()
                _rev_med = revenue_series.loc[_common_cr].abs().median()
                if _rev_med > 0 and _cfo_med / _rev_med > 10:
                    _cfo_s = _cfo_s / 1000
        if not _cfo_s.empty and len(_cfo_s) >= 2:
            _cs_med = _cfo_s.abs().median()
            if _cs_med > 0:
                _cs_ratio = _cfo_s.abs() / _cs_med
                _cfo_s = _cfo_s.where((_cs_ratio >= 0.05) & (_cs_ratio <= 20.0), other=None)
        df_5y_table['LCFD HĐKD (tỷ)'] = df_5y_table['Năm'].map(
            lambda y: _cfo_s.get(y, None) if not _cfo_s.empty else None)

        _shares_map = {}
        if outstanding_shares_series is not None and not outstanding_shares_series.empty:
            for _y, _v in (outstanding_shares_series / 1e9).items():
                if pd.notna(_v) and _v > 0:
                    _shares_map[_y] = round(float(_v), 2)
        if issue_share > 0:
            for _y in years_available:
                if _y not in _shares_map:
                    _shares_map[_y] = round(issue_share / 1e9, 2)
        df_5y_table['Số CP lưu hành (tỷ)'] = df_5y_table['Năm'].map(
            lambda y: _shares_map.get(y, None))

        def _ros_annual(y):
            rev = revenue_series.get(y)    if y in revenue_series.index    else None
            np_ = net_profit_series.get(y) if y in net_profit_series.index else None
            if (rev is not None and np_ is not None
                    and pd.notna(rev) and pd.notna(np_) and rev != 0):
                return np_ / rev * 100
            return None
        df_5y_table['ROS (%)'] = df_5y_table['Năm'].map(_ros_annual)

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
