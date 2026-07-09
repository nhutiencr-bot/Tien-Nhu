"""
valuation.py
------------
Module định giá cổ phiếu: DuPont, DCF (FCFF), Reverse DCF, Graham Number,
DDM Gordon, tổng hợp 9 phương pháp định giá, và ước tính WACC theo ngành.

LƯU Ý: Phần "sector_wacc" (TICKER_SECTOR_MAP, TICKER_BETA_MAP,
SECTOR_WACC_TABLE, detect_sector, estimate_wacc, wacc_scenarios) được GIỮ
NGUYÊN như cũ. Các hàm định giá (dupont_decomposition, dcf_fcff_scenarios,
reverse_dcf_implied_growth, graham_number, ddm_gordon,
nine_methods_valuation, summarize_valuation) được BỔ SUNG LẠI vì đã bị
thiếu trong file trước đó, gây lỗi:
    ImportError: cannot import name 'dupont_decomposition' from 'valuation'
"""

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════════
# PHẦN 1 — WACC THEO NGÀNH (giữ nguyên, không thay đổi)
# ══════════════════════════════════════════════════════════════════════

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
    'bank': {'wacc_low': 0.09, 'wacc_high': 0.11, 'beta_mid': 1.0},
    'steel': {'wacc_low': 0.10, 'wacc_high': 0.12, 'beta_mid': 1.3},
    'real_estate': {'wacc_low': 0.11, 'wacc_high': 0.13, 'beta_mid': 1.4},
    'retail': {'wacc_low': 0.08, 'wacc_high': 0.10, 'beta_mid': 0.8},
    'tech': {'wacc_low': 0.08, 'wacc_high': 0.10, 'beta_mid': 0.9},
    'oil_gas': {'wacc_low': 0.09, 'wacc_high': 0.11, 'beta_mid': 1.2},
    'aviation': {'wacc_low': 0.10, 'wacc_high': 0.12, 'beta_mid': 1.5},
    'default': {'wacc_low': 0.10, 'wacc_high': 0.11, 'beta_mid': 1.0},
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
        'Bi quan': {'wacc': round(base_wacc + 0.005, 4), 'g': 0.02},
        'Cơ sở': {'wacc': round(base_wacc, 4), 'g': 0.03},
        'Tích cực': {'wacc': round(base_wacc - 0.005, 4), 'g': 0.035},
    }


# ══════════════════════════════════════════════════════════════════════
# PHẦN 2 — CÁC HÀM ĐỊNH GIÁ (bổ sung lại — bị thiếu, gây ImportError)
# ══════════════════════════════════════════════════════════════════════

def dupont_decomposition(revenue_series, net_profit_series,
                          total_assets_series, equity_series):
    """
    Phân tách ROE theo mô hình DuPont 3 nhân tố:
        ROE = Biên LN ròng (Net margin) x Vòng quay TS (Asset turnover)
              x Đòn bẩy TC (Equity multiplier)

    Trả về DataFrame theo năm với các cột:
        Năm, Net margin (%), Asset turnover (x), Equity multiplier (x), ROE (%)
    """
    years = sorted(
        set(revenue_series.index) | set(net_profit_series.index) |
        set(total_assets_series.index) | set(equity_series.index)
    )
    rows = []
    for y in years:
        rev = revenue_series.get(y, np.nan)
        npft = net_profit_series.get(y, np.nan)
        assets = total_assets_series.get(y, np.nan)
        equity = equity_series.get(y, np.nan)

        net_margin = (npft / rev * 100) if rev not in (0, None) and pd.notna(rev) and pd.notna(npft) else None
        asset_turnover = (rev / assets) if assets not in (0, None) and pd.notna(assets) and pd.notna(rev) else None
        equity_multiplier = (assets / equity) if equity not in (0, None) and pd.notna(equity) and pd.notna(assets) else None

        roe = None
        if net_margin is not None and asset_turnover is not None and equity_multiplier is not None:
            # net_margin đã ở dạng %, asset_turnover và equity_multiplier là tỷ lệ
            # thuần (lần), nên nhân trực tiếp cho ra ROE (%) mà không cần chia lại 100.
            roe = net_margin * asset_turnover * equity_multiplier

        rows.append({
            'Năm': y,
            'Net margin (%)': round(net_margin, 2) if net_margin is not None else None,
            'Asset turnover (x)': round(asset_turnover, 2) if asset_turnover is not None else None,
            'Equity multiplier (x)': round(equity_multiplier, 2) if equity_multiplier is not None else None,
            'ROE (%)': round(roe, 2) if roe is not None else None,
        })

    return pd.DataFrame(rows)


