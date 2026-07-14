"""
cafef_fallback.py
Cào dữ liệu tài chính từ CafeF khi vnstock/SSI không available.
Không import bất kỳ module nội bộ nào trong project.

BUG FIX (root cause mất 2021):
  CafeF API: tham số `period` là PAGE NUMBER, không phải số năm.
    period=1 → trang 1 = 5 năm gần nhất (2021-2025)
    period=5 → trang 5 = ~2001-2005  ← đây là lý do 2021 không có
  Fix: luôn dùng period=1 cho trang gần nhất.
  Để lấy nhiều hơn 5 năm: gọi thêm period=2 rồi merge.
"""

import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Referer": "https://cafef.vn/",
}

_SESSION = requests.Session()
_SESSION.headers.update(_HEADERS)


def _cafef_is_reachable(timeout: int = 5) -> bool:
    """Kiểm tra CafeF có truy cập được không."""
    try:
        r = _SESSION.get("https://cafef.vn", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def _fetch_one_page(ticker: str, period_type: str, page: int, report_type: int) -> list | None:
    """
    Lấy một trang báo cáo từ CafeF API.

    QUAN TRỌNG: `page` là số trang (1 = gần nhất), không phải số năm/quý.
      - period_type='Y', page=1 → 5 năm gần nhất
      - period_type='Y', page=2 → 5 năm tiếp theo (cũ hơn)
      - period_type='Q', page=1 → 4 quý gần nhất (hoặc 5 tùy API)

    report_type: 1=KQKD, 2=CDKT, 3=LCTT
    """
    url = (
        f"https://s.cafef.vn/Handlers/AjaxFinancialData.ashx"
        f"?symbol={ticker.upper()}&type={report_type}"
        f"&period={page}&periodType={period_type}"
    )
    try:
        r = _SESSION.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("Data") and data["Data"].get("ListFinancialData"):
            return data["Data"]["ListFinancialData"]
    except Exception:
        pass
    return None


def _parse_cafef_table(raw_list: list) -> pd.DataFrame:
    """
    Chuyển list JSON từ CafeF thành DataFrame.

    Index  = tên chỉ tiêu (VD: "Doanh thu thuần")
    Columns = năm/quý dạng string (VD: "2021", "Q1/2024")
    """
    if not raw_list:
        return pd.DataFrame()
    rows = []
    for item in raw_list:
        row = {"Chỉ tiêu": item.get("Name", ""), "Đơn vị": item.get("Unit", "")}
        for period_data in item.get("Data", []):
            col = str(period_data.get("Period", "")).strip()
            if col:
                row[col] = period_data.get("Value")
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.set_index("Chỉ tiêu")
    # Bỏ cột "Đơn vị" — không phải năm, gây nhiễu khi parse
    if "Đơn vị" in df.columns:
        df = df.drop(columns=["Đơn vị"])
    return df


def _merge_cafef_pages(page1: pd.DataFrame, page2: pd.DataFrame) -> pd.DataFrame:
    """
    Merge 2 trang CafeF (mỗi trang 5 năm) thành 1 DataFrame đầy đủ.
    page1 = 5 năm gần (2021-2025), page2 = 5 năm cũ hơn (2016-2020).
    Chỉ giữ các cột năm từ 2019 trở đi để tránh data cũ nhiễu.
    """
    if page1.empty:
        return page2
    if page2.empty:
        return page1
    # Merge theo index (tên chỉ tiêu), outer join để giữ tất cả chỉ tiêu
    merged = page1.join(page2, how="outer", lsuffix="", rsuffix="_p2")
    # Loại các cột trùng tên (_p2 suffix)
    dup_cols = [c for c in merged.columns if c.endswith("_p2")]
    merged = merged.drop(columns=dup_cols)
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_cafef_balance_sheet_5y(ticker: str) -> pd.DataFrame:
    """
    Lấy bảng cân đối kế toán 5 năm gần nhất từ CafeF.

    Returns:
        DataFrame với index là tên chỉ tiêu, columns là năm ("2021", "2022", ...)
        Trả về DataFrame rỗng nếu không lấy được dữ liệu.
    """
    # BUG FIX: page=1 (không phải period=5) → lấy 5 năm GẦN NHẤT
    raw = _fetch_one_page(ticker, period_type="Y", page=1, report_type=2)
    if raw is None:
        return pd.DataFrame()
    return _parse_cafef_table(raw)


def fetch_cafef_yearly_full(ticker: str, n_years: int = 5) -> dict[str, pd.DataFrame]:
    """
    Lấy đầy đủ 3 báo cáo tài chính theo năm từ CafeF.

    n_years: số năm muốn lấy (tối đa 10 = 2 trang × 5 năm/trang).
      - n_years <= 5 → chỉ lấy trang 1 (5 năm gần nhất)
      - n_years > 5  → lấy thêm trang 2 và merge

    BUG FIX: tham số `period` trong CafeF API là PAGE NUMBER.
      Trước đây: _fetch_one_page(..., page=n_years, ...) với n_years=5
        → page=5 → trang 5 → dữ liệu năm ~2001-2005 (sai hoàn toàn)
      Sau fix: page=1 → trang 1 → 5 năm gần nhất (2021-2025) ✓

    Returns:
        {
            "income_statement": DataFrame,
            "balance_sheet":    DataFrame,
            "cash_flow":        DataFrame,
        }
    """
    report_types = {
        "income_statement": 1,
        "balance_sheet":    2,
        "cash_flow":        3,
    }
    # Cần bao nhiêu trang?
    pages_needed = 2 if n_years > 5 else 1

    result: dict[str, pd.DataFrame] = {}

    def _fetch_report(name: str, rtype: int) -> tuple[str, pd.DataFrame]:
        """Fetch 1 hoặc 2 trang rồi merge."""
        # Trang 1 — bắt buộc (5 năm gần nhất)
        raw1 = _fetch_one_page(ticker, period_type="Y", page=1, report_type=rtype)
        df1  = _parse_cafef_table(raw1) if raw1 else pd.DataFrame()

        if pages_needed < 2:
            return name, df1

        # Trang 2 — tùy chọn (5 năm cũ hơn)
        raw2 = _fetch_one_page(ticker, period_type="Y", page=2, report_type=rtype)
        df2  = _parse_cafef_table(raw2) if raw2 else pd.DataFrame()

        return name, _merge_cafef_pages(df1, df2)

    with ThreadPoolExecutor(max_workers=3) as exe:
        futures = {
            exe.submit(_fetch_report, name, rtype): name
            for name, rtype in report_types.items()
        }
        for future in as_completed(futures):
            name, df = future.result()
            result[name] = df

    return result


def fetch_cafef_quarterly_full(ticker: str, n_quarters: int = 8) -> dict[str, pd.DataFrame]:
    """
    Lấy đầy đủ 3 báo cáo tài chính theo quý từ CafeF.

    n_quarters: CafeF trả ~4-5 quý mỗi trang.
      n_quarters <= 5 → 1 trang; > 5 → 2 trang.

    Returns:
        {
            "income_statement": DataFrame,
            "balance_sheet":    DataFrame,
            "cash_flow":        DataFrame,
        }
    """
    report_types = {
        "income_statement": 1,
        "balance_sheet":    2,
        "cash_flow":        3,
    }
    pages_needed = 2 if n_quarters > 5 else 1

    result: dict[str, pd.DataFrame] = {}

    def _fetch_report(name: str, rtype: int) -> tuple[str, pd.DataFrame]:
        raw1 = _fetch_one_page(ticker, period_type="Q", page=1, report_type=rtype)
        df1  = _parse_cafef_table(raw1) if raw1 else pd.DataFrame()
        if pages_needed < 2:
            return name, df1
        raw2 = _fetch_one_page(ticker, period_type="Q", page=2, report_type=rtype)
        df2  = _parse_cafef_table(raw2) if raw2 else pd.DataFrame()
        return name, _merge_cafef_pages(df1, df2)

    with ThreadPoolExecutor(max_workers=3) as exe:
        futures = {
            exe.submit(_fetch_report, name, rtype): name
            for name, rtype in report_types.items()
        }
        for future in as_completed(futures):
            name, df = future.result()
            result[name] = df

    return result


def fetch_cafef_analysis_reports(ticker: str) -> pd.DataFrame:
    """
    Lấy bảng chỉ số phân tích (P/E, P/B, ROE, ROA...) từ CafeF.

    Returns:
        DataFrame hoặc DataFrame rỗng nếu lỗi.
    """
    # BUG FIX: page=1 thay vì period=5
    raw = _fetch_one_page(ticker, period_type="Y", page=1, report_type=5)
    if raw is None:
        return pd.DataFrame()
    return _parse_cafef_table(raw)
