"""
website_scraper.py
──────────────────
Cào dữ liệu tài chính 5 năm trực tiếp từ các nguồn:

  Tầng 1 — Vietstock Finance API (JSON, không cần login, đầy đủ nhất)
  Tầng 2 — CafeF HTML scraping (fallback khi Vietstock fail)
  Tầng 3 — Stockbiz HTML scraping (fallback thứ 3)
  Tầng 4 — HOSE/HNX disclosure (XML, chỉ dùng cho báo cáo kiểm toán)

Contract trả về (cùng format với cafef_fallback.py):
    {
        "income_statement": pd.DataFrame,   # index = tên chỉ tiêu, columns = năm string "2021"…
        "balance_sheet":    pd.DataFrame,
        "cash_flow":        pd.DataFrame,
    }

Mỗi DataFrame:
  - index   : tên chỉ tiêu tiếng Việt (VD: "Doanh thu thuần")
  - columns : năm dạng str (VD: "2021", "2022", "2023", "2024", "2025")
  - values  : đơn vị TỶ VNĐ (float)

Không import bất kỳ module nội bộ nào trong project.
"""

from __future__ import annotations

import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Shared session & helpers
# ──────────────────────────────────────────────────────────────────────────────

_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_SESSION = requests.Session()
_SESSION.headers.update(_HEADERS_BASE)


def _get(url: str, *, referer: str = "", params: dict | None = None,
         json_mode: bool = False, timeout: int = 12) -> requests.Response | None:
    """Safe GET với retry 1 lần."""
    headers = {}
    if referer:
        headers["Referer"] = referer
    if json_mode:
        headers["Accept"] = "application/json, text/plain, */*"
    try:
        r = _SESSION.get(url, headers=headers, params=params, timeout=timeout)
        if r.status_code == 429:
            time.sleep(2)
            r = _SESSION.get(url, headers=headers, params=params, timeout=timeout)
        return r if r.status_code < 500 else None
    except Exception as e:
        logger.debug("GET %s error: %s", url, e)
        return None


