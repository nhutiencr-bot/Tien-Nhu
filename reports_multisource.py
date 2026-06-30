"""
reports_multisource.py
-----------------------
Mở rộng tab "Báo cáo phân tích" sang nhiều nguồn miễn phí, không chỉ CafeF.
Thứ tự thử: CafeF (đã có, ổn định nhất) -> Vietstock (danh sách công khai,
không cần login) -> Simplize (API ngầm phía sau trang bao-cao).

LƯU Ý QUAN TRỌNG:
- Vietstock: phần lớn nội dung bị khoá sau đăng nhập, chỉ có DANH SÁCH
  TIÊU ĐỀ ở trang /bao-cao-phan-tich là công khai -> module này chỉ lấy
  tiêu đề + link + nguồn, lọc theo mã (giống cơ chế cafef_reports.py).
- Simplize: trang HTML trả về rỗng vì bảng load bằng JS gọi API. Hàm dưới
  đoán endpoint phổ biến (api2.simplize.vn). Nếu Anthropic đoán sai /
  endpoint đổi, hàm sẽ tự fail êm (trả về []) chứ không làm sập app -
  BẠN NÊN MỞ DevTools > Network trên trang
  https://simplize.vn/co-phieu/<MA>/bao-cao để lấy đúng URL API thật,
  rồi sửa biến SIMPLIZE_API_CANDIDATES bên dưới.
- Vì sandbox không gọi được các domain .vn nên toàn bộ phần này CHƯA được
  test trực tiếp - hãy chạy thử trên Streamlit Cloud và xem log/caption
  "Nguồn báo cáo" để biết nguồn nào thực sự hoạt động.

Cách dùng: thay `from cafef_reports import fetch_analysis_reports`
bằng `from reports_multisource import fetch_analysis_reports` trong pipeline.py
(giữ nguyên signature nên không cần sửa gì khác).
"""

import re
import requests

from cafef_reports import fetch_analysis_reports as _fetch_cafef_reports

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9",
}
TIMEOUT = 12

# ─────────────────────────────────────────────
# NGUỒN 2: Vietstock — danh sách công khai (không cần login)
# ─────────────────────────────────────────────
VIETSTOCK_LIST_URL = "https://finance.vietstock.vn/bao-cao-phan-tich"

_VIETSTOCK_LINK_RE = re.compile(
    r'href="(https?://finance\.vietstock\.vn/bao-cao-phan-tich/\d+/[^"]+\.htm)"[^>]*>\s*([^<]{8,200})\s*</a>',
    re.IGNORECASE,
)
_DATE_RE = re.compile(r'(\d{2}/\d{2}/\d{4})')


def _fetch_vietstock(ticker: str, max_results: int = 8):
    try:
        r = requests.get(VIETSTOCK_LIST_URL, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200 or not r.text:
            return []
        html = r.text
    except Exception:
        return []

    matches = list(_VIETSTOCK_LINK_RE.finditer(html))
    ticker_upper = (ticker or "").strip().upper()
    pattern = re.compile(rf'(^|[\s\(\[])({re.escape(ticker_upper)})([\s\-:\)\]]|$)') if ticker_upper else None

    results = []
    seen = set()
    for i, m in enumerate(matches):
        url, title = m.group(1).strip(), m.group(2).strip()
        if url in seen or len(title) < 8:
            continue
        if pattern and not pattern.search(title.upper()):
            continue
        seen.add(url)

        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else min(len(html), m.end() + 800)
        chunk = html[m.end():chunk_end]
        date_match = _DATE_RE.search(chunk)

        results.append({
            "title": title,
            "url": url,
            "source": "Vietstock",
            "pub_date": date_match.group(1) if date_match else "—",
        })
        if len(results) >= max_results:
            break
    return results


# ─────────────────────────────────────────────
# NGUỒN 3: Simplize — API ngầm (CẦN XÁC NHẬN LẠI ENDPOINT THẬT)
# ─────────────────────────────────────────────
SIMPLIZE_API_CANDIDATES = [
    "https://api2.simplize.vn/api/company/analysis-report/list/{ticker}?page=1&size=8",
    "https://simplize.vn/_next/data/latest/co-phieu/{ticker}/bao-cao.json",
]


def _fetch_simplize(ticker: str, max_results: int = 8):
    ticker_upper = (ticker or "").strip().upper()
    if not ticker_upper:
        return []
    for url_tpl in SIMPLIZE_API_CANDIDATES:
        url = url_tpl.format(ticker=ticker_upper)
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception:
            continue

        items = None
        if isinstance(data, dict):
            for key in ("data", "content", "items", "result"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        elif isinstance(data, list):
            items = data
        if not items:
            continue

        results = []
        for it in items[:max_results]:
            if not isinstance(it, dict):
                continue
            title = it.get("title") or it.get("name") or it.get("reportName")
            link = it.get("url") or it.get("link") or it.get("fileUrl")
            source = it.get("source") or it.get("issuer") or "Simplize"
            date = it.get("publishDate") or it.get("date") or "—"
            if title and link:
                results.append({
                    "title": str(title).strip(),
                    "url": str(link).strip(),
                    "source": str(source).strip(),
                    "pub_date": str(date)[:10] if date else "—",
                })
        if results:
            return results
    return []


# ─────────────────────────────────────────────
# PUBLIC API — giữ nguyên signature như cafef_reports.fetch_analysis_reports
# ─────────────────────────────────────────────
def fetch_analysis_reports(ticker: str, max_results: int = 8):
    """
    Gộp báo cáo từ nhiều nguồn miễn phí, ưu tiên báo cáo RIÊNG cho mã.
    Trả về dict {"reports": [...], "is_ticker_specific": bool, "sources_used": [...]}
    """
    all_reports = []
    sources_used = []

    # 1. CafeF (nguồn chính, ổn định nhất hiện nay)
    cafef_pkg = _fetch_cafef_reports(ticker, max_results=max_results)
    cafef_reports = cafef_pkg.get("reports", [])
    cafef_specific = cafef_pkg.get("is_ticker_specific", False)
    if cafef_reports:
        sources_used.append("CafeF")
        if cafef_specific:
            all_reports.extend(cafef_reports)

    # 2. Vietstock (chỉ lấy phần lọc theo mã - public list)
    try:
        vs_reports = _fetch_vietstock(ticker, max_results=max_results)
    except Exception:
        vs_reports = []
    if vs_reports:
        sources_used.append("Vietstock")
        all_reports.extend(vs_reports)

    # 3. Simplize (best-effort, có thể không hoạt động nếu endpoint sai)
    try:
        sp_reports = _fetch_simplize(ticker, max_results=max_results)
    except Exception:
        sp_reports = []
    if sp_reports:
        sources_used.append("Simplize")
        all_reports.extend(sp_reports)

    # Dedup theo URL
    seen_urls = set()
    deduped = []
    for r in all_reports:
        if r["url"] in seen_urls:
            continue
        seen_urls.add(r["url"])
        deduped.append(r)

    if deduped:
        return {
            "reports": deduped[:max_results * 2],
            "is_ticker_specific": True,
            "sources_used": sources_used,
        }

    # Không có báo cáo riêng cho mã từ bất kỳ nguồn nào -> fallback về
    # danh sách chung mới nhất của CafeF (như hành vi cũ)
    if cafef_reports:
        return {
            "reports": cafef_reports[:max_results],
            "is_ticker_specific": False,
            "sources_used": ["CafeF"],
        }

    return {"reports": [], "is_ticker_specific": False, "sources_used": []}
