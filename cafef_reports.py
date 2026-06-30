import re
import requests
from bs4 import BeautifulSoup
import streamlit as st

REC_KEYWORDS = [
    "MUA", "BÁN", "TĂNG TỈ TRỌNG", "TĂNG TỶ TRỌNG",
    "GIẢM TỈ TRỌNG", "GIẢM TỶ TRỌNG", "NẮM GIỮ",
    "TRUNG LẬP", "KHẢ QUAN", "THEO DÕI", "PHÙ HỢP THỊ TRƯỜNG",
]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_cafef_reports(ticker: str, limit: int = 10):
    """Cào danh sách báo cáo phân tích từ CafeF cho 1 mã, parse khuyến nghị + giá mục tiêu từ tiêu đề."""
    url = f"https://s.cafef.vn/bao-cao-phan-tich/{ticker.lower()}.chn"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "vi-VN,vi;q=0.9",
        "Referer": "https://s.cafef.vn/",
    }
    out = []
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        candidates = soup.select("a[href]")
        seen_links = set()

        for a in candidates:
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href or not title or len(title) < 15:
                continue
            if ".chn" not in href:
                continue
            if "report" not in href.lower() and ticker.lower() not in href.lower():
                continue
            if href in seen_links:
                continue
            seen_links.add(href)

            if not href.startswith("http"):
                href = "https://s.cafef.vn" + (href if href.startswith("/") else "/" + href)

            rec = "—"
            for kw in REC_KEYWORDS:
                if kw in title.upper():
                    rec = kw
                    break

            target_price = None
            m = re.search(r"(?:giá mục tiêu|gmt)[:\s]*([\d.,]+)\s*(?:vnđ|đồng|đ)?", title, re.IGNORECASE)
            if m:
                raw = m.group(1).replace(".", "").replace(",", "")
                try:
                    target_price = float(raw)
                except ValueError:
                    target_price = None

            source_match = re.search(r"-\s*([A-Za-zÀ-ỹ ]{2,30})\s*$", title)
            source = source_match.group(1).strip() if source_match else "—"

            out.append({
                "ticker": ticker.upper(),
                "recommendation": rec,
                "target_price": target_price,
                "report_date": "—",
                "source": source,
                "url": href,
                "title": title,
            })
            if len(out) >= limit:
                break
    except Exception:
        return []
    return out