def dcf_fcff_scenarios(latest_fcff, shares_outstanding, net_debt=0.0,
                        base_wacc=0.105, years=5):
    """
    Định giá DCF theo dòng tiền tự do doanh nghiệp (FCFF), 3 kịch bản
    (Bi quan / Cơ sở / Tích cực) dựa trên wacc_scenarios().

    Mô hình 2 giai đoạn:
      - Giai đoạn 1 (n năm đầu): FCFF tăng trưởng đều theo g của kịch bản.
      - Giai đoạn 2 (vĩnh viễn): Gordon growth với g_terminal = g / 2
        (thận trọng hơn, tránh g_terminal quá gần WACC).

    Trả về dict: {ten_kich_ban: {"value_per_share": ..., "enterprise_value": ...,
                                   "equity_value": ..., "wacc": ..., "g": ...}}
    """
    if latest_fcff is None or shares_outstanding in (0, None):
        return None

    scenarios = wacc_scenarios(base_wacc)
    result = {}

    for name, params in scenarios.items():
        wacc = params['wacc']
        g = params['g']
        g_terminal = g / 2

        if wacc <= g_terminal:
            continue

        # PV của dòng FCFF trong n năm đầu
        pv_stage1 = 0.0
        fcff_t = latest_fcff
        for t in range(1, years + 1):
            fcff_t = fcff_t * (1 + g)
            pv_stage1 += fcff_t / ((1 + wacc) ** t)

        # Giá trị cuối kỳ (terminal value) theo Gordon growth
        terminal_fcff = fcff_t * (1 + g_terminal)
        terminal_value = terminal_fcff / (wacc - g_terminal)
        pv_terminal = terminal_value / ((1 + wacc) ** years)

        enterprise_value = pv_stage1 + pv_terminal
        equity_value = enterprise_value - net_debt
        value_per_share = equity_value / shares_outstanding if shares_outstanding > 0 else None

        result[name] = {
            "value_per_share": round(value_per_share, 0) if value_per_share is not None else None,
            "enterprise_value": round(enterprise_value, 2),
            "equity_value": round(equity_value, 2),
            "wacc": wacc,
            "g": g,
        }

    return result if result else None


def reverse_dcf_implied_growth(current_price, shares_outstanding, latest_fcff,
                                wacc=0.105, net_debt=0.0):
    """
    Reverse DCF: từ giá thị trường hiện tại, suy ngược ra mức tăng trưởng
    (g) vĩnh viễn mà thị trường đang ngầm định (Gordon growth 1 giai đoạn):

        Equity value = FCFF * (1 + g) / (WACC - g) - net_debt
        market_equity_value = current_price * shares_outstanding

    Giải phương trình theo g:
        g = (WACC * V - FCFF) / (V + FCFF)
        với V = market_equity_value + net_debt (enterprise value ngầm định)

    Trả về g (dạng thập phân, ví dụ 0.08 = 8%), hoặc None nếu không giải được.
    """
    if not latest_fcff or latest_fcff <= 0 or not shares_outstanding or shares_outstanding <= 0:
        return None
    if current_price is None or current_price <= 0:
        return None

    market_equity_value = current_price * shares_outstanding
    enterprise_value_implied = market_equity_value + net_debt

    denominator = enterprise_value_implied + latest_fcff
    if denominator == 0:
        return None

    g_implied = (wacc * enterprise_value_implied - latest_fcff) / denominator

    # Chặn kết quả trong khoảng hợp lý để tránh outlier toán học
    if g_implied >= wacc:
        return None
    return round(g_implied, 4)


