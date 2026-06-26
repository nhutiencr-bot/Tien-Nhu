"""
cafef_fallback.py
------------------
Lớp dự phòng CUỐI CÙNG khi vnstock (VCI/KBS/DNSE) đều thất bại hoặc thiếu
field (vd Vốn CSH/Tổng tài sản = None). Cào trực tiếp HTML công khai của
CafeF (không paywall, không cần JS) cho Cân đối kế toán (bsheet) và Kết
quả kinh doanh (incsta).

⚠️ GIỚI HẠN THỰC TẾ ĐÃ XÁC NHẬN:
- CafeF không phải API chính thức -> cấu trúc HTML CÓ THỂ thay đổi bất kỳ
  lúc nào mà không báo trước, khiến parser này ngừng hoạt động.
- URL cần đúng "slug tên công ty" ở cuối (vd 'can-doi-ke-toan-...-hoa-
  phat.chn'). Module này dò slug bằng cách tìm link "Báo cáo tài chính"
  trên trang tổng quan của mã trước, KHÔNG đoán slug.
- Streamlit Cloud chạy trên IP datacenter (AWS) -- CHƯA kiểm chứng được
  chắc chắn CafeF có chặn loại IP này hay không (khác với VCI/KBS đã xác
  nhận có chặn). Nếu gặp lỗi liên tục, đây là khả năng cao nhất.
- Đơn vị trong bảng là ĐỒNG TUYỆT ĐỐI (vd "31.075.075.510.406", dùng dấu
  '.' làm phân cách nghìn kiểu VN) dù header ghi "Đơn vị: tỷ đồng" -- đây
  là cách CafeF hiển thị, cần tự chia 1e9 để ra đúng tỷ đồng.
"""

import re
import time
import pandas as pd
import requests
import streamlit as st

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Referer": "https://cafef.vn/",
}

REQUEST_TIMEOUT = 10