def _to_billion(val) -> float | None:
    """Chuyển giá trị về tỷ VNĐ — tự detect đơn vị."""
    if val is None:
        return None
    try:
        v = float(str(val).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None
    if pd.isna(v):
        return None
    # Heuristic: nếu lớn hơn 5 tỷ tỷ → đơn vị đồng → chia 1e9
    #            nếu lớn hơn 5 triệu   → đơn vị triệu → chia 1e3
    abs_v = abs(v)
    if abs_v > 5e12:
        return round(v / 1e9, 2)
    if abs_v > 5e6:
        return round(v / 1e3, 2)
    return round(v, 2)


def _year_from_str(s: str) -> str | None:
    """'2021', '2021/12', '12/2021', '31/12/2021' → '2021'."""
    m = re.search(r"\b(20\d{2}|19\d{2})\b", str(s))
    return m.group(1) if m else None


# ──────────────────────────────────────────────────────────────────────────────
# Tầng 1 — Vietstock Finance API
# ──────────────────────────────────────────────────────────────────────────────
# API: https://finance.vietstock.vn/data/financeinfo
#   reporttype: KQKD | CDKT | LCTT
#   reportTermType: 1 = năm, 2 = quý
#   Unit: 1000000000 = tỷ VNĐ
#   Page: 1 (trang 1 = 5 năm gần nhất)

_VS_BASE = "https://finance.vietstock.vn"
_VS_API  = f"{_VS_BASE}/data/financeinfo"
_VS_REPORT_TYPES = {
    "income_statement": "KQKD",
    "balance_sheet":    "CDKT",
    "cash_flow":        "LCTT",
}


def _vietstock_init_session() -> bool:
    """Hit main page để lấy cookies/CSRF."""
    r = _get(_VS_BASE, timeout=10)
    return r is not None and r.status_code == 200


def _vietstock_fetch_report(ticker: str, report_key: str, n_years: int = 5) -> pd.DataFrame:
    """
    Lấy một loại báo cáo từ Vietstock Finance API.

    Vietstock trả JSON dạng:
      { "data": [ { "TermName": "2024", "Rows": [ {"Name": "...", "Value": ...}, ... ] }, ... ] }
    hoặc dạng tabular khác tuỳ endpoint.
    """
    rtype = _VS_REPORT_TYPES.get(report_key)
    if not rtype:
        return pd.DataFrame()

    pages = 2 if n_years > 5 else 1
    dfs = []

    for page in range(1, pages + 1):
        params = {
            "code":           ticker.upper(),
            "reporttype":     rtype,
            "reportTermType": "1",           # năm
            "Unit":           "1000000000",  # tỷ VNĐ
            "Page":           str(page),
            "PageSize":       "5",
            "Audited":        "0",
            "AuditedStatus":  "3",
            "FY":             "2025",
        }
        r = _get(_VS_API, referer=f"{_VS_BASE}/{ticker}/tai-chinh.htm",
                 params=params, json_mode=True)
        if r is None or r.status_code != 200:
            continue
        try:
            payload = r.json()
        except Exception:
            continue

        # Vietstock có thể wrap trong key khác nhau tuỳ phiên bản API
        rows_data = (
            payload.get("data")
            or payload.get("Data")
            or payload.get("result")
            or []
        )
        if not rows_data:
            # Thử parse dạng list trực tiếp
            if isinstance(payload, list):
                rows_data = payload

        df = _parse_vietstock_json(rows_data)
        if not df.empty:
            dfs.append(df)

    if not dfs:
        return pd.DataFrame()
    if len(dfs) == 1:
        return dfs[0]

    # Merge 2 trang: outer join trên index (tên chỉ tiêu)
    merged = dfs[0].join(dfs[1], how="outer", lsuffix="", rsuffix="_p2")
    dup = [c for c in merged.columns if c.endswith("_p2")]
    return merged.drop(columns=dup)


def _parse_vietstock_json(rows_data: list) -> pd.DataFrame:
    """
    Chuyển JSON Vietstock → DataFrame.

    Hỗ trợ 2 format phổ biến:
      Format A: [{"TermName":"2024", "Rows":[{"Name":"...", "Value":...},...]}]
      Format B: [{"Name":"...", "2024": ..., "2023": ..., ...}]
    """
    if not rows_data:
        return pd.DataFrame()

    first = rows_data[0]

    # Format A — grouped by term (năm)
    if "TermName" in first and "Rows" in first:
        year_data: dict[str, dict] = {}
        for term in rows_data:
            yr = _year_from_str(str(term.get("TermName", "")))
            if not yr:
                continue
            for row in term.get("Rows", []):
                name = str(row.get("Name", "")).strip()
                val  = _to_billion(row.get("Value"))
                if name and val is not None:
                    year_data.setdefault(yr, {})[name] = val
        if not year_data:
            return pd.DataFrame()
        df = pd.DataFrame(year_data)  # index=tên chỉ tiêu, columns=năm
        df.index.name = "Chỉ tiêu"
        return df

    # Format B — one row per chỉ tiêu, columns = năm
    if "Name" in first:
        records = {}
        for row in rows_data:
            name = str(row.get("Name", "")).strip()
            if not name:
                continue
            yr_vals = {}
            for k, v in row.items():
                if k == "Name":
                    continue
                yr = _year_from_str(str(k))
                if yr:
                    converted = _to_billion(v)
                    if converted is not None:
                        yr_vals[yr] = converted
            if yr_vals:
                records[name] = yr_vals
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records).T
        df.index.name = "Chỉ tiêu"
        return df

    return pd.DataFrame()