def graham_number(eps, bvps):
    """
    Graham Number (Benjamin Graham):
        Value = sqrt(22.5 * EPS * BVPS)

    22.5 = P/E tối đa (15) x P/B tối đa (1.5) theo tiêu chuẩn Graham.
    EPS, BVPS phải > 0, nếu không trả về None.
    """
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return round(np.sqrt(22.5 * eps * bvps), 0)


def ddm_gordon(dividend_per_share, required_return, growth_rate):
    """
    Dividend Discount Model - Gordon Growth (1 giai đoạn):
        Value = D1 / (r - g)

    dividend_per_share: cổ tức năm gần nhất (D0). D1 = D0 * (1 + g).
    required_return: tỷ suất sinh lời yêu cầu (r), ví dụ 0.12.
    growth_rate: tốc độ tăng trưởng cổ tức vĩnh viễn (g), ví dụ 0.03.

    Trả về None nếu r <= g hoặc thiếu dữ liệu (không chia âm/0).
    """
    if dividend_per_share is None or dividend_per_share <= 0:
        return None
    if required_return is None or growth_rate is None:
        return None
    if required_return <= growth_rate:
        return None

    d1 = dividend_per_share * (1 + growth_rate)
    value = d1 / (required_return - growth_rate)
    return round(value, 0)


def nine_methods_valuation(eps_latest, bvps_latest, pe_series, pb_series,
                            current_price, dcf_results=None,
                            graham_value=None, ddm_value=None):
    """
    Tổng hợp các phương pháp định giá thành 1 dict duy nhất, gồm:
      1. P/E trung bình 3 năm  x EPS
      2. P/E thấp nhất 3 năm   x EPS (thận trọng)
      3. P/B trung bình 3 năm  x BVPS
      4. P/B thấp nhất 3 năm   x BVPS (thận trọng)
      5. Graham Number
      6. DDM Gordon (nếu có)
      7-9. DCF FCFF: Bi quan / Cơ sở / Tích cực (nếu có)

    Trả về dict: {ten_phuong_phap: gia_tri_uoc_tinh}
    Bỏ qua các phương pháp không đủ dữ liệu.
    """
    methods = {}

    # 1-2. Định giá theo P/E lịch sử
    if pe_series is not None and not pe_series.empty and eps_latest and eps_latest > 0:
        pe_valid = pe_series[pe_series > 0].tail(3)
        if not pe_valid.empty:
            methods["P/E trung bình (3 năm)"] = round(pe_valid.mean() * eps_latest, 0)
            methods["P/E thấp nhất (3 năm)"] = round(pe_valid.min() * eps_latest, 0)

    # 3-4. Định giá theo P/B lịch sử
    if pb_series is not None and not pb_series.empty and bvps_latest and bvps_latest > 0:
        pb_valid = pb_series[pb_series > 0].tail(3)
        if not pb_valid.empty:
            methods["P/B trung bình (3 năm)"] = round(pb_valid.mean() * bvps_latest, 0)
            methods["P/B thấp nhất (3 năm)"] = round(pb_valid.min() * bvps_latest, 0)

    # 5. Graham Number
    if graham_value:
        methods["Graham Number"] = graham_value

    # 6. DDM Gordon
    if ddm_value:
        methods["DDM Gordon"] = ddm_value

    # 7-9. DCF FCFF (3 kịch bản)
    if dcf_results:
        for scenario_name, data in dcf_results.items():
            vps = data.get("value_per_share")
            if vps:
                methods[f"DCF FCFF - {scenario_name}"] = vps

    return methods if methods else None


