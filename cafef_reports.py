"""
cafef_reports.py
-----------------
Lấy "Báo cáo phân tích" (khuyến nghị từ các công ty chứng khoán) từ trang
CafeF: https://cafef.vn/du-lieu/phan-tich-bao-cao.chn

Trang này hiển thị danh sách báo cáo mới nhất trên toàn thị trường (không có
tham số URL để lọc theo mã do phần lọc chạy bằng JavaScript/AJAX phía client).
Vì vậy module này:
  1. Cào toàn bộ danh sách báo cáo mới nhất hiển thị trên trang.
  2. Lọc ra những báo cáo có liên quan tới mã cổ phiếu đang xem (dựa vào quy
     ước đặt tiêu đề báo cáo của CafeF, ví dụ "MBB - Tự tin duy trì...").
  3. Nếu không có báo cáo riêng cho mã đó, trả về danh sách báo cáo chung mới
     nhất kèm cờ `is_ticker_specific=False` để giao diện hiển thị ghi chú phù hợp.

Dùng chung Session/Headers với cafef_fallback.py để tái sử dụng kết nối và
cơ chế kiểm tra reachability đã có sẵn.
"""

import re
from cafef_fallback import _SESSION, REQUEST_TIMEOUT, _cafef_is_reachable

REPORTS_URL = "https://cafef.vn/du-lieu/phan-tich-bao-cao.chn"
MAX_REPORTS_DEFAULT = 8

# Regex bắt từng khối báo cáo: link bài viết (report/...chn) + tiêu đề trong anchor
_REPORT_LINK_RE = re.compile(
    r'href="(https://cafef\.vn/du-lieu/report/[^"]+\.chn[^"]*)"[^>]*>\s*([^<]{8,200})\s*</a>',
    re.IGNORECASE,
)
_SOURCE_RE = re.compile(r'Ngu[oồ]n:\s*([A-Za-zÀ-ỹ0-9\s\.\-&]{2,40})')
_DATE_RE = re.compile(r'(\d{2}/\d{2}/\d{4})')


def _fetch_raw_html():
    if not _cafef_is_reachable():
        return ""
    try:
        resp = _SESSION.get(REPORTS_URL, timeout=REQUEST_TIMEOUT + 2)
        return resp.text if resp.status_code == 200 else ""
    except Exception:
        return ""


def _parse_reports(html_text: str, max_results: int):
    if not html_text:
        return []

    matches = list(_REPORT_LINK_RE.finditer(html_text))
    results = []
    seen_urls = set()

    for i, m in enumerate(matches):
        url, title = m.group(1).strip(), m.group(2).strip()
        if url in seen_urls:
            continue
        if not title or len(title) < 8:
            continue
        seen_urls.add(url)

        # Lấy đoạn HTML ngay sau link này (tới link tiếp theo) để tìm nguồn + ngày
        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else min(len(html_text), m.end() + 1500)
        chunk = html_text[m.end():chunk_end]

        source_match = _SOURCE_RE.search(chunk)
        date_match = _DATE_RE.search(chunk)

        results.append({
            "title": title,
            "url": url,
            "source": source_match.group(1).strip() if source_match else "CafeF",
            "pub_date": date_match.group(1) if date_match else "—",
        })

        if len(results) >= max_results * 4:  # cào dư để còn lọc theo mã
            break

    return results


def fetch_analysis_reports(ticker: str, max_results: int = MAX_REPORTS_DEFAULT):
    """
    Trả về dict: {"reports": [...], "is_ticker_specific": bool}
    Mỗi report có: title, url, source, pub_date.
    """
    html_text = _fetch_raw_html()
    all_reports = _parse_reports(html_text, max_results)

    ticker_upper = (ticker or "").strip().upper()
    if ticker_upper:
        pattern = re.compile(rf'(^|[\s\(\[])({re.escape(ticker_upper)})([\s\-:\)\]]|$)')
        ticker_reports = [r for r in all_reports if pattern.search(r["title"].upper())]
    else:
        ticker_reports = []

    if ticker_reports:
        return {"reports": ticker_reports[:max_results], "is_ticker_specific": True}

    return {"reports": all_reports[:max_results], "is_ticker_specific": False}
