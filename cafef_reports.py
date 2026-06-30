"""
cafef_reports.py
-----------------
Lấy "Báo cáo phân tích" (khuyến nghị từ các công ty chứng khoán) từ trang
CafeF: https://cafef.vn/du-lieu/phan-tich-bao-cao.chn

Dùng lxml.html để parse DOM thật (thay vì regex) -> không phụ thuộc việc
tiêu đề có nằm trực tiếp trong <a> hay lồng trong <span>/<h3> bên trong.

LƯU Ý: hàm này KHÔNG còn phụ thuộc vào _cafef_is_reachable() (vốn kiểm tra
domain s.cafef.vn - chỉ dùng để cào BCTC, khác domain với trang báo cáo
cafef.vn). Một domain phụ bị chặn/timeout không còn làm rỗng cả tab báo cáo.
"""

import re
import requests
import lxml.html

REPORTS_URL = "https://cafef.vn/du-lieu/phan-tich-bao-cao.chn"
MAX_REPORTS_DEFAULT = 8
REQUEST_TIMEOUT = 8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Referer": "https://cafef.vn/",
}

_SOURCE_RE = re.compile(r'Ngu[oồ]n:\s*([A-Za-zÀ-ỹ&\.\-]+(?:\s+[A-Za-zÀ-ỹ&\.\-]+){0,4})')
_DATE_RE = re.compile(r'(\d{2}/\d{2}/\d{4})')
# Href báo cáo thật: https://cafef.vn/du-lieu/report/{slug}-{hexid}.chn[?query]
_REPORT_HREF_RE = re.compile(r'^https?://cafef\.vn/du-lieu/report/.+\.chn(\?.*)?$', re.IGNORECASE)


def _fetch_raw_html():
    try:
        resp = requests.get(REPORTS_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        return resp.text if resp.status_code == 200 else ""
    except Exception:
        return ""


def _parse_reports(html_text: str, max_results: int):
    if not html_text:
        return []

    try:
        tree = lxml.html.fromstring(html_text)
    except Exception:
        return []

    results = []
    seen_urls = set()

    for a in tree.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        if not _REPORT_HREF_RE.match(href):
            continue
        if href in seen_urls:
            continue

        # text_content() lấy TOÀN BỘ text con (kể cả nếu nằm trong <span>/<h3>),
        # bỏ qua các <a> chỉ chứa <img> (không có text -> độ dài quá ngắn).
        title = " ".join(a.text_content().split()).strip()
        if len(title) < 8:
            continue
        seen_urls.add(href)

        # Lấy 1 đoạn text xung quanh thẻ <a> (cha + các anh em kế tiếp) để tìm
        # nguồn phát hành + ngày đăng, vì 2 thông tin này thường nằm ngay sau
        # tiêu đề trong cùng 1 khối "card" báo cáo.
        parent = a.getparent()
        context_text = ""
        if parent is not None:
            try:
                context_text = " ".join(parent.itertext())
                nxt = parent.getnext()
                if nxt is not None:
                    context_text += " " + " ".join(nxt.itertext())
            except Exception:
                pass

        source_match = _SOURCE_RE.search(context_text)
        date_match = _DATE_RE.search(context_text)

        results.append({
            "title": title,
            "url": href,
            "source": source_match.group(1).strip() if source_match else "CafeF",
            "pub_date": date_match.group(1) if date_match else "—",
        })

        if len(results) >= max_results * 6:  # cào dư để còn lọc theo mã
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
