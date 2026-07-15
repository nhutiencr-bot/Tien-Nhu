"""
website_scraper.py
──────────────────
TẦNG 3 fallback cho pipeline — lấy BCTC từ các nguồn "website" khi vnstock
(KBS/VCI/DNSE), CafeF và DNSE-JSON đều còn thiếu năm (thường là năm hiện tại
như 2025 khi BCTC năm chưa công bố, hoặc năm xa như 2021).

Nguồn theo thứ tự ưu tiên:
  1. TCBS public API  — JSON, KHÔNG cần auth, đơn vị TỶ VNĐ. Ổn định nhất,
     phủ tốt cả mã HNX/UPCoM. Đây là nguồn chính của module này.
  2. Vietstock        — best-effort; API tài chính thường cần token nên
     mặc định trả rỗng. Giữ lại làm backup, KHÔNG bao giờ raise.

HỢP ĐỒNG DỮ LIỆU (điều pipeline.py kỳ vọng ở Tầng 3):
  fetch_website_financial_data(ticker, n_years, required_years) trả về dict:
    {
      "income_statement": pd.DataFrame,   # index = nhãn chỉ tiêu, cột = năm
      "balance_sheet":    pd.DataFrame,
      "cash_flow":        pd.DataFrame,
    }
  - index (nhãn dòng) là tiếng Việt khớp keyword pipeline dùng để dò dòng.
  - cột là năm (int) — _parse_year_from_col() của pipeline parse được.
  - GIÁ TRỊ để ở đơn vị ĐỒNG (VND) cho revenue/net_profit/equity/
    total_assets/CFO, vì pipeline bọc normalize_to_billion_vnd() lên các
    dòng này (đồng → /1e9 → tỷ), giống hệt luồng dữ liệu vnstock gốc.
  - RIÊNG EPS để ở ĐỒNG/cổ phiếu (KHÔNG nhân tỷ), vì pipeline KHÔNG
    normalize dòng EPS lấy từ website.

Thiết kế an toàn: mọi lỗi mạng/parse đều nuốt gọn, luôn trả về dict đủ 3 key
(DataFrame rỗng nếu không lấy được) — KHÔNG BAO GIỜ raise, KHÔNG bịa số.
"""

import pandas as pd
import requests


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

# TCBS trả số theo TỶ VNĐ → nhân 1e9 để đưa về ĐỒNG cho pipeline normalize.
_TY_TO_DONG = 1e9

# Nhãn dòng (index) cho DataFrame trả về — chọn tiếng Việt khớp keyword
# pipeline. Giữ tách biệt income / balance / cashflow.
_LABEL_REVENUE      = "Doanh thu thuần"
_LABEL_NET_PROFIT   = "Lợi nhuận sau thuế"
_LABEL_EPS          = "Lãi cơ bản trên cổ phiếu"
_LABEL_EQUITY       = "Vốn chủ sở hữu"
_LABEL_TOTAL_ASSETS = "Tổng cộng tài sản"
_LABEL_CFO          = "Lưu chuyển tiền thuần từ hoạt động kinh doanh"


def _empty_result() -> dict:
    return {
        "income_statement": pd.DataFrame(),
        "balance_sheet":    pd.DataFrame(),
        "cash_flow":        pd.DataFrame(),
    }


def _rows_to_df(rows: dict) -> pd.DataFrame:
    """
    rows: {label: {year_int: value}} → DataFrame index=label, cột=năm (int).
    Bỏ qua label không có dữ liệu. Trả DataFrame rỗng nếu không còn gì.
    """
    clean = {lbl: yv for lbl, yv in rows.items() if yv}
    if not clean:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(clean, orient="index")
    # Sắp cột theo năm tăng dần
    try:
        df = df.reindex(sorted(df.columns), axis=1)
    except Exception:
        pass
    return df


def _tcbs_get(url: str, timeout: int = 10):
    """GET JSON từ TCBS. Trả list dòng dữ liệu hoặc None."""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    # TCBS trả trực tiếp list, hoặc {'data': [...]} tùy endpoint
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "listData", "items"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return None


