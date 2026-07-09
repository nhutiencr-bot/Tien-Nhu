"""
cafef_fallback.py
Cào dữ liệu tài chính từ CafeF khi vnstock/SSI không available.
Không import bất kỳ module nội bộ nào trong project.
"""

import re
import time
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


def _fetch_one_period(ticker: str, period_type: str, period: int, report_type: int) -> dict | None:
    """
    Lấy một kỳ báo cáo từ CafeF API.

    period_type: 'Y' (năm) hoặc 'Q' (quý)
    report_type: 1=KQKD, 2=CDKT, 3=LCTT
    """
    url = (
        f"https://s.cafef.vn/Handlers/AjaxFinancialData.ashx"
        f"?symbol={ticker.upper()}&type={report_type}"
        f"&period={period}&periodType={period_type}"
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
    """Chuyển list JSON từ CafeF thành DataFrame."""
    if not raw_list:
        return pd.DataFrame()
    rows = []
    for item in raw_list:
        row = {"Chỉ tiêu": item.get("Name", ""), "Đơn vị": item.get("Unit", "")}
        for period_data in item.get("Data", []):
            col = period_data.get("Period", "")
            row[col] = period_data.get("Value")
        rows.append(row)
    df = pd.DataFrame(rows)
    if "Chỉ tiêu" in df.columns:
        df = df.set_index("Chỉ tiêu")
    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_cafef_balance_sheet_5y(ticker: str) -> pd.DataFrame:
    """
    Lấy bảng cân đối kế toán 5 năm gần nhất từ CafeF.

    Returns:
        DataFrame với index là tên chỉ tiêu, columns là năm (e.g. '2023', '2022', ...)
        Trả về DataFrame rỗng nếu không lấy được dữ liệu.
    """
    raw = _fetch_one_period(ticker, period_type="Y", period=5, report_type=2)
    if raw is None:
        return pd.DataFrame()
    return _parse_cafef_table(raw)


def fetch_cafef_yearly_full(ticker: str, n_years: int = 5) -> dict[str, pd.DataFrame]:
    """
    Lấy đầy đủ 3 báo cáo tài chính theo năm từ CafeF.

    Returns:
        {
            "income_statement": DataFrame,
            "balance_sheet":    DataFrame,
            "cash_flow":        DataFrame,
        }
    """
    report_types = {
        "income_statement": 1,
        "balance_sheet": 2,
        "cash_flow": 3,
    }
    result = {}

    def _fetch(name, rtype):
        raw = _fetch_one_period(ticker, period_type="Y", period=n_years, report_type=rtype)
        return name, _parse_cafef_table(raw) if raw else pd.DataFrame()

    with ThreadPoolExecutor(max_workers=3) as exe:
        futures = {exe.submit(_fetch, name, rtype): name for name, rtype in report_types.items()}
        for future in as_completed(futures):
            name, df = future.result()
            result[name] = df

    return result


def fetch_cafef_quarterly_full(ticker: str, n_quarters: int = 8) -> dict[str, pd.DataFrame]:
    """
    Lấy đầy đủ 3 báo cáo tài chính theo quý từ CafeF.

    Returns:
        {
            "income_statement": DataFrame,
            "balance_sheet":    DataFrame,
            "cash_flow":        DataFrame,
        }
    """
    report_types = {
        "income_statement": 1,
        "balance_sheet": 2,
        "cash_flow": 3,
    }
    result = {}

    def _fetch(name, rtype):
        raw = _fetch_one_period(ticker, period_type="Q", period=n_quarters, report_type=rtype)
        return name, _parse_cafef_table(raw) if raw else pd.DataFrame()

    with ThreadPoolExecutor(max_workers=3) as exe:
        futures = {exe.submit(_fetch, name, rtype): name for name, rtype in report_types.items()}
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
    url = (
        f"https://s.cafef.vn/Handlers/AjaxFinancialData.ashx"
        f"?symbol={ticker.upper()}&type=5&period=5&periodType=Y"
    )
    try:
        r = _SESSION.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        raw = data.get("Data", {}).get("ListFinancialData")
        if raw:
            return _parse_cafef_table(raw)
    except Exception:
        pass
    return pd.DataFrame()