def fetch_vietstock_yearly_full(ticker: str, n_years: int = 5) -> dict[str, pd.DataFrame]:
    """
    Lấy 3 BCTC theo năm từ Vietstock Finance.

    Returns:
        {"income_statement": df, "balance_sheet": df, "cash_flow": df}
        DataFrame trống nếu thất bại.
    """
    _vietstock_init_session()

    result: dict[str, pd.DataFrame] = {}

    def _fetch(key: str) -> tuple[str, pd.DataFrame]:
        return key, _vietstock_fetch_report(ticker, key, n_years=n_years)

    with ThreadPoolExecutor(max_workers=3) as exe:
        futures = {exe.submit(_fetch, k): k for k in _VS_REPORT_TYPES}
        for future in as_completed(futures):
            key, df = future.result()
            result[key] = df

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Tầng 2 — CafeF HTML scraping
# ──────────────────────────────────────────────────────────────────────────────
# Trang: https://cafef.vn/bao-cao-tai-chinh-{ticker}-ct.chn
# Chứa 3 bảng (KQKD, CDKT, LCTT) mỗi bảng 5 năm

_CF_REPORT_URL = "https://cafef.vn/bao-cao-tai-chinh-{ticker}-ct.chn"

# Map section title → report key
_CF_SECTION_MAP = {
    "kết quả kinh doanh": "income_statement",
    "cân đối kế toán":    "balance_sheet",
    "lưu chuyển tiền tệ": "cash_flow",
    "kqkd":               "income_statement",
    "cdkt":               "balance_sheet",
    "lctt":               "cash_flow",
}


def _cafef_html_scrape(ticker: str) -> dict[str, pd.DataFrame]:
    """
    Cào bảng BCTC từ trang HTML CafeF.
    Dùng khi CafeF AJAX API không khả dụng (ví dụ: CORS, block).
    """
    url = _CF_REPORT_URL.format(ticker=ticker.lower())
    r = _get(url, referer="https://cafef.vn/")
    if r is None or r.status_code != 200:
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    result: dict[str, pd.DataFrame] = {}

    # Tìm các bảng BCTC — thường có class 'table-finance' hoặc id chứa 'KQKD'/'CDKT'/'LCTT'
    for section_id, report_key in [
        ("divKQKD", "income_statement"),
        ("divCDKT", "balance_sheet"),
        ("divLCTT", "cash_flow"),
    ]:
        section = soup.find(id=section_id) or soup.find("div", {"data-type": section_id})
        if section is None:
            # Fallback: tìm theo heading
            headings = soup.find_all(["h2", "h3", "h4"])
            for h in headings:
                h_text = h.get_text(strip=True).lower()
                mapped = next((v for k, v in _CF_SECTION_MAP.items() if k in h_text), None)
                if mapped == report_key:
                    section = h.find_next("table")
                    break

        if section is None:
            continue

        table = section.find("table") if section.name != "table" else section
        if table is None:
            continue

        df = _parse_html_table(table)
        if not df.empty:
            result[report_key] = df

    return result


def _parse_html_table(table_tag) -> pd.DataFrame:
    """
    Parse <table> HTML → DataFrame.
    Hàng đầu tiên = header (năm).
    Cột đầu tiên = tên chỉ tiêu.
    """
    rows = table_tag.find_all("tr")
    if len(rows) < 2:
        return pd.DataFrame()

    # Header row
    headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
    # Tìm cột năm
    year_indices: list[tuple[int, str]] = []
    for i, h in enumerate(headers):
        yr = _year_from_str(h)
        if yr:
            year_indices.append((i, yr))

    if not year_indices:
        return pd.DataFrame()

    records: dict[str, dict[str, float]] = {}
    for row in rows[1:]:
        cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
        if not cells:
            continue
        name = cells[0].strip()
        if not name:
            continue
        yr_vals = {}
        for col_idx, yr in year_indices:
            if col_idx >= len(cells):
                continue
            val = _to_billion(cells[col_idx])
            if val is not None:
                yr_vals[yr] = val
        if yr_vals:
            records[name] = yr_vals

    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).T
    df.index.name = "Chỉ tiêu"
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Tầng 3 — Stockbiz HTML scraping
# ──────────────────────────────────────────────────────────────────────────────
# URL: https://www.stockbiz.vn/Stocks/{TICKER}/FinancialStatements.aspx

