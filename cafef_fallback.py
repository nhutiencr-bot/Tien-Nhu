"""
cafef_fallback.py
------------------
Lớp dự phòng CUỐI CÙNG khi vnstock (VCI/KBS/DNSE) đều thất bại hoặc thiếu field.
Cào trực tiếp HTML công khai của CafeF.
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
    "Referer": "https://s.cafef.vn/",
}

# Giảm xuống 3s để thoát cực nhanh nếu bị chặn, không làm treo web hàng phút
REQUEST_TIMEOUT = 3  


@st.cache_data(ttl=300)  
def _cafef_is_reachable() -> bool:
    """Kiểm tra máy chủ s.cafef.vn có sống không trước khi chạy vòng lặp."""
    try:
        resp = requests.get("https://s.cafef.vn", headers=HEADERS, timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


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
    # Bóc HTML tags -> text thuần
    plain_text = re.sub(r'<[^>]+>', ' ', html_text)
    plain_text = re.sub(r'&nbsp;', ' ', plain_text)
    plain_text = re.sub(r'\s+', ' ', plain_text)

    pattern = re.compile(
        row_label_pattern + r'\s*((?:-?[\d.,]+\s*){1,40})', re.IGNORECASE
    )
    match = pattern.search(plain_text)
    if not match:
        return []

    numbers_blob = match.group(1)
    raw_numbers = re.findall(r'-?[\d][\d.,]*', numbers_blob)
    return [_parse_vn_number(n) for n in raw_numbers]


def _fetch_one_period(ticker: str, year: int, quarter: int, slug: str, debug: bool = False):
    """
    Cào 1 kỳ từ s.cafef.vn. Nếu bị Timeout, trả về {"TIMEOUT": True} để 
    các vòng lặp bên ngoài biết đường ngắt sớm, cứu web khỏi treo.
    """
    out = {}
    # SỬA LỖI URL: Đổi tên miền về s.cafef.vn, loại bỏ chữ /du-lieu/
    bsheet_url = f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}/bsheet/{year}/{quarter}/0/0/{slug}.chn"
    incsta_url = f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}/incsta/{year}/{quarter}/0/0/{slug}.chn"

    try:
        resp = requests.get(bsheet_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
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
            if equity_vals and equity_vals[-1] is not None:
                out['equity'] = equity_vals[-1] / 1e9
            if assets_vals and assets_vals[-1] is not None:
                out['total_assets'] = assets_vals[-1] / 1e9
    except requests.exceptions.Timeout:
        return {"TIMEOUT": True}
    except Exception:
        pass

    try:
        resp = requests.get(incsta_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            text = resp.text
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
            if revenue_vals and revenue_vals[-1] is not None:
                out['revenue'] = revenue_vals[-1] / 1e9
            if profit_vals and profit_vals[-1] is not None:
                out['net_profit'] = profit_vals[-1] / 1e9
    except requests.exceptions.Timeout:
        return {"TIMEOUT": True}
    except Exception:
        pass

    return out


@st.cache_data(ttl=3600 * 12)
def fetch_cafef_balance_sheet_5y(ticker: str, end_year: int):
    slug = _find_company_slug(ticker)
    equity_by_year = {}
    total_assets_by_year = {}

    if not _cafef_is_reachable():
        st.warning("⚠️ Máy chủ CafeF hiện không thể truy cập — Bỏ qua lớp dự phòng.")
        return {"equity": pd.Series(dtype=float), "total_assets": pd.Series(dtype=float)}

    for year in range(end_year - 4, end_year + 1):
        data = _fetch_one_period(ticker, year, 4, slug)
        
        # Cầu dao tự ngắt: Nếu CafeF timeout -> Break ngay lập tức cứu web khỏi treo
        if data.get("TIMEOUT"):
            st.warning("⚠️ CafeF phản hồi quá chậm. Đã ngắt kết nối sớm để tránh treo trang.")
            break
            
        if 'equity' in data:
            equity_by_year[year] = data['equity']
        if 'total_assets' in data:
            total_assets_by_year[year] = data['total_assets']
        time.sleep(0.15)

    return {
        "equity": pd.Series(equity_by_year).sort_index(),
        "total_assets": pd.Series(total_assets_by_year).sort_index(),
    }


@st.cache_data(ttl=3600 * 12)
def fetch_cafef_yearly_full(ticker: str, years: list, debug: bool = False):
    slug = _find_company_slug(ticker)
    revenue, net_profit, equity, total_assets = {}, {}, {}, {}
    empty = pd.Series(dtype=float)

    if not _cafef_is_reachable():
        return {"revenue": empty, "net_profit": empty, "equity": empty, "total_assets": empty, "roe": empty, "roa": empty}

    for year in years:
        data = _fetch_one_period(ticker, year, 4, slug, debug=debug)
        
        # Cầu dao tự ngắt
        if data.get("TIMEOUT"):
            break
            
        if 'revenue' in data:
            revenue[year] = data['revenue']
        if 'net_profit' in data:
            net_profit[year] = data['net_profit']
        if 'equity' in data:
            equity[year] = data['equity']
        if 'total_assets' in data:
            total_assets[year] = data['total_assets']
        time.sleep(0.15)

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
    slug = _find_company_slug(ticker)
    revenue, net_profit, equity, total_assets = {}, {}, {}, {}

    if not _cafef_is_reachable():
        return {"revenue": {}, "net_profit": {}, "equity": {}, "total_assets": {}}

    for year, q in quarters:
        key = f"{year}-Q{q}"
        data = _fetch_one_period(ticker, year, q, slug, debug=debug)
        
        # Cầu dao tự ngắt
        if data.get("TIMEOUT"):
            break
            
        if 'revenue' in data:
            revenue[key] = data['revenue']
        if 'net_profit' in data:
            net_profit[key] = data['net_profit']
        if 'equity' in data:
            equity[key] = data['equity']
        if 'total_assets' in data:
            total_assets[key] = data['total_assets']
        time.sleep(0.15)

    return {
        "revenue": revenue, "net_profit": net_profit,
        "equity": equity, "total_assets": total_assets,
    }


@st.cache_data(ttl=3600 * 12)
def fetch_cafef_market_snapshot(ticker: str):
    try:
        url = f"https://s.cafef.vn/du-lieu/tai-bao-cao-tai-chinh/{ticker.upper()}.chn"
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
        return {}
