import re
import time
import pandas as pd
import requests
import concurrent.futures

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Referer": "https://s.cafef.vn/",
}

# Timeout ngắn hơn cho từng request đơn lẻ -> tránh treo cả pipeline khi cafef chậm/rớt mạng.
REQUEST_TIMEOUT = 6

# Dùng chung 1 Session để tái sử dụng kết nối TCP/TLS (keep-alive) thay vì
# mở kết nối mới cho mỗi request -> nhanh hơn đáng kể khi cào nhiều trang.
_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)

# Cache kết quả "cafef có phản hồi không" trong vài giây để KHÔNG phải bắn
# request kiểm tra reachability nhiều lần trong cùng 1 lượt tải dữ liệu
# (trước đây mỗi hàm fetch_cafef_* tự kiểm tra riêng -> tốn thêm round-trip).
_REACHABLE_CACHE = {"ts": 0.0, "value": None}
_REACHABLE_CACHE_TTL = 30  # giây


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


def _extract_row_values(html_text: str, row_label_pattern: str):
    plain_text = re.sub(r'<[^>]+>', ' ', html_text)
    plain_text = re.sub(r'&nbsp;', ' ', plain_text)
    plain_text = re.sub(r'\s+', ' ', plain_text)
    pattern = re.compile(row_label_pattern + r'\s*((?:-?[\d.,]+\s*){1,40})', re.IGNORECASE)
    match = pattern.search(plain_text)
    if not match:
        return []
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

    # Bắn 2 request (bsheet + incsta) CÙNG LÚC thay vì lần lượt -> giảm ~một nửa
    # thời gian chờ cho mỗi kỳ báo cáo.
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
        if equity_vals and equity_vals[-1] not in (None, 0.0, 0):
            out['equity'] = equity_vals[-1] / 1e9
        if assets_vals and assets_vals[-1] not in (None, 0.0, 0):
            out['total_assets'] = assets_vals[-1] / 1e9

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
                       _extract_row_values(text_is, r'Lợi nhuận thuần sau thuế') or
                       _extract_row_values(text_is, r'Lợi nhuận kế toán sau thuế') or
                       _extract_row_values(text_is, r'Lợi nhuận sau thuế TNDN') or
                       _extract_row_values(text_is, r'LNST') or
                       _extract_row_values(text_is, r'Lãi sau thuế') or
                       _extract_row_values(text_is, r'20\.\s*Lợi nhuận sau thuế') or
                       _extract_row_values(text_is, r'Lợi nhuận thuần từ hoạt động kinh doanh'))
        # ⚠️ Sanity guard: doanh thu/LNST đúng bằng 0.0 gần như KHÔNG BAO GIỜ
        # là số thật cho 1 công ty niêm yết đang hoạt động cả năm — nhiều khả
        # năng regex khớp nhầm 1 dòng/cột trống (VD do layout báo cáo năm cũ
        # khác cấu trúc bảng của các năm gần đây). Coi giá trị 0.0 là TRÍCH
        # XUẤT THẤT BẠI, không phải dữ liệu thật — bỏ qua thay vì lưu số 0
        # sai lệch vào bảng (đã từng gây hiển thị "Doanh thu thuần = 0.00"
        # sai cho năm 2021 dù công ty chắc chắn có doanh thu).
        if revenue_vals and revenue_vals[-1] not in (None, 0.0, 0):
            out['revenue'] = revenue_vals[-1] / 1e9
        if profit_vals and profit_vals[-1] not in (None, 0.0, 0):
            out['net_profit'] = profit_vals[-1] / 1e9

    return out