_SB_BASE = "https://www.stockbiz.vn"
_SB_REPORT_URLS = {
    "income_statement": "{base}/Stocks/{ticker}/FinancialStatements.aspx?type=1",
    "balance_sheet":    "{base}/Stocks/{ticker}/FinancialStatements.aspx?type=2",
    "cash_flow":        "{base}/Stocks/{ticker}/FinancialStatements.aspx?type=3",
}


def _stockbiz_fetch_report(ticker: str, report_key: str) -> pd.DataFrame:
    url = _SB_REPORT_URLS[report_key].format(base=_SB_BASE, ticker=ticker.upper())
    r = _get(url, referer=_SB_BASE)
    if r is None or r.status_code != 200:
        return pd.DataFrame()
    soup = BeautifulSoup(r.text, "html.parser")
    # Stockbiz có table class 'datatable' hoặc id 'ctl00_ContentPlaceHolder1_...'
    table = (
        soup.find("table", class_="datatable")
        or soup.find("table", class_="finance-table")
        or soup.find("table", id=re.compile(r"(grid|table|finance)", re.I))
    )
    if table is None:
        tables = soup.find_all("table")
        # Lấy table lớn nhất (nhiều hàng nhất)
        if tables:
            table = max(tables, key=lambda t: len(t.find_all("tr")))
    if table is None:
        return pd.DataFrame()
    return _parse_html_table(table)


def fetch_stockbiz_yearly_full(ticker: str) -> dict[str, pd.DataFrame]:
    """Lấy 3 BCTC từ Stockbiz."""
    result: dict[str, pd.DataFrame] = {}

    def _fetch(key: str) -> tuple[str, pd.DataFrame]:
        return key, _stockbiz_fetch_report(ticker, key)

    with ThreadPoolExecutor(max_workers=3) as exe:
        futures = {exe.submit(_fetch, k): k for k in _SB_REPORT_URLS}
        for future in as_completed(futures):
            key, df = future.result()
            result[key] = df

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Tầng 4 — Wichart / Wiintelligence API (JSON, đầy đủ chỉ tiêu)
# ──────────────────────────────────────────────────────────────────────────────
# API: https://api.wichart.vn/finance/{ticker}/income-statement?period=year
# Không yêu cầu auth, trả JSON thuần, unit thường là triệu VNĐ

_WI_BASE     = "https://api.wichart.vn"
_WI_REPORTS  = {
    "income_statement": "income-statement",
    "balance_sheet":    "balance-sheet",
    "cash_flow":        "cash-flow",
}


def _wichart_fetch_report(ticker: str, report_key: str) -> pd.DataFrame:
    endpoint = _WI_REPORTS.get(report_key, "")
    url = f"{_WI_BASE}/finance/{ticker.upper()}/{endpoint}"
    r = _get(url, referer="https://wichart.vn/",
             params={"period": "year", "lang": "vi"}, json_mode=True)
    if r is None or r.status_code != 200:
        return pd.DataFrame()
    try:
        payload = r.json()
    except Exception:
        return pd.DataFrame()

    # Wichart format: {"data": [{"year":2024, "items":[{"name":"...", "value":...}]}]}
    items_by_year = payload.get("data") or payload.get("Data") or []
    if not items_by_year:
        if isinstance(payload, list):
            items_by_year = payload

    return _parse_vietstock_json(items_by_year)  # format tương tự, tái dùng parser


def fetch_wichart_yearly_full(ticker: str) -> dict[str, pd.DataFrame]:
    """Lấy 3 BCTC từ Wichart API."""
    result: dict[str, pd.DataFrame] = {}

    def _fetch(key: str) -> tuple[str, pd.DataFrame]:
        return key, _wichart_fetch_report(ticker, key)

    with ThreadPoolExecutor(max_workers=3) as exe:
        futures = {exe.submit(_fetch, k): k for k in _WI_REPORTS}
        for future in as_completed(futures):
            key, df = future.result()
            result[key] = df

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Public API tổng hợp — Cascade qua 4 tầng
# ──────────────────────────────────────────────────────────────────────────────

