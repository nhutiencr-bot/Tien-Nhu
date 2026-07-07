"""
cafef_fallback.py
-----------------
Scrape bảng cân đối kế toán (Vốn CSH + Tổng tài sản) từ CafeF
khi vnstock không có dữ liệu năm cũ (2021).

ĐƠN VỊ CafeF: Triệu đồng (VNĐ)
→ Hàm này trả về Series theo đơn vị TỶ (chia 1e3 từ triệu).
→ pipeline.py gọi hàm này rồi dùng trực tiếp, KHÔNG normalize thêm.

QUAN TRỌNG: Không gọi normalize_to_billion_vnd() trên kết quả của hàm này,
vì đã convert trong _parse_cafef_value().
"""

import re
import requests
import pandas as pd
from bs4 import BeautifulSoup


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

_SESSION = requests.Session()
_SESSION.headers.update(_HEADERS)


def _parse_cafef_value(raw: str) -> float | None:
    """
    Parse số CafeF → float (đơn vị TỶ VNĐ).

    CafeF hiển thị số theo TRIỆU ĐỒNG, dùng dấu ',' phân cách nghìn
    và '.' cho thập phân (format quốc tế).

    VD:  "30,058,172"  → 30058172 triệu → chia 1e3 → 30,058.172 tỷ
         "544,654.40"  → 544654.40 triệu → chia 1e3 → 544.654 tỷ
         "1.234"       → ambiguous; nếu không có dấu phẩy → xét ngữ cảnh
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip().replace('\xa0', '').replace(' ', '')
    if raw in ('', '-', '—', 'N/A', 'n/a'):
        return None
    try:
        # Xác định format: nếu có cả ',' và '.' thì ',' là phân cách nghìn
        if ',' in raw and '.' in raw:
            # VD: "30,058,172.50" → bỏ ',' → "30058172.50"
            cleaned = raw.replace(',', '')
        elif ',' in raw and '.' not in raw:
            # VD: "30,058,172" → dấu phẩy là phân cách nghìn
            cleaned = raw.replace(',', '')
        elif '.' in raw and ',' not in raw:
            # VD: "544654.40" → dấu chấm là thập phân
            # VD: "1.234" → ambiguous, nhưng trong ngữ cảnh CafeF (tỷ số lớn)
            # nếu chỉ có 3 số sau dấu chấm và không có dấu phẩy → thập phân
            cleaned = raw
        else:
            cleaned = raw

        val_million = float(cleaned)

        # Sanity check: CafeF báo triệu đồng
        # Vốn CSH ngân hàng lớn ~500,000 triệu = 500 tỷ → giá trị raw ≈ 500000
        # Nếu val_million > 1e9 → có thể đã là đồng → chia thêm 1e6
        if abs(val_million) > 1e9:
            return round(val_million / 1e9, 2)  # đồng → tỷ

        # Triệu → tỷ: chia 1e3
        return round(val_million / 1e3, 2)

    except (ValueError, TypeError):
        return None


def _build_cafef_url(ticker: str, report_type: int = 1, year: int = 0) -> str:
    """
    report_type: 1=Quý, 2=Năm
    year: 0=mới nhất, hoặc năm cụ thể
    """
    return (
        f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}"
        f"/CDKT/{year}/{report_type}/0/0/0/bao-cao-tai-chinh-.chn"
    )


def _scrape_cafef_balance(ticker: str, year: int = 0) -> dict[str, dict[int, float]]:
    """
    Scrape bảng CĐKT từ CafeF. Trả về dict:
    {
      'equity':       {năm: giá_trị_tỷ},
      'total_assets': {năm: giá_trị_tỷ},
    }
    """
    url = _build_cafef_url(ticker, report_type=2, year=year)
    try:
        resp = _SESSION.get(url, timeout=15)
        resp.raise_for_status()
    except Exception:
        return {'equity': {}, 'total_assets': {}}

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Tìm bảng tài chính
    table = soup.find('table', {'id': re.compile(r'tblGridData|tableContent|Grid', re.I)})
    if table is None:
        # Fallback: tìm bảng đầu tiên có nhiều cột
        tables = soup.find_all('table')
        table = next((t for t in tables if len(t.find_all('th')) >= 3), None)
    if table is None:
        return {'equity': {}, 'total_assets': {}}

    # Parse header để lấy danh sách năm
    years = []
    header_row = table.find('tr')
    if header_row:
        cells = header_row.find_all(['th', 'td'])
        for cell in cells[1:]:
            txt = cell.get_text(strip=True)
            m = re.search(r'20\d{2}', txt)
            if m:
                years.append(int(m.group()))

    if not years:
        return {'equity': {}, 'total_assets': {}}

    # Keywords để tìm dòng cần thiết
    EQUITY_KEYS = [
        'vốn chủ sở hữu', 'tổng vốn chủ sở hữu',
        'b. vốn chủ sở hữu', 'ii. vốn chủ sở hữu',
        "equity", "owner's equity", "shareholders' equity",
    ]
    TOTAL_ASSETS_KEYS = [
        'tổng cộng tài sản', 'tổng tài sản',
        'a+b', 'tổng cộng nguồn vốn',
        'total assets',
    ]

    equity_vals = {}
    total_assets_vals = {}

    rows = table.find_all('tr')[1:]
    for row in rows:
        cells = row.find_all(['td', 'th'])
        if not cells:
            continue
        label = cells[0].get_text(strip=True).lower()
        data_cells = cells[1:]

        is_equity = any(k in label for k in EQUITY_KEYS)
        is_ta     = any(k in label for k in TOTAL_ASSETS_KEYS)

        if not is_equity and not is_ta:
            continue

        for i, cell in enumerate(data_cells):
            if i >= len(years):
                break
            val = _parse_cafef_value(cell.get_text(strip=True))
            if val is None:
                continue
            y = years[i]
            if is_equity and y not in equity_vals:
                equity_vals[y] = val
            if is_ta and y not in total_assets_vals:
                total_assets_vals[y] = val

    return {'equity': equity_vals, 'total_assets': total_assets_vals}


def fetch_cafef_balance_sheet_5y(
    ticker: str,
    end_year: int | None = None,
) -> dict[str, pd.Series]:
    """
    Public API: lấy Vốn CSH + Tổng TS từ CafeF cho ~5 năm gần nhất.

    Trả về:
    {
      'equity':       pd.Series (index=năm, values=tỷ VNĐ),
      'total_assets': pd.Series (index=năm, values=tỷ VNĐ),
    }

    Nếu lỗi → trả về 2 Series rỗng (không crash pipeline).
    """
    empty = {'equity': pd.Series(dtype=float), 'total_assets': pd.Series(dtype=float)}
    try:
        data = _scrape_cafef_balance(ticker, year=0)

        # CafeF default trả 5 năm gần nhất (2022–2026 khi đang ở 2026).
        # Merge thêm trang year=2021 để đảm bảo có đủ 2021–2025.
        data_old = _scrape_cafef_balance(ticker, year=2021)
        for k in ('equity', 'total_assets'):
            for yr, val in data_old.get(k, {}).items():
                data.setdefault(k, {})[yr] = data[k].get(yr) or val

        eq_series = pd.Series(data.get('equity', {}), dtype=float).sort_index()
        ta_series = pd.Series(data.get('total_assets', {}), dtype=float).sort_index()

        # Sanity check: loại bỏ giá trị bất thường (< 0 hoặc > 50,000 tỷ cho từng chỉ số)
        eq_series = eq_series[(eq_series > 0) & (eq_series < 5e7)]
        ta_series = ta_series[(ta_series > 0) & (ta_series < 5e7)]

        return {'equity': eq_series, 'total_assets': ta_series}

    except Exception:
        return empty


def _scrape_cafef_income(ticker: str, year: int = 0) -> dict:
    """Scrape KQKD từ CafeF — trả về revenue + net_profit theo năm (đơn vị: tỷ VNĐ)."""
    url = _build_cafef_url(ticker, report_type=2, year=year).replace('/CDKT/', '/KQKD/')
    try:
        resp = _SESSION.get(url, timeout=15)
        resp.raise_for_status()
    except Exception:
        return {'revenue': {}, 'net_profit': {}}

    soup = BeautifulSoup(resp.text, 'html.parser')
    table = soup.find('table', {'id': re.compile(r'tblGridData|tableContent|Grid', re.I)})
    if table is None:
        tables = soup.find_all('table')
        table = next((t for t in tables if len(t.find_all('th')) >= 3), None)
    if table is None:
        return {'revenue': {}, 'net_profit': {}}

    years_list = []
    header_row = table.find('tr')
    if header_row:
        for cell in header_row.find_all(['th', 'td'])[1:]:
            m = re.search(r'20\d{2}', cell.get_text(strip=True))
            if m:
                years_list.append(int(m.group()))
    if not years_list:
        return {'revenue': {}, 'net_profit': {}}

    REVENUE_KEYS = [
        'doanh thu thuần', 'doanh thu bán hàng', 'tổng doanh thu',
        'tổng thu nhập hoạt động', 'thu nhập lãi thuần',
    ]
    PROFIT_KEYS = [
        'lợi nhuận sau thuế thu nhập doanh nghiệp',
        'lợi nhuận sau thuế', 'lnst', 'lãi sau thuế',
    ]

    rev_vals, np_vals = {}, {}
    for row in table.find_all('tr')[1:]:
        cells = row.find_all(['td', 'th'])
        if not cells:
            continue
        label = cells[0].get_text(strip=True).lower()
        data_cells = cells[1:]
        is_rev = any(k in label for k in REVENUE_KEYS)
        is_np  = any(k in label for k in PROFIT_KEYS)
        if not is_rev and not is_np:
            continue
        for i, cell in enumerate(data_cells):
            if i >= len(years_list):
                break
            val = _parse_cafef_value(cell.get_text(strip=True))
            if val is None:
                continue
            y = years_list[i]
            if is_rev and y not in rev_vals:
                rev_vals[y] = val
            if is_np and y not in np_vals:
                np_vals[y] = val

    return {'revenue': rev_vals, 'net_profit': np_vals}


def fetch_cafef_yearly_full(ticker: str, years: list = None, debug: bool = False) -> dict:
    """
    Lấy đủ 4 chỉ tiêu (equity, total_assets, revenue, net_profit) từ CafeF.
    Trả về dict: mỗi key là pd.Series(index=năm, values=tỷ VNĐ).
    """
    empty_s = pd.Series(dtype=float)
    try:
        bs  = _scrape_cafef_balance(ticker, year=0)
        inc = _scrape_cafef_income(ticker, year=0)

        # CafeF default (year=0) trả về 5 năm gần nhất — trong 2026 đó là
        # 2022–2026. Nếu caller yêu cầu năm 2021 thì phải fetch thêm trang
        # cũ hơn bằng cách truyền year=2021 vào URL.
        if years and any(y <= 2021 for y in years):
            bs_old  = _scrape_cafef_balance(ticker, year=2021)
            inc_old = _scrape_cafef_income(ticker, year=2021)
            for k in ('equity', 'total_assets'):
                for yr, val in bs_old.get(k, {}).items():
                    bs.setdefault(k, {})[yr] = bs[k].get(yr) or val
            for k in ('revenue', 'net_profit'):
                for yr, val in inc_old.get(k, {}).items():
                    inc.setdefault(k, {})[yr] = inc[k].get(yr) or val

        result = {
            'equity':       pd.Series(bs.get('equity', {}),       dtype=float).sort_index(),
            'total_assets': pd.Series(bs.get('total_assets', {}), dtype=float).sort_index(),
            'revenue':      pd.Series(inc.get('revenue', {}),     dtype=float).sort_index(),
            'net_profit':   pd.Series(inc.get('net_profit', {}),  dtype=float).sort_index(),
        }
        if years:
            for k in result:
                s = result[k]
                result[k] = s[s.index.isin(years)] if not s.empty else s
        return result
    except Exception:
        return {'equity': empty_s, 'total_assets': empty_s,
                'revenue': empty_s, 'net_profit': empty_s}
