"""
valuation.py
------------
Tổng hợp tất cả các hàm định giá mà pipeline.py cần import:
  - dupont_decomposition
  - dcf_fcff_scenarios
  - reverse_dcf_implied_growth
  - graham_number
  - ddm_gordon
  - nine_methods_valuation
  - summarize_valuation

Đồng thời giữ nguyên toàn bộ logic WACC theo ngành (sector_wacc) đã có.
"""

import numpy as np
import pandas as pd

# ============================================================
# WACC THEO NGÀNH (giữ nguyên từ bản cũ)
# ============================================================

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

TICKER_BETA_MAP = {
    'VCB': 0.85, 'VNM': 0.65, 'HPG': 1.30, 'FPT': 0.95,
    'MWG': 1.20, 'VIC': 1.40, 'VHM': 1.40, 'GAS': 0.80,
}

SECTOR_WACC_TABLE = {
    'bank':        {'wacc_low': 0.09, 'wacc_high': 0.11, 'beta_mid': 1.0},
    'steel':       {'wacc_low': 0.10, 'wacc_high': 0.12, 'beta_mid': 1.3},
    'real_estate': {'wacc_low': 0.11, 'wacc_high': 0.13, 'beta_mid': 1.4},
    'retail':      {'wacc_low': 0.08, 'wacc_high': 0.10, 'beta_mid': 0.8},
    'tech':        {'wacc_low': 0.08, 'wacc_high': 0.10, 'beta_mid': 0.9},
    'oil_gas':     {'wacc_low': 0.09, 'wacc_high': 0.11, 'beta_mid': 1.2},
    'aviation':    {'wacc_low': 0.10, 'wacc_high': 0.12, 'beta_mid': 1.5},
    'default':     {'wacc_low': 0.10, 'wacc_high': 0.11, 'beta_mid': 1.0},
}


def detect_sector(ticker: str, industry_text: str = "") -> str:
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
    return {
        'Bi quan':  {'wacc': round(base_wacc + 0.005, 4), 'g': 0.02},
        'Cơ sở':   {'wacc': round(base_wacc, 4),          'g': 0.03},
        'Tích cực': {'wacc': round(base_wacc - 0.005, 4), 'g': 0.035},
    }


# ============================================================
# 1. DUPONT DECOMPOSITION
# ============================================================