_EMPTY_RESULT: dict[str, pd.DataFrame] = {
    "income_statement": pd.DataFrame(),
    "balance_sheet":    pd.DataFrame(),
    "cash_flow":        pd.DataFrame(),
}


def _merge_results(base: dict[str, pd.DataFrame],
                   extra: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """
    Merge hai dict kết quả: với mỗi report_key, fill cột năm còn thiếu từ extra.
    Ưu tiên giữ giá trị base (nguồn đáng tin hơn).
    """
    merged = {}
    for key in ("income_statement", "balance_sheet", "cash_flow"):
        df_b = base.get(key, pd.DataFrame())
        df_e = extra.get(key, pd.DataFrame())
        if df_b.empty:
            merged[key] = df_e
        elif df_e.empty:
            merged[key] = df_b
        else:
            # Merge: giữ base, thêm cột năm mới từ extra
            new_cols = [c for c in df_e.columns if c not in df_b.columns]
            if new_cols:
                # Align trên index (tên chỉ tiêu)
                extra_sub = df_e[new_cols].reindex(df_b.index)
                merged[key] = pd.concat([df_b, extra_sub], axis=1)
            else:
                merged[key] = df_b
    return merged


def _count_years(result: dict[str, pd.DataFrame]) -> int:
    """Đếm số năm có dữ liệu trong result."""
    years: set[str] = set()
    for df in result.values():
        if not df.empty:
            years.update(str(c) for c in df.columns if _year_from_str(str(c)))
    return len(years)


def fetch_website_financial_data(ticker: str, n_years: int = 5,
                                 required_years: set[int] | None = None) -> dict[str, pd.DataFrame]:
    """
    Lấy BCTC 5 năm từ website bên ngoài theo cascade:
      1. Vietstock Finance API   — đầy đủ nhất, JSON sạch
      2. CafeF HTML scraping     — fallback khi Vietstock fail
      3. Stockbiz HTML scraping  — fallback thứ 3
      4. Wichart API             — fallback cuối cùng

    Args:
        ticker       : mã CK (VD: "VNM")
        n_years      : số năm cần lấy (default 5)
        required_years: set năm cần có (VD: {2021,2022,2023,2024,2025}).
                        Nếu None → lấy hết và trả về.

    Returns:
        dict với 3 key: "income_statement", "balance_sheet", "cash_flow"
        Mỗi value là DataFrame (có thể rỗng nếu toàn bộ nguồn thất bại).
    """
    result = dict(_EMPTY_RESULT)  # copy

    # ── Tầng 1: Vietstock
    try:
        vs = fetch_vietstock_yearly_full(ticker, n_years=n_years)
        result = _merge_results(result, vs)
    except Exception as e:
        logger.debug("Vietstock failed for %s: %s", ticker, e)

    if required_years and _count_years(result) >= len(required_years):
        return result

    # ── Tầng 2: CafeF HTML
    try:
        cf = _cafef_html_scrape(ticker)
        result = _merge_results(result, cf)
    except Exception as e:
        logger.debug("CafeF HTML failed for %s: %s", ticker, e)

    if required_years and _count_years(result) >= len(required_years):
        return result

    # ── Tầng 3: Stockbiz
    try:
        sb = fetch_stockbiz_yearly_full(ticker)
        result = _merge_results(result, sb)
    except Exception as e:
        logger.debug("Stockbiz failed for %s: %s", ticker, e)

    if required_years and _count_years(result) >= len(required_years):
        return result

    # ── Tầng 4: Wichart
    try:
        wi = fetch_wichart_yearly_full(ticker)
        result = _merge_results(result, wi)
    except Exception as e:
        logger.debug("Wichart failed for %s: %s", ticker, e)

    return result
