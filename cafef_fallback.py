"""
cafef_fallback.py
-----------------
Lấy BCTC (Vốn CSH, Tổng tài sản, Doanh thu, LNST) từ CafeF
khi vnstock (VCI/KBS/DNSE) không có dữ liệu năm cũ (thường là 2021).

ĐƠN VỊ: CafeF trả về TRIỆU đồng → hàm này chuyển sang TỶ (÷ 1,000).
KHÔNG gọi normalize_to_billion_vnd() trên kết quả — đã convert sẵn.

URL pattern đúng (đã kiểm tra thực tế trên repo):
  bsheet  → Bảng cân đối kế toán (equity + total_assets)
  incsta  → Kết quả kinh doanh     (revenue + net_profit)
  Slug    → tên công ty viết thường, lấy từ URL trang CafeF của mã
"""

import re
import time
import unicodedata
import pandas as pd
import requests
import concurrent.futures


# ── Cấu hình ─────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 8
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9",
    "Referer": "https://s.cafef.vn/",
}

_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)

# Cache kiểm tra reachability (tránh probe mỗi lần gọi)
_REACHABLE_CACHE: dict = {"value": None, "ts": 0.0}
_REACHABLE_CACHE_TTL = 120  # giây


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cafef_is_reachable() -> bool:
    now = time.time()
    if _REACHABLE_CACHE["value"] is not None and (now - _REACHABLE_CACHE["ts"]) < _REACHABLE_CACHE_TTL:
        return _REACHABLE_CACHE["value"]
    try:
        # Probe URL dữ liệu thật (không phải homepage — homepage có thể block,
        # nhưng URL báo cáo cụ thể vẫn accessible từ Streamlit Cloud).
        # Chấp nhận bất kỳ response < 500 = server đang hoạt động.
        resp = _SESSION.get(
            "https://s.cafef.vn/bao-cao-tai-chinh/VNM/bsheet/2024/4/0/0/vnm.chn",
            timeout=REQUEST_TIMEOUT)
        ok = resp.status_code < 500
    except Exception:
        ok = False
    _REACHABLE_CACHE["ts"] = now
    _REACHABLE_CACHE["value"] = ok
    return ok


def _find_company_slug(ticker: str) -> str:
    """Lấy slug (tên viết thường) cho URL CafeF. Mặc định = mã viết thường."""
    return ticker.lower()


def _parse_vn_number(raw: str):
    """Parse số CafeF (đơn vị triệu) → float triệu. Trả None nếu không parse được."""
    if not raw:
        return None
    raw = raw.strip().replace('\xa0', '').replace(' ', '').replace('%', '')
    if raw in ('', '-', '—', 'N/A', 'n/a', '..'):
        return None
    try:
        if ',' in raw and '.' in raw:
            cleaned = raw.replace(',', '')
        elif ',' in raw:
            cleaned = raw.replace(',', '')
        else:
            cleaned = raw
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _extract_row_values(html_text: str, row_label_pattern: str):
    """
    Tìm dòng có label khớp regex trong HTML text đã strip tags,
    trả về list giá trị số tìm được sau label đó.
    Chuẩn hoá NFC để tránh mismatch Unicode NFC vs NFD (tiếng Việt dấu).
    """
    plain = re.sub(r'<[^>]+>', ' ', html_text)
    plain = re.sub(r'&nbsp;', ' ', plain)
    plain = re.sub(r'\s+', ' ', plain)
    plain = unicodedata.normalize('NFC', plain)
    row_label_pattern = unicodedata.normalize('NFC', row_label_pattern)
    pattern = re.compile(row_label_pattern + r'\s*((?:-?[\d.,]+\s*){1,20})', re.IGNORECASE)
    match = pattern.search(plain)
    if not match:
        return []
    blob = match.group(1)
    raws = re.findall(r'-?[\d][\d.,]*', blob)
    return [_parse_vn_number(r) for r in raws]


