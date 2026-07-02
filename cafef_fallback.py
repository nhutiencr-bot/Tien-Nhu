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

REQUEST_TIMEOUT = 8  # tăng từ 6 → 8s cho năm cũ load chậm hơn

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
    if not raw:
        return None
    raw = raw.replace('.', '').replace(',', '.')
    try:
        return float(raw)
    except ValueError:
        return None


def _strip_html(html_text: str) -> str:
    """Strip HTML tags và chuẩn hoá whitespace."""
    text = re.sub(r'<[^>]+>', ' ', html_text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&#\d+;', ' ', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def _extract_row_values(html_text: str, row_label_pattern: str):
    """
    Tìm label trong plain text và lấy dãy số ngay sau.
    BUG CŨ: chỉ dùng 1 pattern → dễ miss nếu label có khoảng trắng lạ.
    FIX: normalize whitespace trong label trước khi match.
    """
    plain_text = _strip_html(html_text)
    pattern = re.compile(row_label_pattern + r'[\s:]*((?:-?[\d.,]+\s*){1,10})', re.IGNORECASE)
    match = pattern.search(plain_text)
    if not match:
        return []
    numbers_blob = match.group(1)
    raw_numbers = re.findall(r'-?[\d][\d.,]*', numbers_blob)
    return [_parse_vn_number(n) for n in raw_numbers]


def _extract_table_value(html_text: str, row_label_pattern: str,
                          col_index: int = 0) -> float | None:
    """
    Parse trực tiếp từ HTML table thay vì plain text — CHÍNH XÁC HƠN.

    CafeF incsta/bsheet render bảng dạng:
      <td>Doanh thu thuần</td><td>35,235</td><td>30,706</td>...

    col_index=0 → lấy cột đầu tiên (năm hiện tại = năm được request).
    col_index=1 → cột thứ 2 (năm trước đó, nếu CafeF hiển thị 2 năm).

    Trả về float (triệu đồng — đơn vị gốc CafeF) hoặc None.
    """
    pattern = re.compile(
        row_label_pattern + r'.*?</td>((?:\s*<td[^>]*>.*?</td>)+)',
        re.IGNORECASE | re.DOTALL
    )
    match = pattern.search(html_text)
    if not match:
        return None

    cells_html = match.group(1)
    # Lấy nội dung từng <td>...</td>
    cell_values = re.findall(r'<td[^>]*>(.*?)</td>', cells_html, re.DOTALL | re.IGNORECASE)

    def _clean_cell(c):
        c = re.sub(r'<[^>]+>', '', c)
        c = re.sub(r'&nbsp;', '', c)
        c = c.strip().replace('.', '').replace(',', '.')
        try:
            return float(c)
        except Exception:
            return None

    cleaned = [_clean_cell(c) for c in cell_values]
    cleaned = [v for v in cleaned if v is not None]

    if not cleaned:
        return None
    return cleaned[col_index] if col_index < len(cleaned) else cleaned[0]


# ── Mapping nhãn CafeF → key nội bộ ──────────────────────────────────────────
# CafeF hiển thị label khác nhau tuỳ loại DN (thông thường / ngân hàng / CK).
# Thứ tự ưu tiên: label chính xác nhất trước, fallback sau.

_BSHEET_LABELS = {
    'equity': [
        r'D\.\s*VỐN CHỦ SỞ HỮU',
        r'VỐN CHỦ SỞ HỮU \(.*?\)',
        r'VỐN CHỦ SỞ HỮU',
        r'I\.\s*Vốn chủ sở hữu',
        r'Vốn chủ sở hữu',
        r'VCSH',
    ],
    'total_assets': [
        r'TỔNG CỘNG TÀI SẢN',
        r'TỔNG TÀI SẢN',
        r'Tổng cộng tài sản',
        r'Total assets',
    ],
}

_INCSTA_LABELS = {
    'revenue': [
        r'Doanh thu thuần về bán hàng và cung cấp dịch vụ',
        r'Doanh thu thuần',
        r'DOANH THU THUẦN',
        r'Tổng doanh thu hoạt động',
        r'TỔNG THU NHẬP HOẠT ĐỘNG',
        r'Tổng thu nhập hoạt động',
        r'Thu nhập lãi thuần',
        r'Doanh thu hoạt động',
        r'Tổng doanh thu',
        r'Doanh thu bán hàng và cung cấp dịch vụ',
        r'Doanh thu',
    ],
    'net_profit': [
        r'Lợi nhuận sau thuế thu nhập doanh nghiệp',
        r'LỢI NHUẬN SAU THUẾ THU NHẬP DOANH NGHIỆP',
        r'LỢI NHUẬN SAU THUẾ',
        r'Lợi nhuận sau thuế',
        r'Lãi/\s*\(lỗ\) thuần sau thuế',
        r'Lợi nhuận thuần sau thuế',
        r'Lợi nhuận ròng',
    ],
}


def _try_extract(html: str, labels: list[str]) -> float | None:
    """
    Thử lần lượt các label trong danh sách.
    Dùng cả 2 phương pháp: table parse (chính xác) + plain text fallback.
    Trả về float (đơn vị gốc: triệu VNĐ) hoặc None.
    """
    for label in labels:
        # Phương pháp 1: parse từ HTML table (chính xác hơn)
        val = _extract_table_value(html, label, col_index=0)
        if val is not None:
            return val
        # Phương pháp 2: fallback plain text
        vals = _extract_row_values(html, label)
        if vals and vals[0] is not None:
            return vals[0]
    return None


def _fetch_one_period(ticker: str, year: int, quarter: int, slug: str) -> dict:
    """
    Fetch bsheet + incsta cho 1 kỳ báo cáo (year, quarter).

    ⚠️ FIX QUAN TRỌNG: CafeF `incsta` với year cũ (2021) đôi khi trả về
    page rỗng ở URL chính. Thêm fallback URL thứ 2 với format khác.

    Đơn vị trả về: TỶ đồng (đã chia 1e9 từ đơn vị gốc triệu đồng × 1e6 / 1e9 = / 1e3).
    Chú ý: CafeF hiển thị số đơn vị TRIỆU đồng → chia 1000 để ra TỶ.
    """
    out = {}

    # URL chính
    bsheet_url = (f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}"
                  f"/bsheet/{year}/{quarter}/0/0/{slug}.chn")
    incsta_url = (f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}"
                  f"/incsta/{year}/{quarter}/0/0/{slug}.chn")

    # URL fallback (format cũ hơn của CafeF — đôi khi hoạt động với năm cũ)
    bsheet_url2 = (f"https://s.cafef.vn/{slug}/{year}/bsheet.chn"
                   f"?MaCK={ticker.upper()}&Nam={year}&Quy={quarter}")
    incsta_url2 = (f"https://s.cafef.vn/{slug}/{year}/incsta.chn"
                   f"?MaCK={ticker.upper()}&Nam={year}&Quy={quarter}")

    def _get(url: str) -> str:
        try:
            resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200 and len(resp.text) > 500:
                return resp.text
            return ""
        except Exception:
            return ""

    def _get_with_fallback(url1: str, url2: str) -> str:
        text = _get(url1)
        if not text:
            text = _get(url2)
        return text

    # Fetch song song 2 loại báo cáo, mỗi loại có fallback
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_bs = ex.submit(_get_with_fallback, bsheet_url, bsheet_url2)
        fut_is = ex.submit(_get_with_fallback, incsta_url, incsta_url2)
        text_bs = fut_bs.result()
        text_is = fut_is.result()

    # Parse bsheet
    if text_bs:
        eq_val = _try_extract(text_bs, _BSHEET_LABELS['equity'])
        ta_val = _try_extract(text_bs, _BSHEET_LABELS['total_assets'])
        # Đơn vị CafeF: triệu đồng → chia 1000 = tỷ đồng
        if eq_val is not None:
            out['equity'] = eq_val / 1000
        if ta_val is not None:
            out['total_assets'] = ta_val / 1000

    # Parse incsta
    if text_is:
        rev_val = _try_extract(text_is, _INCSTA_LABELS['revenue'])
        np_val = _try_extract(text_is, _INCSTA_LABELS['net_profit'])
        if rev_val is not None:
            out['revenue'] = rev_val / 1000
        if np_val is not None:
            out['net_profit'] = np_val / 1000

    return out


