"""
news_fetcher.py
---------------
Lấy tin tức liên quan đến mã cổ phiếu từ Google News RSS.
- Không cần API key, hoàn toàn miễn phí.
- Google News tự tổng hợp từ: CafeF, VnExpress, NDH, Vietstock, Nhịp Cầu Đầu Tư...
- Lọc chỉ giữ tin trong 6 tháng gần nhất.
- Fallback: nếu RSS lỗi, trả về list rỗng để pipeline xử lý.
"""

import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


# Bảng tên công ty đầy đủ để tăng chất lượng tìm kiếm
# (Google News tìm theo tên công ty cho kết quả tốt hơn chỉ tìm theo mã)
TICKER_NAME_MAP = {
    "BSR": "Lọc Hóa Dầu Bình Sơn",
    "FPT": "FPT Corporation",
    "VCB": "Vietcombank",
    "TCB": "Techcombank",
    "MBB": "MB Bank",
    "BID": "BIDV",
    "CTG": "VietinBank",
    "ACB": "ACB",
    "STB": "Sacombank",
    "HPG": "Hòa Phát",
    "VHM": "Vinhomes",
    "VIC": "Vingroup",
    "MSN": "Masan",
    "VNM": "Vinamilk",
    "SAB": "Sabeco",
    "GVR": "Cao su Việt Nam",
    "PLX": "Petrolimex",
    "POW": "PV Power",
    "GAS": "PV Gas",
    "VJC": "Vietjet",
    "HVN": "Vietnam Airlines",
    "MWG": "Thế Giới Di Động",
    "PNJ": "PNJ",
    "REE": "REE Corporation",
    "DPM": "Đạm Phú Mỹ",
    "DCM": "Đạm Cà Mau",
}

LOOKBACK_MONTHS = 6
MAX_NEWS = 20  # Số tin tối đa trả về

# Timeout cho request RSS — giảm từ 15s xuống 5s để không kéo chậm cả pipeline
# khi Google News phản hồi chậm hoặc mạng có vấn đề. Nếu timeout, hàm tự
# fallback về [] và pipeline sẽ dùng news từ vnstock thay thế.
RSS_TIMEOUT_SECONDS = 5


def _build_search_query(ticker: str) -> str:
    """Tạo câu truy vấn tìm kiếm tốt nhất cho mã cổ phiếu."""
    company_name = TICKER_NAME_MAP.get(ticker.upper(), "")
    if company_name:
        # Kết hợp cả mã và tên để tăng độ chính xác
        return f"{ticker} {company_name} cổ phiếu chứng khoán"
    else:
        return f"{ticker} cổ phiếu chứng khoán Việt Nam"


def _is_within_months(pub_date_str: str, months: int = LOOKBACK_MONTHS) -> bool:
    """Kiểm tra xem tin có nằm trong khoảng thời gian months tháng gần nhất không."""
    try:
        pub_dt = parsedate_to_datetime(pub_date_str)
        # Đảm bảo timezone-aware
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=months * 30)
        return pub_dt >= cutoff
    except Exception:
        return True  # Nếu không parse được ngày, giữ lại bài (an toàn hơn là bỏ)


