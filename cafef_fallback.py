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


def _find_company_slug(ticker: str) -> str:
    """
    ⚠️ PHÁT HIỆN QUAN TRỌNG (đã verify qua nhiều mã thật): route bsheet/
    incsta của CafeF dùng MÃ + NĂM + QUÝ để tra dữ liệu -- phần "slug tên
    công ty" ở cuối URL chỉ mang tính trang trí/SEO, KHÔNG ảnh hưởng tới
    việc tra đúng dữ liệu. CafeF vẫn trả đúng response dù slug không khớp
    chính xác tên công ty thật (vd do công ty đổi tên, hoặc viết tắt khác).

    => Bỏ hẳn việc dò slug đúng qua HTTP request riêng (tốn thêm 1 request,
    dễ lỗi nếu trang trung gian đổi cấu trúc). Dùng 1 placeholder cố định
    hợp lệ về mặt cú pháp URL (chỉ chữ thường + gạch ngang).
    """
    return f"bao-cao-tai-chinh-{ticker.lower()}"


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


def _fetch_one_period(ticker: str, year: int, quarter: int, slug: str, debug: bool = False):
    """
    Cào 1 kỳ (năm + quý) từ CafeF, trả về dict các chỉ tiêu thô tìm được
    (đơn vị tỷ đồng, đã chia 1e9). Quarter=4 nghĩa là số liệu LŨY KẾ/CUỐI
    NĂM (CafeF không có báo cáo "riêng Q4", cột cuối của request quý=4
    chính là số liệu chốt năm cho bảng cân đối, và là số liệu QUÝ 4 cho
    KQKD vì KQKD CafeF hiển thị theo từng quý rời, không lũy kế).
    Trả về dict rỗng nếu lỗi/không tìm thấy.

    debug=True: in ra st.caption tình trạng HTTP + dòng nào khớp/không khớp,
    dùng để chẩn đoán khi CafeF đổi cấu trúc HTML hoặc chặn IP.
    """
    out = {}
    bsheet_url = f"https://cafef.vn/du-lieu/bao-cao-tai-chinh/{ticker.lower()}/bsheet/{year}/{quarter}/0/0/{slug}.chn"
    incsta_url = f"https://cafef.vn/du-lieu/bao-cao-tai-chinh/{ticker.lower()}/incsta/{year}/{quarter}/0/0/{slug}.chn"

    try:
        resp = requests.get(bsheet_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if debug:
            st.caption(f"🔍 [CafeF debug] BSHEET {year}-Q{quarter}: HTTP {resp.status_code}, "
                       f"độ dài HTML={len(resp.text) if resp.status_code == 200 else 0}, url={bsheet_url}")
        if resp.status_code == 200:
            text = resp.text
            equity_vals = (
                _extract_row_values(text, r'D\.\s*VỐN CHỦ SỞ HỮU') or
                _extract_row_values(text, r'VỐN CHỦ SỞ HỮU') or
                _extract_row_values(text, r'I\.\s*Vốn chủ sở hữu') or
                _extract_row_values(text, r'Vốn chủ sở hữu')
            )
            assets_vals = (
                _extract_row_values(text, r'TỔNG CỘNG TÀI SẢN') or
                _extract_row_values(text, r'TỔNG TÀI SẢN') or
                _extract_row_values(text, r'Tổng cộng tài sản')
            )
            if debug:
                st.caption(f"    → Vốn CSH khớp: {equity_vals[-3:] if equity_vals else 'KHÔNG TÌM THẤY dòng'}")
                st.caption(f"    → Tổng tài sản khớp: {assets_vals[-3:] if assets_vals else 'KHÔNG TÌM THẤY dòng'}")
            if equity_vals and equity_vals[-1] is not None:
                out['equity'] = equity_vals[-1] / 1e9
            if assets_vals and assets_vals[-1] is not None:
                out['total_assets'] = assets_vals[-1] / 1e9
    except Exception as e:
        if debug:
            st.caption(f"🔍 [CafeF debug] BSHEET {year}-Q{quarter} lỗi request: {e}")

    try:
        resp = requests.get(incsta_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if debug:
            st.caption(f"🔍 [CafeF debug] INCSTA {year}-Q{quarter}: HTTP {resp.status_code}, "
                       f"độ dài HTML={len(resp.text) if resp.status_code == 200 else 0}, url={incsta_url}")
        if resp.status_code == 200:
            text = resp.text
            # Doanh nghiệp thường: "Doanh thu thuần". Ngân hàng/CK/bảo hiểm
            # không có dòng này -> thử lần lượt các biến thể tên dòng KQKD
            # đặc thù (tổng thu nhập hoạt động, thu nhập lãi thuần, doanh thu
            # hoạt động, tổng doanh thu...).
            revenue_vals = (
                _extract_row_values(text, r'Doanh thu thuần') or
                _extract_row_values(text, r'TỔNG THU NHẬP HOẠT ĐỘNG') or
                _extract_row_values(text, r'Tổng thu nhập hoạt động') or
                _extract_row_values(text, r'Thu nhập lãi thuần') or
                _extract_row_values(text, r'Doanh thu hoạt động') or
                _extract_row_values(text, r'Tổng doanh thu hoạt động') or
                _extract_row_values(text, r'Tổng doanh thu') or
                _extract_row_values(text, r'Doanh thu bán hàng và cung cấp dịch vụ') or
                _extract_row_values(text, r'Doanh thu')
            )
            profit_vals = (
                _extract_row_values(text, r'Lợi nhuận sau thuế thu nhập doanh nghiệp') or
                _extract_row_values(text, r'LỢI NHUẬN SAU THUẾ') or
                _extract_row_values(text, r'Lợi nhuận sau thuế') or
                _extract_row_values(text, r'Lãi/\s*\(lỗ\) thuần sau thuế') or
                _extract_row_values(text, r'Lợi nhuận thuần sau thuế')
            )
            if debug:
                st.caption(f"    → Doanh thu khớp: {revenue_vals[-3:] if revenue_vals else 'KHÔNG TÌM THẤY dòng'}")
                st.caption(f"    → LNST khớp: {profit_vals[-3:] if profit_vals else 'KHÔNG TÌM THẤY dòng'}")
            if revenue_vals and revenue_vals[-1] is not None:
                out['revenue'] = revenue_vals[-1] / 1e9
            if profit_vals and profit_vals[-1] is not None:
                out['net_profit'] = profit_vals[-1] / 1e9
    except Exception as e:
        if debug:
            st.caption(f"🔍 [CafeF debug] INCSTA {year}-Q{quarter} lỗi request: {e}")

    return out


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

    equity_by_year = {}
    total_assets_by_year = {}

    for year in range(end_year - 4, end_year + 1):
        data = _fetch_one_period(ticker, year, 4, slug)
        if 'equity' in data:
            equity_by_year[year] = data['equity']
        if 'total_assets' in data:
            total_assets_by_year[year] = data['total_assets']
        time.sleep(0.4)

    return {
        "equity": pd.Series(equity_by_year).sort_index(),
        "total_assets": pd.Series(total_assets_by_year).sort_index(),
    }


@st.cache_data(ttl=3600 * 12)
def fetch_cafef_yearly_full(ticker: str, years: list, debug: bool = False):
    """
    Cào ĐẦY ĐỦ Doanh thu thuần, LNST, Vốn CSH, Tổng tài sản cho danh sách
    năm chỉ định (vd [2021, 2022, ...]) -- dùng để nối thêm năm cũ (2021)
    mà vnstock không cung cấp (vnstock chỉ trả 4 năm gần nhất).

    Trả về dict {'revenue':Series, 'net_profit':Series, 'equity':Series,
    'total_assets':Series, 'roe':Series, 'roa':Series} theo TỶ ĐỒNG.
    ROE/ROA tự tính = LNST / Vốn CSH (hoặc Tổng tài sản) * 100, đây là
    CÔNG THỨC GẦN ĐÚNG (không phải số ROE/ROA chính thức công bố), dùng
    khi không có cách nào khác.
    """
    slug = _find_company_slug(ticker)

    revenue, net_profit, equity, total_assets = {}, {}, {}, {}

    for year in years:
        data = _fetch_one_period(ticker, year, 4, slug, debug=debug)
        if 'revenue' in data:
            revenue[year] = data['revenue']
        if 'net_profit' in data:
            net_profit[year] = data['net_profit']
        if 'equity' in data:
            equity[year] = data['equity']
        if 'total_assets' in data:
            total_assets[year] = data['total_assets']
        time.sleep(0.4)

    revenue_s, profit_s = pd.Series(revenue).sort_index(), pd.Series(net_profit).sort_index()
    equity_s, assets_s = pd.Series(equity).sort_index(), pd.Series(total_assets).sort_index()

    roe = (profit_s / equity_s.replace(0, float('nan')) * 100) if not equity_s.empty else pd.Series(dtype=float)
    roa = (profit_s / assets_s.replace(0, float('nan')) * 100) if not assets_s.empty else pd.Series(dtype=float)

    return {
        "revenue": revenue_s, "net_profit": profit_s,
        "equity": equity_s, "total_assets": assets_s,
        "roe": roe.dropna(), "roa": roa.dropna(),
    }


@st.cache_data(ttl=3600 * 12)
def fetch_cafef_quarterly_full(ticker: str, quarters: list, debug: bool = False):
    """
    Cào dữ liệu THEO QUÝ từ CafeF cho danh sách (year, quarter) chỉ định
    -- dùng để bù các quý cũ mà vnstock không trả về (vnstock chỉ ổn định
    4 quý gần nhất).

    quarters: list các tuple (year:int, quarter:int), vd [(2022,1),(2022,2),...]

    Trả về dict {'revenue':{key:val}, 'net_profit':{...}, 'equity':{...},
    'total_assets':{...}} với key dạng chuỗi "YYYY-Qn", giá trị TỶ ĐỒNG.
    Quý nào lỗi/không có dữ liệu sẽ bị bỏ qua (không có key đó).

    ⚠️ Lưu ý: với LNST/Doanh thu, CafeF KQKD hiển thị số liệu RIÊNG của
    từng quý (không lũy kế) nên dùng được trực tiếp. Với Vốn CSH/Tổng tài
    sản (bảng cân đối), số liệu vốn dĩ là CUỐI KỲ nên cũng dùng trực tiếp.
    """
    slug = _find_company_slug(ticker)

    revenue, net_profit, equity, total_assets = {}, {}, {}, {}

    for year, q in quarters:
        key = f"{year}-Q{q}"
        data = _fetch_one_period(ticker, year, q, slug, debug=debug)
        if 'revenue' in data:
            revenue[key] = data['revenue']
        if 'net_profit' in data:
            net_profit[key] = data['net_profit']
        if 'equity' in data:
            equity[key] = data['equity']
        if 'total_assets' in data:
            total_assets[key] = data['total_assets']
        time.sleep(0.4)

    return {
        "revenue": revenue, "net_profit": net_profit,
        "equity": equity, "total_assets": total_assets,
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
