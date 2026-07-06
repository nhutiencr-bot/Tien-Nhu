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

        eq_series = pd.Series(data.get('equity', {}), dtype=float).sort_index()
        ta_series = pd.Series(data.get('total_assets', {}), dtype=float).sort_index()

        # Sanity check: loại bỏ giá trị bất thường (< 0 hoặc > 50,000 tỷ cho từng chỉ số)
        eq_series = eq_series[(eq_series > 0) & (eq_series < 5e7)]
        ta_series = ta_series[(ta_series > 0) & (ta_series < 5e7)]

        return {'equity': eq_series, 'total_assets': ta_series}

    except Exception:
        return empty