def fetch_news_google_rss(ticker: str, max_results: int = MAX_NEWS) -> list[dict]:
    """
    Lấy tin tức từ Google News RSS cho mã cổ phiếu ticker.

    Trả về list of dict, mỗi dict có:
        title   : tiêu đề bài báo
        source  : tên nguồn (CafeF, VnExpress, ...)
        url     : đường dẫn bài gốc
        pub_date: ngày đăng (string dạng 'DD/MM/YYYY')

    Trả về [] nếu lỗi, timeout, hoặc không có tin — KHÔNG raise exception,
    để có thể gọi an toàn từ trong ThreadPoolExecutor của pipeline.py.
    """
    query = _build_search_query(ticker)
    encoded_query = urllib.parse.quote(query)
    rss_url = (
        f"https://news.google.com/rss/search"
        f"?q={encoded_query}&hl=vi&gl=VN&ceid=VN:vi"
    )

    try:
        req = urllib.request.Request(
            rss_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        # Timeout ngắn (5s thay vì 15s cũ) — nếu Google News chậm thì bỏ qua
        # nhanh và để pipeline fallback sang nguồn khác, tránh kéo chậm app.
        with urllib.request.urlopen(req, timeout=RSS_TIMEOUT_SECONDS) as response:
            content = response.read()

        tree = ET.fromstring(content)
        items = tree.findall(".//item")

        results = []
        for item in items:
            title_el   = item.find("title")
            link_el    = item.find("link")
            source_el  = item.find("source")
            pubdate_el = item.find("pubDate")

            title    = title_el.text   if title_el   is not None else ""
            url      = link_el.text    if link_el    is not None else ""
            source   = source_el.text  if source_el  is not None else "Google News"
            pub_date = pubdate_el.text if pubdate_el is not None else ""

            # Google News RSS luôn gắn " - Tên nguồn" vào cuối tiêu đề
            # (vd "...20 triệu đơn vị - nguoiquansat.vn") -> cắt bỏ phần này
            # vì tên nguồn đã hiển thị riêng ở dòng dưới rồi.
            title = title.strip()
            src_clean = (source or "").strip()
            if src_clean and title.endswith(f" - {src_clean}"):
                title = title[: -(len(src_clean) + 3)].strip()
            else:
                # Fallback: cắt cụm " - xxx.vn"/" - Tên Báo" cuối cùng nếu có,
                # phòng khi tên nguồn không khớp y hệt domain trong tiêu đề.
                title = re.sub(r"\s-\s[^-]{2,40}$", "", title).strip()

            # Bỏ qua tin quá cũ (hơn LOOKBACK_MONTHS tháng)
            if pub_date and not _is_within_months(pub_date, LOOKBACK_MONTHS):
                continue

            # Parse ngày thực để sắp xếp đúng thứ tự (mới nhất -> cũ nhất),
            # đồng thời tạo chuỗi hiển thị dd/mm/yyyy
            try:
                dt = parsedate_to_datetime(pub_date)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                pub_date_display = dt.strftime("%d/%m/%Y")
            except Exception:
                dt = datetime.min.replace(tzinfo=timezone.utc)
                pub_date_display = pub_date[:10] if pub_date else "—"

            # Bỏ qua bài không có tiêu đề hoặc tiêu đề quá ngắn
            if not title or len(title.strip()) < 10:
                continue

            results.append({
                "title":    title.strip(),
                "source":   source.strip() if source else "Google News",
                "url":      url.strip() if url else "#",
                "pub_date": pub_date_display,
                "_sort_dt": dt,
            })

        # Sắp xếp theo ngày thực, mới nhất lên đầu, rồi mới cắt còn max_results
        results.sort(key=lambda x: x["_sort_dt"], reverse=True)
        for r in results:
            del r["_sort_dt"]

        return results[:max_results]

    except Exception as e:
        # Không raise để tránh crash app — trả về rỗng, pipeline tự fallback
        print(f"[news_fetcher] Lỗi lấy tin RSS cho {ticker}: {e}")
        return []


def fetch_news_with_fallback(ticker: str, vnstock_news_cards: list, rss_news: list | None = None) -> list[dict]:
    """
    Hàm wrapper: ưu tiên dùng kết quả RSS đã có sẵn (nếu được truyền vào từ
    pipeline, ví dụ khi đã fetch song song trong ThreadPoolExecutor), nếu
    không có thì tự fetch RSS, cuối cùng mới fallback về vnstock news_cards.

    Trả về list of dict chuẩn hóa để render_tab_news() dùng được.

    Tham số:
        rss_news: kết quả của fetch_news_google_rss() đã fetch sẵn từ trước
                  (vd trong thread pool). Truyền vào để tránh gọi network
                  2 lần / chạy tuần tự sau khi pipeline đã xong việc khác.
    """
    if rss_news is None:
        rss_news = fetch_news_google_rss(ticker)

    if rss_news:
        return rss_news

    # Fallback: convert vnstock news_cards sang format giống RSS
    if vnstock_news_cards:
        return [
            {
                "title":    item.get("title", "Không có tiêu đề"),
                "source":   item.get("source", "vnstock"),
                "url":      item.get("url", "#"),
                "pub_date": item.get("pub_date", "—"),
            }
            for item in vnstock_news_cards
        ]

    return [{"title": "Không có tin tức trong 6 tháng gần nhất.", "source": "Hệ thống", "url": "#", "pub_date": "—"}]