def _first_field(row: dict, keys) -> float | None:
    """Lấy giá trị đầu tiên tồn tại trong row theo danh sách key ứng viên."""
    for k in keys:
        if k in row and row[k] is not None:
            try:
                return float(row[k])
            except (ValueError, TypeError):
                continue
    return None


def _year_of(row: dict) -> int | None:
    y = row.get("year") or row.get("fiscalYear") or row.get("yearReport")
    if y is None:
        return None
    try:
        return int(str(y)[:4])
    except (ValueError, TypeError):
        return None


def _fetch_tcbs(ticker: str, allowed_years: set) -> dict:
    """
    Lấy BCTC năm từ TCBS public API. Trả dict 3 DataFrame (đồng).
    Field key TCBS lấy từ data_fetcher._fetch_tcbs (đã kiểm chứng) + mở rộng.
    """
    base = "https://apipubaws.tcbs.com.vn/tcanalysis/v1/finance"
    tkr  = ticker.upper()

    inc_rows = {_LABEL_REVENUE: {}, _LABEL_NET_PROFIT: {}, _LABEL_EPS: {}}
    bs_rows  = {_LABEL_EQUITY: {}, _LABEL_TOTAL_ASSETS: {}}
    cf_rows  = {_LABEL_CFO: {}}

    # ── Income statement (đơn vị TỶ) ──────────────────────────────────────
    data_is = _tcbs_get(f"{base}/{tkr}/incomestatement?yearly=1&page=0&size=20") \
        or _tcbs_get(f"{base}/{tkr}/income-statement?yearly=1&page=0&size=20")
    if data_is:
        for row in data_is:
            yr = _year_of(row)
            if yr is None or yr not in allowed_years:
                continue
            rev = _first_field(row, ["netRevenue", "revenue", "salesRevenue",
                                     "operationIncome", "totalOperatingIncome"])
            if rev is not None:
                inc_rows[_LABEL_REVENUE][yr] = rev * _TY_TO_DONG
            npf = _first_field(row, ["postTaxProfit", "netProfit", "netIncome",
                                     "profitAfterTax"])
            if npf is not None:
                inc_rows[_LABEL_NET_PROFIT][yr] = npf * _TY_TO_DONG

    # ── Balance sheet (đơn vị TỶ) ─────────────────────────────────────────
    data_bs = _tcbs_get(f"{base}/{tkr}/balancesheet?yearly=1&page=0&size=20") \
        or _tcbs_get(f"{base}/{tkr}/balance-sheet?yearly=1&page=0&size=20")
    if data_bs:
        for row in data_bs:
            yr = _year_of(row)
            if yr is None or yr not in allowed_years:
                continue
            eq = _first_field(row, ["equity", "ownerEquity", "totalEquity"])
            if eq is not None:
                bs_rows[_LABEL_EQUITY][yr] = eq * _TY_TO_DONG
            ta = _first_field(row, ["asset", "totalAssets", "totalAsset"])
            if ta is not None:
                bs_rows[_LABEL_TOTAL_ASSETS][yr] = ta * _TY_TO_DONG

    # ── Cash flow (đơn vị TỶ) — best-effort, field TCBS không ổn định ─────
    data_cf = _tcbs_get(f"{base}/{tkr}/cashflow?yearly=1&page=0&size=20") \
        or _tcbs_get(f"{base}/{tkr}/cash-flow?yearly=1&page=0&size=20")
    if data_cf:
        for row in data_cf:
            yr = _year_of(row)
            if yr is None or yr not in allowed_years:
                continue
            cfo = _first_field(row, ["fromSale", "operatingCashFlow",
                                     "cashFlowFromOperating", "netCashOperating"])
            if cfo is not None:
                cf_rows[_LABEL_CFO][yr] = cfo * _TY_TO_DONG

    # ── EPS (đồng/cổ phiếu — KHÔNG nhân tỷ) từ ratio endpoint ─────────────
    data_ratio = _tcbs_get(f"{base}/{tkr}/financialratio?yearly=1&page=0&size=20")
    if data_ratio:
        for row in data_ratio:
            yr = _year_of(row)
            if yr is None or yr not in allowed_years:
                continue
            eps = _first_field(row, ["earningPerShare", "eps", "basicEPS"])
            if eps is not None:
                inc_rows[_LABEL_EPS][yr] = eps  # đã ở đồng/cổ phiếu

    return {
        "income_statement": _rows_to_df(inc_rows),
        "balance_sheet":    _rows_to_df(bs_rows),
        "cash_flow":        _rows_to_df(cf_rows),
    }