def fetch_cafef_balance_sheet_5y(ticker: str, end_year: int):
    slug = _find_company_slug(ticker)
    equity_by_year, total_assets_by_year = {}, {}
    revenue_by_year, net_profit_by_year = {}, {}

    if not _cafef_is_reachable():
        empty = pd.Series(dtype=float)
        return {"equity": empty, "total_assets": empty,
                "revenue": empty, "net_profit": empty}

    # ⚠️ end_year phải là năm CÓ BÁO CÁO (2025), không phải datetime.today().year (2026)
    # range(end_year - 4, end_year + 1) = [2021, 2022, 2023, 2024, 2025] ✓
    target_years = list(range(end_year - 4, end_year + 1))

    def fetch_task(year):
        return year, _fetch_one_period(ticker, year, 4, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_y = {executor.submit(fetch_task, y): y for y in target_years}
        for future in concurrent.futures.as_completed(future_to_y):
            try:
                year, data = future.result()
                if 'equity' in data:
                    equity_by_year[year] = data['equity']
                if 'total_assets' in data:
                    total_assets_by_year[year] = data['total_assets']
                if 'revenue' in data:
                    revenue_by_year[year] = data['revenue']
                if 'net_profit' in data:
                    net_profit_by_year[year] = data['net_profit']
            except Exception:
                pass

    return {
        "equity": pd.Series(equity_by_year).sort_index(),
        "total_assets": pd.Series(total_assets_by_year).sort_index(),
        "revenue": pd.Series(revenue_by_year).sort_index(),
        "net_profit": pd.Series(net_profit_by_year).sort_index(),
    }


def fetch_cafef_yearly_full(ticker: str, years: list, debug: bool = False):
    slug = _find_company_slug(ticker)
    revenue, net_profit, equity, total_assets = {}, {}, {}, {}
    empty = pd.Series(dtype=float)

    if not _cafef_is_reachable() or not years:
        return {"revenue": empty, "net_profit": empty,
                "equity": empty, "total_assets": empty,
                "roe": empty, "roa": empty}

    def fetch_task(year):
        return year, _fetch_one_period(ticker, year, 4, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(years), 6)) as executor:
        future_to_y = {executor.submit(fetch_task, y): y for y in years}
        for future in concurrent.futures.as_completed(future_to_y):
            try:
                year, data = future.result()
                if debug:
                    print(f"  [CafeF {year}] raw={data}")
                if 'revenue' in data:
                    revenue[year] = data['revenue']
                if 'net_profit' in data:
                    net_profit[year] = data['net_profit']
                if 'equity' in data:
                    equity[year] = data['equity']
                if 'total_assets' in data:
                    total_assets[year] = data['total_assets']
            except Exception:
                pass

    revenue_s = pd.Series(revenue).sort_index()
    profit_s = pd.Series(net_profit).sort_index()
    equity_s = pd.Series(equity).sort_index()
    assets_s = pd.Series(total_assets).sort_index()

    # Tính ROE/ROA từ dữ liệu scraped (dùng trong pipeline nếu cần)
    roe = (profit_s / equity_s.replace(0, float('nan')) * 100
           if not equity_s.empty else pd.Series(dtype=float))
    roa = (profit_s / assets_s.replace(0, float('nan')) * 100
           if not assets_s.empty else pd.Series(dtype=float))

    return {
        "revenue": revenue_s,
        "net_profit": profit_s,
        "equity": equity_s,
        "total_assets": assets_s,
        "roe": roe.dropna() if not roe.empty else roe,
        "roa": roa.dropna() if not roa.empty else roa,
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
                if 'revenue' in data:
                    revenue[key] = data['revenue']
                if 'net_profit' in data:
                    net_profit[key] = data['net_profit']
                if 'equity' in data:
                    equity[key] = data['equity']
                if 'total_assets' in data:
                    total_assets[key] = data['total_assets']
            except Exception:
                pass

    return {
        "revenue": revenue,
        "net_profit": net_profit,
        "equity": equity,
        "total_assets": total_assets,
    }


def fetch_cafef_analysis_reports(ticker: str):
    """Giữ nguyên — không thay đổi."""
    return {
        "reports": [],
        "is_ticker_specific": False,
        "sources_used": ["CafeF"],
        "debug_log": ["fetch_cafef_analysis_reports: not implemented in this version."],
    }
