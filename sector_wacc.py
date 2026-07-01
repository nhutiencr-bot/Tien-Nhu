"""
sector_wacc.py
--------------
Ước tính WACC theo NGÀNH + BETA cụ thể cho DCF, thay vì dùng 1 con số
10.5% cố định cho mọi mã (bug cũ: Ngân hàng 9-11%, Bán lẻ 8-10%, BĐS
11-13%, Hàng không 10-12%... đều bị gán chung 10.5%).

Nguồn tham chiếu: bảng WACC theo ngành VN 2025-2026 (rf ≈ 3-3.5%,
equity risk premium VN ≈ 7-8%, thuế TNDN 20%).
"""

# Ticker → nhóm ngành (mở rộng dần theo nhu cầu; is_bank trong pipeline.py
# vẫn được dùng riêng cho phần loại trừ P/S & EV/EBITDA, độc lập với bảng này)
TICKER_SECTOR_MAP = {
    # Ngân hàng
    'VCB': 'bank', 'BID': 'bank', 'CTG': 'bank', 'TCB': 'bank', 'MBB': 'bank',
    'ACB': 'bank', 'STB': 'bank', 'VPB': 'bank', 'HDB': 'bank', 'SHB': 'bank',
    'EIB': 'bank', 'LPB': 'bank', 'OCB': 'bank', 'TPB': 'bank', 'VIB': 'bank',
    'MSB': 'bank', 'SSB': 'bank', 'NAB': 'bank', 'ABB': 'bank', 'BVB': 'bank',

    # Thép / công nghiệp nặng
    'HPG': 'steel', 'HSG': 'steel', 'NKG': 'steel', 'TVN': 'steel', 'SMC': 'steel',

    # Bất động sản
    'VIC': 'real_estate', 'VHM': 'real_estate', 'NLG': 'real_estate',
    'KDH': 'real_estate', 'DXG': 'real_estate', 'PDR': 'real_estate',
    'NVL': 'real_estate', 'VRE': 'real_estate', 'HDG': 'real_estate',
    'DIG': 'real_estate', 'CEO': 'real_estate',

    # Bán lẻ / tiêu dùng
    'VNM': 'retail', 'MWG': 'retail', 'PNJ': 'retail', 'MSN': 'retail',
    'FRT': 'retail', 'DGW': 'retail', 'SAB': 'retail', 'VHC': 'retail',

    # Công nghệ / viễn thông
    'FPT': 'tech', 'CMG': 'tech', 'ELC': 'tech', 'CTR': 'tech',

    # Dầu khí / hoá chất
    'GAS': 'oil_gas', 'PLX': 'oil_gas', 'PVD': 'oil_gas', 'PVS': 'oil_gas',
    'BSR': 'oil_gas', 'DCM': 'oil_gas', 'DPM': 'oil_gas', 'PVT': 'oil_gas',

    # Hàng không / vận tải
    'HVN': 'aviation', 'VJC': 'aviation', 'ACV': 'aviation', 'GMD': 'aviation',
}

# Beta 5 năm ước tính cho các blue-chip cụ thể (ưu tiên hơn beta trung bình ngành)
TICKER_BETA_MAP = {
    'VCB': 0.85, 'VNM': 0.65, 'HPG': 1.30, 'FPT': 0.95,
    'MWG': 1.20, 'VIC': 1.40, 'VHM': 1.40, 'GAS': 0.80,
}

# (WACC thấp - WACC cao, beta điển hình thấp - cao, beta giữa dải) theo ngành
SECTOR_WACC_TABLE = {
    'bank':        {'wacc_low': 0.09,  'wacc_high': 0.11,  'beta_mid': 1.0},
    'steel':       {'wacc_low': 0.10,  'wacc_high': 0.12,  'beta_mid': 1.3},
    'real_estate': {'wacc_low': 0.11,  'wacc_high': 0.13,  'beta_mid': 1.4},
    'retail':      {'wacc_low': 0.08,  'wacc_high': 0.10,  'beta_mid': 0.8},
    'tech':        {'wacc_low': 0.08,  'wacc_high': 0.10,  'beta_mid': 0.9},
    'oil_gas':     {'wacc_low': 0.09,  'wacc_high': 0.11,  'beta_mid': 1.2},
    'aviation':    {'wacc_low': 0.10,  'wacc_high': 0.12,  'beta_mid': 1.5},
    'default':     {'wacc_low': 0.10,  'wacc_high': 0.11,  'beta_mid': 1.0},
}


def detect_sector(ticker: str, industry_text: str = "") -> str:
    """Nhận diện nhóm ngành từ mã CP (ưu tiên) hoặc chuỗi 'industry' của
    vnstock overview (dò từ khoá tiếng Việt, fallback khi mã không có
    trong TICKER_SECTOR_MAP)."""
    ticker = (ticker or "").upper().strip()
    if ticker in TICKER_SECTOR_MAP:
        return TICKER_SECTOR_MAP[ticker]

    text = (industry_text or "").lower()
    keyword_rules = [
        (['ngân hàng', 'bank'], 'bank'),
        (['thép', 'steel', 'khoáng sản', 'luyện kim'], 'steel'),
        (['bất động sản', 'real estate', 'xây dựng'], 'real_estate'),
        (['bán lẻ', 'retail', 'thực phẩm', 'tiêu dùng', 'hàng tiêu dùng'], 'retail'),
        (['công nghệ', 'technology', 'viễn thông', 'phần mềm'], 'tech'),
        (['dầu khí', 'oil', 'gas', 'hoá chất', 'hóa chất', 'xăng dầu'], 'oil_gas'),
        (['hàng không', 'aviation', 'vận tải', 'logistics', 'cảng'], 'aviation'),
    ]
    for keywords, sector in keyword_rules:
        if any(kw in text for kw in keywords):
            return sector
    return 'default'


def estimate_wacc(ticker: str, industry_text: str = "") -> float:
    """
    WACC ≈ trung điểm dải ngành, tinh chỉnh theo beta cụ thể của mã
    (nếu có trong TICKER_BETA_MAP), theo quy tắc rule-of-thumb:
        wacc_adjusted = wacc_mid + (beta - beta_mid_sector) * 0.02
    Kẹp trong khoảng [wacc_low - 1%, wacc_high + 1%] để tránh outlier.
    """
    ticker = (ticker or "").upper().strip()
    sector = detect_sector(ticker, industry_text)
    row = SECTOR_WACC_TABLE.get(sector, SECTOR_WACC_TABLE['default'])
    wacc_low, wacc_high, beta_mid = row['wacc_low'], row['wacc_high'], row['beta_mid']
    wacc_mid = (wacc_low + wacc_high) / 2

    beta = TICKER_BETA_MAP.get(ticker, beta_mid)
    wacc_adjusted = wacc_mid + (beta - beta_mid) * 0.02

    lower_bound, upper_bound = wacc_low - 0.01, wacc_high + 0.01
    wacc_adjusted = max(lower_bound, min(upper_bound, wacc_adjusted))
    return round(wacc_adjusted, 4)


def wacc_scenarios(base_wacc: float) -> dict:
    """3 kịch bản WACC quanh mức cơ sở theo ngành (Bi quan/Cơ sở/Tích cực),
    thay vì dùng 10%/10.5%/11% cố định cho mọi mã."""
    return {
        'Bi quan':  {'wacc': round(base_wacc + 0.005, 4), 'g': 0.02},
        'Cơ sở':    {'wacc': round(base_wacc, 4),          'g': 0.03},
        'Tích cực': {'wacc': round(base_wacc - 0.005, 4), 'g': 0.035},
    }