def summarize_valuation(valuation_methods, current_price):
    """
    Tổng hợp kết quả từ nhiều phương pháp định giá thành 1 khuyến nghị:
      - Giá mục tiêu trung bình / trung vị
      - % Upside/Downside so với giá hiện tại
      - Khuyến nghị: MUA MẠNH / MUA / NẮM GIỮ / BÁN dựa trên upside

    Trả về dict tóm tắt, hoặc None nếu không có phương pháp nào hợp lệ.
    """
    if not valuation_methods:
        return None

    values = [v for v in valuation_methods.values() if v is not None and v > 0]
    if not values:
        return None

    avg_value = float(np.mean(values))
    median_value = float(np.median(values))
    min_value = float(np.min(values))
    max_value = float(np.max(values))

    upside_pct = None
    recommendation = "KHÔNG ĐỦ DỮ LIỆU"
    if current_price and current_price > 0:
        upside_pct = (median_value / current_price - 1) * 100
        if upside_pct >= 20:
            recommendation = "MUA MẠNH"
        elif upside_pct >= 8:
            recommendation = "MUA"
        elif upside_pct >= -8:
            recommendation = "NẮM GIỮ"
        else:
            recommendation = "BÁN"

    return {
        "target_price_avg": round(avg_value, 0),
        "target_price_median": round(median_value, 0),
        "target_price_min": round(min_value, 0),
        "target_price_max": round(max_value, 0),
        "upside_pct": round(upside_pct, 2) if upside_pct is not None else None,
        "recommendation": recommendation,
        "num_methods": len(values),
    }


def detect_stock_dividend_years(outstanding_shares_series):
    """
    Phát hiện năm có chia cổ tức bằng cổ phiếu / tăng vốn đột biến
    dựa trên series số CP lưu hành.
    Trả về list các năm có số CP tăng > 10% so với năm trước.
    """
    if outstanding_shares_series is None or outstanding_shares_series.empty:
        return []
    s = outstanding_shares_series.sort_index().dropna()
    if len(s) < 2:
        return []
    dividend_years = []
    years = sorted(s.index)
    for i in range(1, len(years)):
        prev_y, cur_y = years[i-1], years[i]
        prev_val, cur_val = s[prev_y], s[cur_y]
        if prev_val and prev_val > 0:
            change = (cur_val - prev_val) / prev_val
            if change > 0.10:   # tăng >10% → nhiều khả năng có chia CP
                dividend_years.append(cur_y)
    return dividend_years


def normalize_eps_bvps_series(eps_series, bvps_series, outstanding_shares_series):
    """
    Điều chỉnh EPS/BVPS lịch sử về cùng base số CP với năm hiện tại
    (Bẫy 5B — split-adjustment consistency).

    Với mỗi năm có chia cổ phiếu: chia EPS/BVPS cho hệ số tích luỹ
    để đưa về base post-split, nhất quán với giá đã split-adjusted
    mà vnstock Quote.history() trả về.

    Trả về (eps_adj, bvps_adj) — hai pd.Series đã điều chỉnh.
    Nếu không phát hiện split → trả về series gốc không đổi.
    """
    import pandas as pd
    import numpy as np

    eps_adj  = eps_series.copy()  if eps_series  is not None else pd.Series(dtype=float)
    bvps_adj = bvps_series.copy() if bvps_series is not None else pd.Series(dtype=float)

    if outstanding_shares_series is None or outstanding_shares_series.empty:
        return eps_adj, bvps_adj

    s = outstanding_shares_series.sort_index().dropna()
    if len(s) < 2:
        return eps_adj, bvps_adj

    # Tính hệ số dồn tích từ năm sớm nhất đến năm muộn nhất
    years = sorted(s.index)
    # cumulative_mult[y] = số CP năm cuối / số CP năm y
    latest_shares = s[years[-1]]
    eps_adj  = eps_adj.astype(float)
    bvps_adj = bvps_adj.astype(float)

    for y in years[:-1]:
        if s[y] and s[y] > 0 and latest_shares > 0:
            mult = latest_shares / s[y]
            if abs(mult - 1.0) > 0.05:   # chỉ adjust khi lệch > 5%
                if y in eps_adj.index:
                    eps_adj[y] = eps_adj[y] / mult
                if y in bvps_adj.index:
                    bvps_adj[y] = bvps_adj[y] / mult

    return eps_adj, bvps_adj
