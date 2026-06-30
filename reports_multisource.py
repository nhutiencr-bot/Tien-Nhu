"""
reports_multisource.py
-----------------------
Mở rộng tab "Báo cáo phân tích" sang nhiều nguồn miễn phí, không chỉ CafeF.
Thứ tự thử: CafeF (parser dùng lxml, xem cafef_reports.py) -> Vietstock
(danh sách công khai /bao-cao-phan-tich, không cần login) -> Simplize
(best-effort qua API ngầm, có thể chưa hoạt động).

Cả 2 nguồn CafeF + Vietstock đều dùng lxml.html để parse DOM thật thay vì
regex trên text thô -> không bị gãy khi tiêu đề báo cáo nằm lồng trong
<span>/<h3> hay có ảnh thumbnail kèm theo trong cùng 1 khối.
"""

import re
import requests
import lxml.html

from cafef_reports import fetch_analysis_reports as _fetch_cafef_reports

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9",
}
TIMEOUT = 12

# ─────────────────────────────────────────────
# NGUỒN 2: Vietstock — danh sách công khai (không cần login)
# Mẫu link thật: https://finance.vietstock.vn/bao-cao-phan-tich/20925/
#                bmp-khuyen-nghi-kha-quan-voi-gia-muc-tieu-168500-...htm
# ─────────────────────────────────────────────
VIETSTOCK_LIST_URL = "https://finance.vietstock.vn/bao-cao-phan-tich"
_VIETSTOCK_HREF_RE = re.compile(
    r'^https?://finance\.vietstock\.vn/bao-cao-phan-tich/\d+/.+\.htm$', re.IGNORECASE
)
_DATE_RE = re.compile(r'(\d{2}/\d{2}/\d{4})')


def _fetch_vietstock(ticker: str, max_results: int = 8):
    try:
        r = requests.get(VIETSTOCK_LIST_URL, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200 or not r.text:
            return []
        tree = lxml.html.fromstring(r.text)
    except Exception:
        return []

    ticker_upper = (ticker or "").strip().upper()
    # Tiêu đề Vietstock thường ở dạng "BMP: Khuyến nghị..." hoặc "BMP - ..."
    # nên chỉ cần khớp TICKER ở đầu câu, theo sau bởi dấu câu/khoảng trắng.
    pattern = re.compile(rf'^{re.escape(ticker_upper)}([\s\-:,]|$)') if ticker_upper else None

    results = []
    seen = set()
    for a in tree.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        if not _VIETSTOCK_HREF_RE.match(href):
            continue
        if href in seen:
            continue
        title = " ".join(a.text_content().split()).strip()
        if len(title) < 8:
            continue
        if pattern and not pattern.search(title.upper()):
            continue
        seen.add(href)

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
        date_match = _DATE_RE.search(context_text)
        source_match = re.search(r'Ngu[oồ]n:\s*([A-Za-zÀ-ỹ&\.\-]+)', context_text)

        results.append({
            "title": title,
            "url": href,
            "source": source_match.group(1).strip() if source_match else "Vietstock",
            "pub_date": date_match.group(1) if date_match else "—",
        })
        if len(results) >= max_results:
            break
    return results


# ─────────────────────────────────────────────
# NGUỒN 3: Simplize — API ngầm (best-effort, CÓ THỂ CHƯA HOẠT ĐỘNG)
# Trang simplize.vn/co-phieu/{MA}/bao-cao load bảng báo cáo bằng JS gọi
# API riêng. Nếu URL dưới sai, hàm tự fail êm (trả về []) chứ không
# làm hỏng các nguồn khác. Mở DevTools > Network trên trang đó để lấy
# đúng endpoint thật nếu muốn nguồn này hoạt động.
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


def _raw_probe(url, label, debug_log):
    """Gọi thẳng 1 URL để xem status code / độ dài response thật."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        snippet = (r.text or "")[:120].replace("\n", " ")
        debug_log.append(
            f"  [probe] {label}: HTTP {r.status_code}, {len(r.text or '')} ký tự, "
            f"đầu trang: {snippet!r}"
        )
    except Exception as e:
        debug_log.append(f"  [probe] {label}: LỖI -> {type(e).__name__}: {e}")


# ─────────────────────────────────────────────
# PUBLIC API — giữ nguyên signature như cafef_reports.fetch_analysis_reports
# ─────────────────────────────────────────────
def fetch_analysis_reports(ticker: str, max_results: int = 8, debug: bool = False):
    """
    Gộp báo cáo từ nhiều nguồn miễn phí, ưu tiên báo cáo RIÊNG cho mã.
    Trả về dict {"reports": [...], "is_ticker_specific": bool, "sources_used": [...], "debug_log": [...]}
    """
    all_reports = []
    sources_used = []
    debug_log = []

    debug_log.append("--- RAW PROBE (bỏ qua mọi logic parse) ---")
    _raw_probe("https://cafef.vn/du-lieu/phan-tich-bao-cao.chn", "cafef.vn/du-lieu/phan-tich-bao-cao.chn", debug_log)
    _raw_probe(VIETSTOCK_LIST_URL, "finance.vietstock.vn/bao-cao-phan-tich", debug_log)
    _raw_probe(
        SIMPLIZE_API_CANDIDATES[0].format(ticker=(ticker or "").upper()),
        "Simplize API candidate #1", debug_log,
    )
    debug_log.append("--- KẾT QUẢ TỪNG NGUỒN SAU PARSE ---")

    # 1. CafeF (nguồn chính, ổn định nhất hiện nay)
    try:
        cafef_pkg = _fetch_cafef_reports(ticker, max_results=max_results)
        cafef_reports = cafef_pkg.get("reports", [])
        cafef_specific = cafef_pkg.get("is_ticker_specific", False)
        debug_log.append(f"CafeF: lấy được {len(cafef_reports)} báo cáo (specific={cafef_specific})")
    except Exception as e:
        cafef_reports, cafef_specific = [], False
        debug_log.append(f"CafeF: LỖI -> {type(e).__name__}: {e}")
    if cafef_reports:
        sources_used.append("CafeF")
        if cafef_specific:
            all_reports.extend(cafef_reports)

    # 2. Vietstock (chỉ lấy phần lọc theo mã - public list)
    try:
        vs_reports = _fetch_vietstock(ticker, max_results=max_results)
        debug_log.append(f"Vietstock: lấy được {len(vs_reports)} báo cáo khớp mã")
    except Exception as e:
        vs_reports = []
        debug_log.append(f"Vietstock: LỖI -> {type(e).__name__}: {e}")
    if vs_reports:
        sources_used.append("Vietstock")
        all_reports.extend(vs_reports)

    # 3. Simplize (best-effort, có thể không hoạt động nếu endpoint sai)
    try:
        sp_reports = _fetch_simplize(ticker, max_results=max_results)
        debug_log.append(f"Simplize: lấy được {len(sp_reports)} báo cáo")
    except Exception as e:
        sp_reports = []
        debug_log.append(f"Simplize: LỖI -> {type(e).__name__}: {e}")
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
            "debug_log": debug_log,
        }

    # Không có báo cáo riêng cho mã từ bất kỳ nguồn nào -> fallback về
    # danh sách chung mới nhất của CafeF (như hành vi cũ)
    if cafef_reports:
        return {
            "reports": cafef_reports[:max_results],
            "is_ticker_specific": False,
            "sources_used": ["CafeF"],
            "debug_log": debug_log,
        }

    return {"reports": [], "is_ticker_specific": False, "sources_used": [], "debug_log": debug_log}