def _find_company_slug(ticker: str) -> str | None:
    """
    Tìm đúng slug tên công ty (vd 'cong-ty-co-phan-tap-doan-hoa-phat') từ
    trang tổng quan CafeF của mã, KHÔNG đoán mò để tránh URL sai 404.
    Trả về None nếu không tìm được.
    """
    try:
        overview_url = f"https://cafef.vn/du-lieu/hose/{ticker.lower()}-x.chn"
        # Trang tổng quan đôi khi không cần đúng slug để redirect đúng mã,
        # nhưng để chắc ăn, dùng link "Báo cáo tài chính" tổng (tai-bao-cao-tai-chinh)
        # vì link này CHỈ cần mã, không cần slug đúng.
        fallback_url = f"https://cafef.vn/du-lieu/tai-bao-cao-tai-chinh/{ticker.lower()}.chn"
        resp = requests.get(fallback_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None

        # Tìm 1 link bsheet hoặc incsta có sẵn trong trang để trích slug thật
        match = re.search(
            r'/bao-cao-tai-chinh/' + re.escape(ticker.lower()) +
            r'/(?:bsheet|incsta)/\d{4}/\d/0/0/([a-z0-9\-]+)\.chn',
            resp.text, re.IGNORECASE,
        )
        if match:
            return match.group(1)
        return None
    except Exception:
        return None


def _parse_vn_number(raw: str):
    """
    Parse số kiểu CafeF: dùng '.' làm phân cách nghìn, ',' (nếu có) làm
    phân cách thập phân, có thể có dấu '-' ở đầu cho số âm.
    Trả về float hoặc None nếu không parse được (ô trống).
    """
    raw = raw.strip()
    if not raw:
        return None
    raw = raw.replace('.', '').replace(',', '.')
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_row_values(html_text: str, row_label_pattern: str):
    """
    Tìm 1 dòng trong bảng theo nhãn (regex), trích các số liệu theo SAU
    nhãn đó trên cùng dòng text (markdown table đã được web_fetch/requests
    trả về dạng text thuần với các số cách nhau bởi khoảng trắng).

    Trả về list[float|None], thứ tự từ cột cũ nhất -> mới nhất (theo đúng
    thứ tự xuất hiện trong HTML, CafeF luôn hiển thị trái->phải = cũ->mới).
    """
    pattern = re.compile(
        row_label_pattern + r'\s*((?:-?[\d.,]+\s*)+)', re.IGNORECASE
    )
    match = pattern.search(html_text)
    if not match:
        return []

    numbers_blob = match.group(1)
    raw_numbers = re.findall(r'-?[\d][\d.,]*', numbers_blob)
    return [_parse_vn_number(n) for n in raw_numbers]


@st.cache_data(ttl=3600 * 12)
def fetch_cafef_balance_sheet_5y(ticker: str, end_year: int):
    """
    Cào Vốn CSH + Tổng tài sản 5 năm gần nhất từ CafeF (lớp dự phòng
    cuối cùng). Gọi 1 request riêng cho mỗi năm với tham số quý=4 (Q4),
    vì cột cuối cùng trong response chính là số liệu CUỐI NĂM tài chính.

    Trả về dict {'equity': pd.Series, 'total_assets': pd.Series} theo
    đơn vị TỶ ĐỒNG (đã tự chia 1e9 từ số liệu đồng tuyệt đối của CafeF).
    Series rỗng nếu thất bại hoàn toàn.
    """
    slug = _find_company_slug(ticker)
    if not slug:
        st.warning(f"⚠️ [CafeF fallback] Không tìm được slug công ty cho mã {ticker} -- bỏ qua nguồn dự phòng này.")
        return {"equity": pd.Series(dtype=float), "total_assets": pd.Series(dtype=float)}

    equity_by_year = {}
    total_assets_by_year = {}

    for year in range(end_year - 4, end_year + 1):
        url = f"https://cafef.vn/du-lieu/bao-cao-tai-chinh/{ticker.lower()}/bsheet/{year}/4/0/0/{slug}.chn"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue

            text = resp.text

            equity_vals = _extract_row_values(text, r'D\.\s*VỐN CHỦ SỞ HỮU')
            assets_vals = _extract_row_values(text, r'TỔNG CỘNG TÀI SẢN')

            # Cột cuối cùng = kỳ gần nhất trong response = Q4 của `year`
            if equity_vals and equity_vals[-1] is not None:
                equity_by_year[year] = equity_vals[-1] / 1e9  # đồng -> tỷ đồng
            if assets_vals and assets_vals[-1] is not None:
                total_assets_by_year[year] = assets_vals[-1] / 1e9

            time.sleep(0.5)  # tránh dồn request quá nhanh

        except Exception as e:
            st.warning(f"⚠️ [CafeF fallback] Lỗi khi lấy BCTC {ticker} năm {year}: {e}")
            continue

    return {
        "equity": pd.Series(equity_by_year).sort_index(),
        "total_assets": pd.Series(total_assets_by_year).sort_index(),
    }


@st.cache_data(ttl=3600 * 12)
def fetch_cafef_market_snapshot(ticker: str):
    """
    Cào nhanh snapshot giá + vốn hóa hiện tại từ trang tổng quan CafeF --
    dùng làm lớp kiểm tra chéo cuối cùng cho Bẫy 6 (vốn hóa sai do số CP
    cũ) khi vnstock không có market_cap đáng tin.

    Trả về dict {'price': float, 'market_cap_billion': float} hoặc dict
    rỗng nếu thất bại.
    """
    try:
        url = f"https://cafef.vn/du-lieu/tai-bao-cao-tai-chinh/{ticker.lower()}.chn"
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return {}

        text = resp.text
        price_match = re.search(r'Giá cổ phiếu[^:]*:\s*([\d.,]+)\s*VNĐ', text)
        cap_match = re.search(r'Vốn hóa\s*(?:tt)?:?\s*([\d.,]+)\s*tỷ', text)

        result = {}
        if price_match:
            result['price'] = _parse_vn_number(price_match.group(1))
        if cap_match:
            result['market_cap_billion'] = _parse_vn_number(cap_match.group(1))
        return result

    except Exception as e:
        st.warning(f"⚠️ [CafeF fallback] Lỗi khi lấy snapshot giá/vốn hóa {ticker}: {e}")
        return {}
