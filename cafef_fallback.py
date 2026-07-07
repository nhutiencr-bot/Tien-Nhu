import re
import time
import pandas as pd
import requests
import concurrent.futures

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Referer": "https://s.cafef.vn/",
}

REQUEST_TIMEOUT = 6

_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)

_REACHABLE_CACHE = {"ts": 0.0, "value": None}
_REACHABLE_CACHE_TTL = 30


def _cafef_is_reachable() -> bool:
    now = time.time()
    if _REACHABLE_CACHE["value"] is not None and (now - _REACHABLE_CACHE["ts"]) < _REACHABLE_CACHE_TTL:
        return _REACHABLE_CACHE["value"]
    try:
        resp = _SESSION.get("https://s.cafef.vn", timeout=REQUEST_TIMEOUT)
        ok = resp.status_code == 200
    except Exception:
        ok = False
    _REACHABLE_CACHE["ts"] = now
    _REACHABLE_CACHE["value"] = ok
    return ok


def _find_company_slug(ticker: str) -> str:
    return f"bao-cao-tai-chinh-{ticker.lower()}"


def _parse_vn_number(raw: str):
    raw = raw.strip()
    if not raw: return None
    raw = raw.replace('.', '').replace(',', '.')
    try: return float(raw)
    except ValueError: return None


def _extract_row_values(html_text: str, row_label_pattern: str):
    plain_text = re.sub(r'<[^>]+>', ' ', html_text)
    plain_text = re.sub(r'&nbsp;', ' ', plain_text)
    plain_text = re.sub(r'\s+', ' ', plain_text)
    pattern = re.compile(row_label_pattern + r'\s*((?:-?[\d.,]+\s*){1,40})', re.IGNORECASE)
    match = pattern.search(plain_text)
    if not match: return []
    numbers_blob = match.group(1)
    raw_numbers = re.findall(r'-?[\d][\d.,]*', numbers_blob)
    return [_parse_vn_number(n) for n in raw_numbers]


def _fetch_one_period(ticker: str, year: int, quarter: int, slug: str):
    out = {}
    bsheet_url = f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}/bsheet/{year}/{quarter}/0/0/{slug}.chn"
    incsta_url = f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}/incsta/{year}/{quarter}/0/0/{slug}.chn"

    def _get(url):
        try:
            resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
            return resp.text if resp.status_code == 200 else ""
        except Exception:
            return ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pair_executor:
        fut_bs = pair_executor.submit(_get, bsheet_url)
        fut_is = pair_executor.submit(_get, incsta_url)
        text_bs = fut_bs.result()
        text_is = fut_is.result()

    if text_bs:
        equity_vals = (_extract_row_values(text_bs, r'D\.\s*VỐN CHỦ SỞ HỮU') or
                       _extract_row_values(text_bs, r'VỐN CHỦ SỞ HỮU') or
                       _extract_row_values(text_bs, r'I\.\s*Vốn chủ sở hữu') or
                       _extract_row_values(text_bs, r'Vốn chủ sở hữu'))
        assets_vals = (_extract_row_values(text_bs, r'TỔNG CỘNG TÀI SẢN') or
                       _extract_row_values(text_bs, r'TỔNG TÀI SẢN') or
                       _extract_row_values(text_bs, r'Tổng cộng tài sản'))
        if equity_vals and equity_vals[-1] is not None: out['equity'] = equity_vals[-1] / 1e9
        if assets_vals and assets_vals[-1] is not None: out['total_assets'] = assets_vals[-1] / 1e9

    if text_is:
        revenue_vals = (_extract_row_values(text_is, r'Doanh thu thuần') or
                        _extract_row_values(text_is, r'TỔNG THU NHẬP HOẠT ĐỘNG') or
                        _extract_row_values(text_is, r'Tổng thu nhập hoạt động') or
                        _extract_row_values(text_is, r'Thu nhập lãi thuần') or
                        _extract_row_values(text_is, r'Doanh thu hoạt động') or
                        _extract_row_values(text_is, r'Tổng doanh thu hoạt động') or
                        _extract_row_values(text_is, r'Tổng doanh thu') or
                        _extract_row_values(text_is, r'Doanh thu bán hàng và cung cấp dịch vụ') or
                        _extract_row_values(text_is, r'Doanh thu'))
        profit_vals = (_extract_row_values(text_is, r'Lợi nhuận sau thuế thu nhập doanh nghiệp') or
                       _extract_row_values(text_is, r'LỢI NHUẬN SAU THUẾ') or
                       _extract_row_values(text_is, r'Lợi nhuận sau thuế') or
                       _extract_row_values(text_is, r'Lãi/\s*\(lỗ\) thuần sau thuế') or
                       _extract_row_values(text_is, r'Lợi nhuận thuần sau thuế'))
        if revenue_vals and revenue_vals[-1] is not None: out['revenue'] = revenue_vals[-1] / 1e9
        if profit_vals and profit_vals[-1] is not None: out['net_profit'] = profit_vals[-1] / 1e9

    return out