def _fetch_one_period(ticker: str, year: int, quarter: int, slug: str) -> dict:
    """
    Cào 1 kỳ báo cáo từ CafeF (bsheet + incsta song song).
    Trả về dict với các key có: equity, total_assets, revenue, net_profit.
    Đơn vị trả về: TỶ VNĐ (đã chia 1,000 từ triệu).
    """
    out = {}
    base = f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}"
    bsheet_url  = f"{base}/bsheet/{year}/{quarter}/0/0/{slug}.chn"
    incsta_url  = f"{base}/incsta/{year}/{quarter}/0/0/{slug}.chn"

    def _get(url):
        try:
            r = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
            return r.text if r.status_code == 200 else ""
        except Exception:
            return ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_bs = ex.submit(_get, bsheet_url)
        f_is = ex.submit(_get, incsta_url)
        text_bs = f_bs.result()
        text_is = f_is.result()

    # ── Balance sheet ─────────────────────────────────────────────────────────
    if text_bs:
        eq_vals = (
            _extract_row_values(text_bs, r'D\.\s*VỐN CHỦ SỞ HỮU') or
            _extract_row_values(text_bs, r'VỐN CHỦ SỞ HỮU') or
            _extract_row_values(text_bs, r'I\.\s*Vốn chủ sở hữu') or
            _extract_row_values(text_bs, r'Vốn chủ sở hữu')
        )
        ta_vals = (
            _extract_row_values(text_bs, r'TỔNG CỘNG TÀI SẢN') or
            _extract_row_values(text_bs, r'TỔNG TÀI SẢN') or
            _extract_row_values(text_bs, r'Tổng cộng tài sản')
        )
        # Lấy phần tử cuối (thường là năm hiện tại trong bảng dọc)
        # Giá trị 0 = lỗi extract, không lưu
        if eq_vals and eq_vals[-1] not in (None, 0.0, 0):
            out['equity'] = round(eq_vals[-1] / 1_000, 2)      # triệu → tỷ
        if ta_vals and ta_vals[-1] not in (None, 0.0, 0):
            out['total_assets'] = round(ta_vals[-1] / 1_000, 2)

    # ── Income statement ──────────────────────────────────────────────────────
    if text_is:
        rev_vals = (
            _extract_row_values(text_is, r'Doanh thu thuần') or
            _extract_row_values(text_is, r'TỔNG THU NHẬP HOẠT ĐỘNG') or
            _extract_row_values(text_is, r'Tổng thu nhập hoạt động') or
            _extract_row_values(text_is, r'Thu nhập lãi thuần') or
            _extract_row_values(text_is, r'Doanh thu hoạt động') or
            _extract_row_values(text_is, r'Tổng doanh thu') or
            _extract_row_values(text_is, r'Doanh thu bán hàng và cung cấp dịch vụ') or
            _extract_row_values(text_is, r'Doanh thu')
        )
        np_vals = (
            _extract_row_values(text_is, r'Lợi nhuận sau thuế thu nhập doanh nghiệp') or
            _extract_row_values(text_is, r'LỢI NHUẬN SAU THUẾ') or
            _extract_row_values(text_is, r'Lợi nhuận sau thuế') or
            _extract_row_values(text_is, r'Lãi/\s*\(lỗ\) thuần sau thuế') or
            _extract_row_values(text_is, r'Lợi nhuận thuần sau thuế') or
            _extract_row_values(text_is, r'Lợi nhuận kế toán sau thuế') or
            _extract_row_values(text_is, r'Lợi nhuận sau thuế TNDN') or
            _extract_row_values(text_is, r'Lãi sau thuế')
        )
        if rev_vals and rev_vals[-1] not in (None, 0.0, 0):
            out['revenue'] = round(rev_vals[-1] / 1_000, 2)
        if np_vals and np_vals[-1] not in (None, 0.0, 0):
            out['net_profit'] = round(np_vals[-1] / 1_000, 2)

    return out


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_cafef_balance_sheet_5y(
    ticker: str,
    end_year: int | None = None,
    years: list | None = None,
) -> dict:
    """
    Lấy Vốn CSH + Tổng tài sản từ CafeF cho danh sách năm cụ thể
    (mặc định: 2021–2025).

    Trả về:
      {'equity': pd.Series, 'total_assets': pd.Series}  — đơn vị: tỷ VNĐ
    """
    empty = {'equity': pd.Series(dtype=float), 'total_assets': pd.Series(dtype=float)}
    if not _cafef_is_reachable():
        return empty

    if years is None:
        if end_year is None:
            import datetime; end_year = datetime.date.today().year
        # Cố định 2021-2025 — không dùng range động để tránh bỏ sót 2021
        years = list(range(2021, end_year))

    slug = _find_company_slug(ticker)
    eq_dict, ta_dict = {}, {}

    def _task(y):
        return y, _fetch_one_period(ticker, y, 4, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(years), 5)) as ex:
        futures = {ex.submit(_task, y): y for y in years}
        for f in concurrent.futures.as_completed(futures):
            try:
                y, data = f.result()
                if 'equity'       in data: eq_dict[y] = data['equity']
                if 'total_assets' in data: ta_dict[y] = data['total_assets']
            except Exception:
                pass

    return {
        'equity':       pd.Series(eq_dict, dtype=float).sort_index(),
        'total_assets': pd.Series(ta_dict, dtype=float).sort_index(),
    }