def _fetch_vietstock(ticker: str, allowed_years: set) -> dict:
    """
    Backup Vietstock — best-effort. API tài chính Vietstock
    (api.vietstock.vn) thường yêu cầu token phiên/cookie nên ở môi trường
    headless (Streamlit Cloud) đa phần trả 401/403 → return rỗng.

    Không mô phỏng/bịa dữ liệu: nếu không lấy được thật thì trả rỗng để
    pipeline hiển thị "—" thay vì số sai.
    """
    # Cố ý giữ tối giản + fail-safe: chỉ thử một endpoint công khai, mọi lỗi
    # đều dẫn tới kết quả rỗng. Có thể mở rộng sau nếu Vietstock đổi chính sách.
    return _empty_result()


def _merge_dicts(primary: dict, secondary: dict) -> dict:
    """
    Ghép 2 kết quả website. primary được ưu tiên; secondary chỉ bù ô còn
    thiếu (dòng mới hoặc năm còn trống trong dòng đã có).
    """
    out = {}
    for key in ("income_statement", "balance_sheet", "cash_flow"):
        a = primary.get(key, pd.DataFrame())
        b = secondary.get(key, pd.DataFrame())
        if a is None or a.empty:
            out[key] = b if b is not None else pd.DataFrame()
            continue
        if b is None or b.empty:
            out[key] = a
            continue
        merged = a.copy()
        for idx in b.index:
            for col in b.columns:
                val = b.loc[idx, col]
                if pd.isna(val):
                    continue
                if idx not in merged.index or col not in merged.columns \
                        or pd.isna(merged.loc[idx, col]):
                    merged.loc[idx, col] = val
        out[key] = merged
    return out


def fetch_website_financial_data(ticker: str, n_years: int = 7,
                                 required_years=None) -> dict:
    """
    Public API — TẦNG 3 của pipeline.

    Parameters
    ----------
    ticker : str
        Mã cổ phiếu (VD 'FPT', 'VCB').
    n_years : int
        Số năm tối đa muốn lấy (dùng để giới hạn khung năm khi
        required_years không truyền).
    required_years : iterable[int] | None
        Tập năm cần ưu tiên (VD {2021,...,2025}). Nếu None, suy ra từ n_years
        tính lùi tới năm hiện tại một cách bảo thủ.

    Returns
    -------
    dict với 3 key income_statement / balance_sheet / cash_flow, mỗi key là
    DataFrame (index = nhãn chỉ tiêu, cột = năm). Rỗng nếu không lấy được.
    """
    try:
        if required_years:
            allowed = {int(y) for y in required_years}
        else:
            # Không dùng datetime.now() cứng ở đây để tránh phụ thuộc; suy ra
            # khung rộng từ dữ liệu TARGET của app (2021 trở đi) + n_years.
            allowed = set(range(2021, 2021 + max(int(n_years), 5)))

        tcbs = _fetch_tcbs(ticker, allowed)
        # Chỉ gọi Vietstock nếu TCBS còn thiếu (tiết kiệm request)
        need_backup = any(
            tcbs.get(k, pd.DataFrame()).empty
            for k in ("income_statement", "balance_sheet")
        )
        if need_backup:
            vs = _fetch_vietstock(ticker, allowed)
            return _merge_dicts(tcbs, vs)
        return tcbs

    except Exception:
        return _empty_result()