def fetch_cafef_balance_sheet_5y(ticker: str, end_year: int = None, years: list = None):
    """
    ⚠️ TRƯỚC ĐÂY: hardcode range(end_year-4, end_year+1) — LUÔN chỉ lấy 5 năm
    TRAILING TỪ NĂM HIỆN TẠI (VD năm 2026 → chỉ 2022-2026), không bao giờ
    với tới được 2021 dù dữ liệu vẫn tồn tại trên CafeF. Đây là 1 trong 2
    nguyên nhân gốc khiến dashboard luôn thiếu năm 2021 (xem pipeline.py
    phần "Fallback CafeF" — nguyên nhân còn lại là fallback chỉ kích hoạt
    khi CẢ series rỗng, không backfill từng năm còn thiếu riêng lẻ).

    Nay nhận thẳng `years` (danh sách năm cụ thể) để gọi nơi khác có thể
    yêu cầu đúng năm bị thiếu (VD chỉ [2021]) thay vì bị khoá cứng vào
    cửa sổ 5 năm trượt theo năm hiện tại. Giữ `end_year` cho tương thích
    ngược — nếu không truyền `years`, mặc định lấy 6 năm gần nhất (rộng
    hơn 1 năm so với bản cũ) để tăng khả năng vẫn phủ được 2021.
    """
    slug = _find_company_slug(ticker)
    equity_by_year, total_assets_by_year = {}, {}

    if not _cafef_is_reachable():
        return {"equity": pd.Series(dtype=float), "total_assets": pd.Series(dtype=float)}

    if years is None:
        if end_year is None:
            end_year = pd.Timestamp.today().year
        years = list(range(end_year - 5, end_year + 1))  # 6 năm, thay vì 5 năm cũ

    def fetch_task(year):
        return year, _fetch_one_period(ticker, year, 4, slug)

    # ĐA LUỒNG: Cào nhiều năm cùng 1 lúc
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(years), 6)) as executor:
        future_to_y = {executor.submit(fetch_task, y): y for y in years}
        for future in concurrent.futures.as_completed(future_to_y):
            try:
                year, data = future.result()
                if 'equity' in data:
                    equity_by_year[year] = data['equity']
                if 'total_assets' in data:
                    total_assets_by_year[year] = data['total_assets']
            except Exception:
                pass

    return {
        "equity": pd.Series(equity_by_year).sort_index(),
        "total_assets": pd.Series(total_assets_by_year).sort_index(),
    }


def fetch_cafef_yearly_full(ticker: str, years: list, debug: bool = False):
    slug = _find_company_slug(ticker)
    revenue, net_profit, equity, total_assets = {}, {}, {}, {}
    empty = pd.Series(dtype=float)

    if not _cafef_is_reachable() or not years:
        return {"revenue": empty, "net_profit": empty, "equity": empty, "total_assets": empty,
                "roe": empty, "roa": empty}

    def fetch_task(year):
        return year, _fetch_one_period(ticker, year, 4, slug)

    # ĐA LUỒNG: Cào bù nhiều năm cùng 1 lúc
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(years), 6)) as executor:
        future_to_y = {executor.submit(fetch_task, y): y for y in years}
        for future in concurrent.futures.as_completed(future_to_y):
            try:
                year, data = future.result()
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

    revenue_s, profit_s = pd.Series(revenue).sort_index(), pd.Series(net_profit).sort_index()
    equity_s, assets_s = pd.Series(equity).sort_index(), pd.Series(total_assets).sort_index()
    roe = (profit_s / equity_s.replace(0, float('nan')) * 100) if not equity_s.empty else pd.Series(dtype=float)
    roa = (profit_s / assets_s.replace(0, float('nan')) * 100) if not assets_s.empty else pd.Series(dtype=float)

    return {
        "revenue": revenue_s, "net_profit": profit_s,
        "equity": equity_s, "total_assets": assets_s,
        "roe": roe.dropna(), "roa": roa.dropna(),
    }