def fetch_cafef_yearly_full(
    ticker: str,
    years: list | None = None,
    debug: bool = False,
) -> dict:
    """
    Lấy đủ 4 chỉ tiêu (equity, total_assets, revenue, net_profit) từ CafeF.

    Tham số `years`: danh sách năm cần cào. Nếu None → cào 2021–2025.
    Trả về dict mỗi key là pd.Series(index=năm, values=tỷ VNĐ).

    Fallback tự động: nếu CafeF không accessible → trả về 4 Series rỗng
    (không crash pipeline, pipeline sẽ dùng dữ liệu vnstock gốc).
    """
    empty_s = pd.Series(dtype=float)
    empty = {'equity': empty_s, 'total_assets': empty_s,
             'revenue': empty_s, 'net_profit': empty_s}

    if not _cafef_is_reachable():
        return empty

    if years is None:
        import datetime
        cur = datetime.date.today().year
        years = list(range(2021, cur))   # 2021 đến năm hiện tại (không bao gồm)

    slug = _find_company_slug(ticker)
    eq_dict, ta_dict, rev_dict, np_dict = {}, {}, {}, {}

    def _task(y):
        return y, _fetch_one_period(ticker, y, 4, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(years), 6)) as ex:
        futures = {ex.submit(_task, y): y for y in years}
        for f in concurrent.futures.as_completed(futures):
            try:
                y, data = f.result()
                if 'equity'       in data: eq_dict[y]  = data['equity']
                if 'total_assets' in data: ta_dict[y]  = data['total_assets']
                if 'revenue'      in data: rev_dict[y] = data['revenue']
                if 'net_profit'   in data: np_dict[y]  = data['net_profit']
            except Exception:
                pass

    return {
        'equity':       pd.Series(eq_dict,  dtype=float).sort_index(),
        'total_assets': pd.Series(ta_dict,  dtype=float).sort_index(),
        'revenue':      pd.Series(rev_dict, dtype=float).sort_index(),
        'net_profit':   pd.Series(np_dict,  dtype=float).sort_index(),
    }


def fetch_cafef_analysis_reports(ticker: str, page_size: int = 10) -> list:
    """Lấy danh sách báo cáo phân tích từ CafeF (dùng cho tab Báo Cáo)."""
    url = f"https://s.cafef.vn/bao-cao-phan-tich/{ticker.lower()}.chn"
    out = []
    try:
        r = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, 'html.parser')
        seen = set()
        for a in soup.select("a[href*='.chn']"):
            href  = a.get('href', '')
            title = a.get_text(strip=True)
            if not title or len(title) < 10 or href in seen:
                continue
            if 'report' not in href.lower():
                continue
            seen.add(href)
            if not href.startswith('http'):
                href = 'https://s.cafef.vn' + (href if href.startswith('/') else '/' + href)
            out.append({'title': title, 'url': href, 'source': '—', 'report_date': '—',
                        'recommendation': '—', 'target_price': None})
            if len(out) >= page_size:
                break
    except Exception:
        pass
    return out