def dupont_decomposition(
    revenue_series: pd.Series,
    net_profit_series: pd.Series,
    total_assets_series: pd.Series,
    equity_series: pd.Series,
) -> pd.DataFrame:
    """
    ROE = Net Margin × Asset Turnover × Leverage
    Trả về DataFrame với index = năm, cột = [net_margin, asset_turnover, leverage, roe_dupont].
    """
    common = (
        revenue_series.index
        .intersection(net_profit_series.index)
        .intersection(total_assets_series.index)
        .intersection(equity_series.index)
    )
    if len(common) == 0:
        return pd.DataFrame()

    rows = []
    for yr in sorted(common):
        rev   = float(revenue_series[yr])
        np_   = float(net_profit_series[yr])
        ta    = float(total_assets_series[yr])
        eq    = float(equity_series[yr])

        if rev == 0 or ta == 0 or eq == 0:
            continue

        net_margin    = np_ / rev          # biên lợi nhuận
        asset_turn    = rev / ta           # vòng quay tài sản
        leverage      = ta / eq            # đòn bẩy tài chính
        roe_dupont    = net_margin * asset_turn * leverage

        rows.append({
            'year':          yr,
            'net_margin':    net_margin,
            'asset_turnover': asset_turn,
            'leverage':      leverage,
            'roe_dupont':    roe_dupont,
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index('year')
    return df


# ============================================================
# 2. DCF FCFF 3 KỊCH BẢN
# ============================================================

def dcf_fcff_scenarios(
    latest_fcff: float,
    shares_outstanding: float,
    net_debt: float = 0.0,
    ticker: str = "",
    industry_text: str = "",
) -> dict:
    """
    Tính DCF FCFF cho 3 kịch bản (Bi quan / Cơ sở / Tích cực).
    WACC lấy từ estimate_wacc() theo ngành thay vì cố định 10.5%.

    latest_fcff      : FCFF năm gần nhất (đơn vị VNĐ tuyệt đối, không phải tỷ)
    shares_outstanding: tổng số CP lưu hành (đơn vị CP)
    net_debt         : nợ ròng (VNĐ tuyệt đối)
    """
    if not latest_fcff or latest_fcff <= 0 or not shares_outstanding or shares_outstanding <= 0:
        return {}

    base_wacc = estimate_wacc(ticker, industry_text)
    scenarios_wacc = wacc_scenarios(base_wacc)

    results = {}
    n_years = 5   # dự phóng 5 năm rõ ràng rồi terminal value

    for name, params in scenarios_wacc.items():
        wacc = params['wacc']
        g    = params['g']

        if wacc <= g:
            continue

        # PV của các dòng tiền tường minh (năm 1–5)
        pv_explicit = 0.0
        fcff_t = latest_fcff
        for t in range(1, n_years + 1):
            # Tăng trưởng tuyến tính từ g_high (năm 1) xuống g_terminal (năm 5)
            g_t = g + (0.06 - g) * (n_years - t) / max(n_years - 1, 1)
            fcff_t = fcff_t * (1 + g_t)
            pv_explicit += fcff_t / ((1 + wacc) ** t)

        # Terminal value (Gordon Growth ở năm n+1)
        terminal_fcff = fcff_t * (1 + g)
        terminal_value = terminal_fcff / (wacc - g)
        pv_terminal = terminal_value / ((1 + wacc) ** n_years)

        enterprise_value = pv_explicit + pv_terminal
        equity_value = enterprise_value - net_debt
        value_per_share = equity_value / shares_outstanding if equity_value > 0 else 0.0

        results[name] = {
            'value_per_share': round(value_per_share, 0),
            'wacc':  wacc,
            'g':     g,
            'pv_explicit':  round(pv_explicit, 0),
            'pv_terminal':  round(pv_terminal, 0),
        }

    return results


# ============================================================
# 3. REVERSE DCF — TỐC ĐỘ TĂNG TRƯỞNG ẨN
# ============================================================

def reverse_dcf_implied_growth(
    current_price: float,
    shares_outstanding: float,
    latest_fcff: float,
    wacc: float = 0.105,
    net_debt: float = 0.0,
    n_years: int = 5,
) -> float | None:
    """
    Giải ngược: thị trường đang kỳ vọng FCFF tăng trưởng bao nhiêu %/năm?
    Trả về tỉ lệ g dưới dạng float (0.12 = 12%). None nếu không giải được.
    """
    if not all([current_price > 0, shares_outstanding > 0, latest_fcff > 0]):
        return None

    target_equity = current_price * shares_outstanding
    target_ev = target_equity + net_debt

    # Binary search g trong [-0.20, +0.50]
    lo, hi = -0.20, 0.50
    for _ in range(60):
        mid = (lo + hi) / 2
        g = mid
        if wacc <= g:
            hi = mid
            continue

        pv = 0.0
        fcff_t = latest_fcff
        for t in range(1, n_years + 1):
            g_t = g + (0.06 - g) * (n_years - t) / max(n_years - 1, 1)
            fcff_t = fcff_t * (1 + g_t)
            pv += fcff_t / ((1 + wacc) ** t)

        tv = fcff_t * (1 + g) / (wacc - g)
        pv_tv = tv / ((1 + wacc) ** n_years)
        ev_calc = pv + pv_tv

        if ev_calc < target_ev:
            lo = mid
        else:
            hi = mid

    return round((lo + hi) / 2, 4)


# ============================================================
# 4. GRAHAM NUMBER & DDM GORDON
# ============================================================

def graham_number(eps: float, bvps: float) -> float | None:
    """
    Graham Number = sqrt(22.5 × EPS × BVPS).
    Trả None nếu EPS hoặc BVPS không dương.
    """
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return round((22.5 * eps * bvps) ** 0.5, 0)


def ddm_gordon(dps: float, required_return: float = 0.11, g: float = 0.04) -> float | None:
    """
    Gordon Growth Model: P = DPS × (1+g) / (ke - g).
    Trả None nếu DPS <= 0 hoặc ke <= g.
    """
    if dps is None or dps <= 0 or required_return <= g:
        return None
    return round((dps * (1 + g)) / (required_return - g), 0)


# ============================================================
# 5. NINE METHODS VALUATION (9 phương pháp PE/PB/BV hội tụ)
# ============================================================

def nine_methods_valuation(
    eps_latest: float,
    bvps_latest: float,
    pe_series: pd.Series,
    pb_series: pd.Series,
    current_price: float,
    dcf_results: dict = None,
    graham_value: float = None,
    ddm_value: float = None,
) -> dict:
    """
    Tổng hợp ≤9 phương pháp định giá.
    Trả về dict {tên_phương_pháp: giá_trị_ước_tính}.
    Chỉ tính phương pháp nào có đủ dữ liệu.
    """
    methods = {}

    # --- Helper lấy median / mean / min của series ---
    def _safe_stat(s: pd.Series, stat='median'):
        if s is None or s.empty:
            return None
        clean = s.dropna()
        if clean.empty:
            return None
        if stat == 'median':
            return float(clean.median())
        if stat == 'mean':
            return float(clean.mean())
        if stat == 'min':
            return float(clean.min())
        return None

    pe_median = _safe_stat(pe_series, 'median')
    pe_mean   = _safe_stat(pe_series, 'mean')
    pe_min    = _safe_stat(pe_series, 'min')
    pb_median = _safe_stat(pb_series, 'median')
    pb_mean   = _safe_stat(pb_series, 'mean')

    # 1. PE Median 5N × EPS
    if eps_latest and eps_latest > 0 and pe_median and pe_median > 0:
        methods['PE Median 5N'] = round(pe_median * eps_latest, 0)

    # 2. PE Trung bình 5N × EPS
    if eps_latest and eps_latest > 0 and pe_mean and pe_mean > 0:
        methods['PE Trung bình 5N'] = round(pe_mean * eps_latest, 0)

    # 3. PE Sàn (thấp nhất) 5N × EPS — giá trị an toàn
    if eps_latest and eps_latest > 0 and pe_min and pe_min > 0:
        methods['PE Sàn 5N'] = round(pe_min * eps_latest, 0)

    # 4. PB Median 5N × BVPS
    if bvps_latest and bvps_latest > 0 and pb_median and pb_median > 0:
        methods['PB Median 5N'] = round(pb_median * bvps_latest, 0)

    # 5. PB Trung bình 5N × BVPS
    if bvps_latest and bvps_latest > 0 and pb_mean and pb_mean > 0:
        methods['PB Trung bình 5N'] = round(pb_mean * bvps_latest, 0)

    # 6. Book Value (BVPS) — floor value
    if bvps_latest and bvps_latest > 0:
        methods['Book Value (BVPS)'] = round(bvps_latest, 0)

    # 7. DCF Cơ sở
    if dcf_results and 'Cơ sở' in dcf_results:
        v = dcf_results['Cơ sở'].get('value_per_share', 0)
        if v > 0:
            methods['DCF Cơ sở'] = round(v, 0)

    # 8. Graham Number
    if graham_value and graham_value > 0:
        methods['Graham Number'] = round(graham_value, 0)

    # 9. DDM Gordon
    if ddm_value and ddm_value > 0:
        methods['DDM Gordon'] = round(ddm_value, 0)

    return methods


# ============================================================
# 6. SUMMARIZE VALUATION — tổng hợp verdict + upside
# ============================================================

def summarize_valuation(methods: dict, current_price: float) -> dict | None:
    """
    Từ dict {tên: giá}, tính median, p25, p75 và verdict.
    Trả None nếu methods rỗng hoặc current_price <= 0.
    """
    if not methods or not current_price or current_price <= 0:
        return None

    # Lọc bỏ các key private (bắt đầu bằng _)
    values = [v for k, v in methods.items()
              if not k.startswith('_') and v is not None and v > 0]

    if not values:
        return None

    arr = sorted(values)
    median_val = float(np.median(arr))
    p25 = float(np.percentile(arr, 25))
    p75 = float(np.percentile(arr, 75))

    upside_median = (median_val / current_price - 1) * 100

    if upside_median > 20:
        verdict = 'UNDERVALUED'
    elif upside_median < -20:
        verdict = 'OVERVALUED'
    else:
        verdict = 'FAIRLY VALUED'

    return {
        'median':             round(median_val, 0),
        'p25':                round(p25, 0),
        'p75':                round(p75, 0),
        'upside_median_pct':  round(upside_median, 1),
        'verdict':            verdict,
        'n_methods':          len(values),
    }