def fetch_cafef_analysis_reports(ticker: str, page_size: int = 10):
    """
    Lấy danh sách báo cáo phân tích/khuyến nghị từ CafeF (CTCK: SSI, VND, VCI,
    HCM, MAS, DNSE, KBS, TCBS, VCBS, VPBS, VDS, ...).

    Trang nguồn: https://cafef.vn/du-lieu/phan-tich-bao-cao.chn (server-render
    sẵn, không cần JS) — đây là feed chung của toàn thị trường (CafeF không
    public 1 endpoint lọc thẳng theo mã). Vì vậy hàm sẽ:
      1. Tải feed báo cáo mới nhất.
      2. Lọc các báo cáo có MÃ TICKER xuất hiện (dạng từ riêng, in hoa) trong
         tiêu đề -> coi là báo cáo riêng cho mã đó (is_ticker_specific=True).
      3. Nếu không có báo cáo riêng, fallback trả về toàn bộ feed mới nhất
         (is_ticker_specific=False) để người dùng vẫn có báo cáo tham khảo.

    Trả về dict:
        {
            "reports": [{"title", "url", "source", "pub_date"}, ...],
            "is_ticker_specific": bool,
            "sources_used": ["CafeF"],
            "debug_log": [str, ...],
        }

    Không raise exception — lỗi/timeout sẽ trả về reports rỗng kèm debug_log.
    """
    debug_log = []
    ticker = ticker.upper().strip()
    feed_url = "https://cafef.vn/du-lieu/phan-tich-bao-cao.chn"

    if not _cafef_is_reachable():
        debug_log.append("CafeF không phản hồi (reachability check thất bại).")
        return {"reports": [], "is_ticker_specific": False,
                "sources_used": ["CafeF"], "debug_log": debug_log}

    try:
        resp = _SESSION.get(feed_url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200 or not resp.text:
            debug_log.append(f"Tải feed báo cáo: HTTP {resp.status_code}")
            return {"reports": [], "is_ticker_specific": False,
                    "sources_used": ["CafeF"], "debug_log": debug_log}
        html_text = resp.text
    except Exception as e:
        debug_log.append(f"Tải feed báo cáo: lỗi {e}")
        return {"reports": [], "is_ticker_specific": False,
                "sources_used": ["CafeF"], "debug_log": debug_log}

    # Mỗi báo cáo là 1 thẻ <a href=".../du-lieu/report/<slug>.chn...">Tiêu đề</a>
    # Lấy toàn bộ link kèm vị trí xuất hiện trong văn bản, để sau đó tìm
    # "Nguồn: <CTCK>" và ngày dd/mm/yyyy nằm gần đó (trong vòng ~400 ký tự
    # phía sau, đúng với thứ tự hiển thị thật trên trang).
    link_pattern = re.compile(
        r'href="(https?://cafef\.vn/du-lieu/report/[^"]+\.chn[^"]*)"[^>]*>([^<]{8,200})</a>',
        re.IGNORECASE
    )

    seen_urls = set()
    raw_reports = []
    for m in link_pattern.finditer(html_text):
        url, title = m.group(1).strip(), m.group(2).strip()
        clean_url = url.split('?')[0]
        if clean_url in seen_urls:
            continue
        title = re.sub(r'\s+', ' ', title).strip()
        if not title or len(title) < 8:
            continue
        seen_urls.add(clean_url)

        # Tìm "Nguồn: XXX" và ngày dd/mm/yyyy trong đoạn văn bản ngay sau link
        window = html_text[m.end(): m.end() + 600]
        window_plain = re.sub(r'<[^>]+>', ' ', window)
        window_plain = re.sub(r'\s+', ' ', window_plain)

        source_match = re.search(r'Ngu.n[:\s]+([A-Za-zÀ-ỹ\-\. ]{2,30}?)(?=\s*\d|\s*$)', window_plain)
        source = source_match.group(1).strip() if source_match else "CafeF"

        date_match = re.search(r'\b(\d{1,2}/\d{1,2}/\d{4})\b', window_plain)
        pub_date = date_match.group(1) if date_match else "—"

        raw_reports.append({
            "title": title, "url": clean_url,
            "source": source, "pub_date": pub_date,
        })

    debug_log.append(f"Feed chung CafeF: tìm thấy {len(raw_reports)} báo cáo.")

    # Lọc theo mã ticker (xuất hiện như 1 từ riêng, in hoa, trong tiêu đề)
    ticker_pattern = re.compile(rf'\b{re.escape(ticker)}\b')
    ticker_reports = [r for r in raw_reports if ticker_pattern.search(r["title"])]
    debug_log.append(f"Lọc theo mã {ticker}: tìm thấy {len(ticker_reports)} báo cáo riêng.")

    if ticker_reports:
        return {
            "reports": ticker_reports[:page_size],
            "is_ticker_specific": True,
            "sources_used": ["CafeF"],
            "debug_log": debug_log,
        }

    # Fallback: không có báo cáo riêng cho mã -> trả về feed chung mới nhất
    return {
        "reports": raw_reports[:page_size],
        "is_ticker_specific": False,
        "sources_used": ["CafeF"],
        "debug_log": debug_log,
    }


def fetch_cafef_quarterly_full(ticker: str, quarters: list, debug: bool = False):
    slug = _find_company_slug(ticker)
    revenue, net_profit, equity, total_assets = {}, {}, {}, {}

    if not _cafef_is_reachable() or not quarters:
        return {"revenue": {}, "net_profit": {}, "equity": {}, "total_assets": {}}

    def fetch_task(q_tuple):
        year, q = q_tuple
        return f"{year}-Q{q}", _fetch_one_period(ticker, year, q, slug)

    # ĐA LUỒNG: Phóng cào 14 Quý cùng MỘT TÍCH TẮC
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
        "revenue": revenue, "net_profit": net_profit,
        "equity": equity, "total_assets": total_assets,
    }
