"""
pipeline_helpers.py
────────────────────
Các hằng số + hàm dùng chung (fetch nhiều nguồn, chuẩn hoá đơn vị, xây dựng
engine vnstock có fallback...) được tách ra từ equity_pipeline.py để file
chính đỡ cồng kềnh. equity_pipeline.py import các tên cần dùng từ đây.
"""
import pandas as pd
import numpy as np
# BYPASS vnai hard-cap 4 kỳ
# QUAN TRỌNG: Import Finance/Quote/Company TRƯỚC, rồi mới unpatch
# Lý do: vnai._ensure_patches_applied() chỉ chạy 1 lần (có guard).
# Nếu unpatch trước khi trigger guard, vnai sẽ re-patch lại sau đó.
from datetime import datetime, timedelta
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

    FIX 1: Thêm per-value magnitude check để tránh partial-year data
    kéo median xuống thấp gây scale sai cho năm hiện tại.
    """
    if series is None or series.empty:
        return series
    numeric = pd.to_numeric(series, errors='coerce').dropna()
    if numeric.empty:
        return series

    # Dùng max thay vì median để tránh bị kéo thấp bởi partial-year data (năm hiện tại)
    max_abs = numeric.abs().max()
    median_abs = numeric.abs().median()

    # Ưu tiên median khi có >= 3 giá trị (ổn định hơn);
    # fallback sang max khi chỉ có 1-2 giá trị (hay gặp khi năm hiện tại partial)
    ref_val = median_abs if len(numeric) >= 3 else max_abs

    if ref_val > 5e11:
        divisor = 1e9
    elif ref_val > 5e5:
        divisor = 1e3
    else:
        divisor = 1.0

    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            v = float(val)
            # FIX 1b: Per-value magnitude override — nếu giá trị đơn lẻ
            # rõ ràng thuộc đơn vị khác với divisor đã chọn, scale riêng.
            # Tránh trường hợp 4 năm ở tỷ, năm 2025 ở đồng → median chọn tỷ
            # nhưng giá trị 2025 phải chia thêm.
            abs_v = abs(v)
            if abs_v > 5e11:
                return round(v / 1e9, 2)
            elif abs_v > 5e5:
                return round(v / 1e3, 2)
            else:
                return round(v / divisor, 2) if divisor != 1.0 else round(v, 2)
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

    ⚠️ vnstock ratio() trả 'Số CP lưu hành' theo đơn vị TRIỆU CP (vd: 3610 = 3.61 tỷ).
    Guard: nếu median < 1e8 → nhân 1e6 để ra đơn vị cổ phiếu lẻ.
    """
    result = {}
    if outstanding_shares_series is not None and not outstanding_shares_series.empty:
        _sh = outstanding_shares_series.dropna()
        _median_sh = _sh.median() if not _sh.empty else 0
        # Nếu đơn vị triệu (median < 1e8) → nhân 1e6 → đơn vị lẻ
        _sh_multiplier = 1e6 if (0 < _median_sh < 1e8) else 1.0
        for yr, val in outstanding_shares_series.items():
            if pd.notna(val) and val > 0:
                result[yr] = float(val) * _sh_multiplier
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
    Lưu ý: mã .VN trên Yahoo trả về đơn vị VNĐ (không phải USD).
    Trả về dict: {field: pd.Series(index=year_int, dtype=float, đơn vị tỷ VNĐ)}
    """
    result = {
        "revenue":      {},
        "net_profit":   {},
        "equity":       {},
        "total_assets": {},
    }
    try:
        import yfinance as yf
        yf_ticker = ticker.upper() + ".VN"
        obj = yf.Ticker(yf_ticker)

        def _vnd_to_ty(val):
            """Mã .VN: Yahoo báo cáo đơn vị VNĐ lẻ → chia 1e9 ra tỷ."""
            if val is None or (isinstance(val, float) and __import__('math').isnan(val)):
                return None
            v = float(val)
            if abs(v) > 1e10:        # đang ở đồng VNĐ
                return round(v / 1e9, 2)
            if abs(v) > 1e7:         # đang ở triệu
                return round(v / 1e3, 2)
            return round(v, 2)       # đã ở tỷ

        # ── Income statement (annual) ──
        try:
            inc = obj.financials
            if inc is not None and not inc.empty:
                for col in inc.columns:
                    try:
                        yr = int(str(col)[:4])
                    except Exception:
                        continue
                    if yr not in allowed_years:
                        continue
                    for kw in ["Total Revenue", "Revenue"]:
                        if kw in inc.index:
                            v = _vnd_to_ty(inc.loc[kw, col])
                            if v is not None and v > 0:
                                result["revenue"][yr] = v
                                break
                    for kw in ["Net Income Common Stockholders", "Net Income",
                               "Net Income Applicable To Common Shares"]:
                        if kw in inc.index:
                            v = _vnd_to_ty(inc.loc[kw, col])
                            if v is not None:
                                result["net_profit"][yr] = v
                                break
        except Exception:
            pass

        # ── Balance sheet (annual) ──
        try:
            bs = obj.balance_sheet
            if bs is not None and not bs.empty:
                for col in bs.columns:
                    try:
                        yr = int(str(col)[:4])
                    except Exception:
                        continue
                    if yr not in allowed_years:
                        continue
                    for kw in ["Stockholders Equity", "Common Stock Equity",
                               "Total Equity Gross Minority Interest"]:
                        if kw in bs.index:
                            v = _vnd_to_ty(bs.loc[kw, col])
                            if v is not None and v > 0:
                                result["equity"][yr] = v
                                break
                    for kw in ["Total Assets"]:
                        if kw in bs.index:
                            v = _vnd_to_ty(bs.loc[kw, col])
                            if v is not None and v > 0:
                                result["total_assets"][yr] = v
                                break
        except Exception:
            pass

    except ImportError:
        pass
    except Exception:
        pass

    return {k: pd.Series(v, dtype=float) for k, v in result.items()}