def fetch_cafef_balance_sheet_5y(ticker: str, end_year: int):
    slug = _find_company_slug(ticker)
    equity_by_year, total_assets_by_year = {}, {}

    if not _cafef_is_reachable():
        return {"equity": pd.Series(dtype=float), "total_assets": pd.Series(dtype=float)}

    def fetch_task(year):
        return year, _fetch_one_period(ticker, year, 4, slug)

    # ── FIX: range(end_year - 4, end_year + 1) cào 2022-2026, bỏ 2021 ──────
    # Đổi thành range(end_year - 5, end_year) → cào đúng 2021-2025
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_y = {
            executor.submit(fetch_task, y): y
            for y in range(end_year - 5, end_year)   # 2021, 2022, 2023, 2024, 2025
        }
        for future in concurrent.futures.as_completed(future_to_y):
            try:
                year, data = future.result()
                if 'equity'       in data: equity_by_year[year]      = data['equity']
                if 'total_assets' in data: total_assets_by_year[year] = data['total_assets']
            except Exception:
                pass

    return {
        "equity":       pd.Series(equity_by_year).sort_index(),
        "total_assets": pd.Series(total_assets_by_year).sort_index(),
    }


def fetch_cafef_yearly_full(ticker: str, years: list, debug: bool = False):
    slug = _find_company_slug(ticker)
    revenue, net_profit, equity, total_assets = {}, {}, {}, {}
    empty = pd.Series(dtype=float)

    if not _cafef_is_reachable() or not years:
        return {"revenue": empty, "net_profit": empty, "equity": empty,
                "total_assets": empty, "roe": empty, "roa": empty}

    def fetch_task(year):
        return year, _fetch_one_period(ticker, year, 4, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(years), 6)) as executor:
        future_to_y = {executor.submit(fetch_task, y): y for y in years}
        for future in concurrent.futures.as_completed(future_to_y):
            try:
                year, data = future.result()
                if 'revenue'      in data: revenue[year]      = data['revenue']
                if 'net_profit'   in data: net_profit[year]   = data['net_profit']
                if 'equity'       in data: equity[year]        = data['equity']
                if 'total_assets' in data: total_assets[year]  = data['total_assets']
            except Exception:
                pass

    revenue_s, profit_s = pd.Series(revenue).sort_index(), pd.Series(net_profit).sort_index()
    equity_s,  assets_s = pd.Series(equity).sort_index(),  pd.Series(total_assets).sort_index()

    roe = (profit_s / equity_s.replace(0, float('nan')) * 100) if not equity_s.empty else empty
    roa = (profit_s / assets_s.replace(0, float('nan')) * 100) if not assets_s.empty else empty

    return {
        "revenue":       revenue_s,  "net_profit":   profit_s,
        "equity":        equity_s,   "total_assets": assets_s,
        "roe":           roe.dropna(), "roa":         roa.dropna(),
    }


def fetch_cafef_quarterly_full(ticker: str, quarters: list, debug: bool = False):
    slug = _find_company_slug(ticker)
    revenue, net_profit, equity, total_assets = {}, {}, {}, {}

    if not _cafef_is_reachable() or not quarters:
        return {"revenue": {}, "net_profit": {}, "equity": {}, "total_assets": {}}

    def fetch_task(q_tuple):
        year, q = q_tuple
        return f"{year}-Q{q}", _fetch_one_period(ticker, year, q, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(quarters), 6)) as executor:
        future_to_q = {executor.submit(fetch_task, qt): qt for qt in quarters}
        for future in concurrent.futures.as_completed(future_to_q):
            try:
                key, data = future.result()
                if 'revenue'      in data: revenue[key]      = data['revenue']
                if 'net_profit'   in data: net_profit[key]   = data['net_profit']
                if 'equity'       in data: equity[key]        = data['equity']
                if 'total_assets' in data: total_assets[key]  = data['total_assets']
            except Exception:
                pass

    return {
        "revenue":       revenue,    "net_profit":   net_profit,
        "equity":        equity,     "total_assets": total_assets,
    }
