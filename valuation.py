"""
valuation.py
============
Tổng hợp TẤT CẢ hàm định giá + DuPont cho Equity Research Terminal.

FIX QUAN TRỌNG — Cổ tức cổ phiếu (stock dividend) & pha loãng:
────────────────────────────────────────────────────────────────
Khi DN chia cổ tức cổ phiếu (VD: 30% = phát hành thêm 30% CP),
số lượng CP lưu hành tăng → EPS/BVPS lịch sử bị pha loãng so với
hiện tại → chuỗi EPS/BVPS qua các năm KHÔNG thể so trực tiếp.

Biểu hiện bug:
  • BVPS tăng giả tạo một số năm rồi giảm mạnh
  • PE median bị lệch vì giá/EPS dùng số CP khác nhau qua các năm
  • DDM cho giá thấp bất thường với mã chỉ chia cổ tức cổ phiếu (DPS ≈ 0)

Cách xử lý trong code này:
  1. detect_stock_dividend_years(): phát hiện năm nào có khả năng phát hành
     thêm CP (shares tăng đột biến > 5%) → gán flag để cảnh báo ở UI
  2. normalize_eps_bvps_series(): chia EPS/BVPS lịch sử theo tỷ lệ điều chỉnh
     (tương tự split-adjustment) để so sánh được qua các năm
  3. DDM: chỉ áp dụng khi DPS tiền mặt > 0 VÀ payout ratio > 10% TẤT CẢ
     3 năm gần nhất — nếu không, trả về None kèm lý do
  4. PE/PB median: loại bỏ outlier (năm PE < 3 hoặc > 60) trước khi tính median
"""

import math
import numpy as np
import pandas as pd
from typing import Optional


# ════════════════════════════════════════════════════════════════════════════
# 1. PHÁT HIỆN PHA LOÃNG TỪ CỔ TỨC CỔ PHIẾU
# ════════════════════════════════════════════════════════════════════════════

def detect_stock_dividend_years(shares_series: pd.Series, threshold: float = 0.05) -> list:
    if shares_series is None or len(shares_series) < 2:
        return []
    dilution_years = []
    s = shares_series.dropna().sort_index()
    for i in range(1, len(s)):
        prev, curr = float(s.iloc[i-1]), float(s.iloc[i])
        if prev > 0 and (curr - prev) / prev > threshold:
            dilution_years.append(int(s.index[i]))
    return dilution_years


def normalize_eps_bvps_series(
    eps_series: pd.Series,
    bvps_series: pd.Series,
    shares_series: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    if shares_series is None or shares_series.empty:
        return eps_series, bvps_series

    s = shares_series.dropna().sort_index()
    if len(s) < 2:
        return eps_series, bvps_series

    current_shares = float(s.iloc[-1])
    if current_shares <= 0:
        return eps_series, bvps_series

    dilution_years = detect_stock_dividend_years(s, threshold=0.05)
    if not dilution_years:
        return eps_series, bvps_series

    adj_eps  = eps_series.copy()  if eps_series  is not None else pd.Series(dtype=float)
    adj_bvps = bvps_series.copy() if bvps_series is not None else pd.Series(dtype=float)

    for year in s.index:
        hist_shares = float(s.loc[year])
        if hist_shares <= 0:
            continue
        ratio = hist_shares / current_shares
        int_year = int(year)
        if adj_eps is not None and int_year in adj_eps.index and ratio != 1:
            adj_eps[int_year] = round(float(adj_eps[int_year]) * ratio, 2)
        if adj_bvps is not None and int_year in adj_bvps.index and ratio != 1:
            adj_bvps[int_year] = round(float(adj_bvps[int_year]) * ratio, 2)

    return adj_eps, adj_bvps


# ════════════════════════════════════════════════════════════════════════════
# 2. DCF — FCFF 3 KỊCH BẢN
# ════════════════════════════════════════════════════════════════════════════

def dcf_fcff_scenarios(
    latest_fcff: float,
    shares_outstanding: float,
    net_debt: float = 0.0,
    ticker: str = "",
    industry_text: str = "",
) -> Optional[dict]:
    if not latest_fcff or latest_fcff <= 0 or not shares_outstanding or shares_outstanding <= 0:
        return None

    base_wacc = estimate_wacc(ticker, industry_text)
    scenarios  = wacc_scenarios(base_wacc)
    results    = {}

    for name, params in scenarios.items():
        wacc = params['wacc']
        g    = params['g']
        if wacc <= g:
            continue
        try:
            growth_map = {'Bi quan': 0.03, 'Cơ sở': 0.06, 'Tích cực': 0.10}
            fcff_g = growth_map.get(name, 0.06)

            pv_explicit = sum(
                latest_fcff * ((1 + fcff_g) ** t) / ((1 + wacc) ** t)
                for t in range(1, 6)
            )
            fcff_y5 = latest_fcff * ((1 + fcff_g) ** 5)
            terminal_value = fcff_y5 * (1 + g) / (wacc - g)
            pv_terminal    = terminal_value / ((1 + wacc) ** 5)
            enterprise_value = pv_explicit + pv_terminal
            equity_value     = enterprise_value - net_debt
            fair_price       = equity_value / shares_outstanding

            if fair_price > 0:
                results[name] = {
                    'value_per_share': round(fair_price, 0),
                    'wacc':            wacc,
                    'g':               g,
                    'fcff_growth':     fcff_g,
                }
        except Exception:
            continue

    return results if results else None


# ════════════════════════════════════════════════════════════════════════════
# 3. REVERSE DCF
# ════════════════════════════════════════════════════════════════════════════

def reverse_dcf_implied_growth(
    current_price: float,
    shares_outstanding: float,
    latest_fcff: float,
    wacc: float = 0.105,
    net_debt: float = 0.0,
    g_terminal: float = 0.03,
) -> Optional[float]:
    if not all([current_price > 0, shares_outstanding > 0, latest_fcff > 0]):
        return None

    target_eq = current_price * shares_outstanding + net_debt

    def calc_ev(g_fcff):
        try:
            pv = sum(latest_fcff * ((1 + g_fcff)**t) / ((1 + wacc)**t) for t in range(1, 6))
            fcff_y5 = latest_fcff * ((1 + g_fcff)**5)
            tv = fcff_y5 * (1 + g_terminal) / (wacc - g_terminal)
            pv += tv / ((1 + wacc)**5)
            return pv
        except Exception:
            return 0

    lo, hi = -0.10, 0.40
    for _ in range(60):
        mid = (lo + hi) / 2
        if calc_ev(mid) < target_eq:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


# ════════════════════════════════════════════════════════════════════════════
# 4. GRAHAM NUMBER
# ════════════════════════════════════════════════════════════════════════════

def graham_number(eps: float, bvps: float) -> Optional[float]:
    if eps and bvps and eps > 0 and bvps > 0:
        return round(math.sqrt(22.5 * eps * bvps), 0)
    return None


# ════════════════════════════════════════════════════════════════════════════
# 5. DDM (GORDON GROWTH MODEL)
# ════════════════════════════════════════════════════════════════════════════

def ddm_gordon(
    dps_series: pd.Series,
    net_profit_series: pd.Series,
    ticker: str = "",
    ke: Optional[float] = None,
    g: float = 0.04,
) -> tuple[Optional[float], Optional[str]]:
    if dps_series is None or dps_series.empty:
        return None, "Không có dữ liệu DPS tiền mặt"

    dps_latest = float(dps_series.dropna().iloc[-1]) if not dps_series.dropna().empty else 0.0
    if dps_latest <= 0:
        return None, "DN không chia cổ tức tiền mặt (DPS = 0) — DDM không áp dụng được. Dùng DCF/PE/PB thay thế."

    if net_profit_series is not None and not net_profit_series.empty:
        recent_np  = net_profit_series.dropna().iloc[-3:]
        recent_dps = dps_series.dropna()
        common = recent_np.index.intersection(recent_dps.index)
        if len(common) >= 2:
            payout_ok = all(
                (float(recent_dps.loc[y]) / float(recent_np.loc[y])) > 0.10
                for y in common
                if float(recent_np.loc[y]) > 0
            )
            if not payout_ok:
                return None, "Payout ratio < 10% — DDM không đáng tin cậy cho mã này"

    if ke is None:
        ke = estimate_wacc(ticker) + 0.01

    if ke <= g:
        return None, f"ke ({ke:.1%}) ≤ g ({g:.1%}) — DDM vô nghĩa"

    return round(dps_latest * (1 + g) / (ke - g), 0), None


# ════════════════════════════════════════════════════════════════════════════
# 6. PE / PB MEDIAN
# ════════════════════════════════════════════════════════════════════════════

def _clean_multiples(series: pd.Series, lo: float, hi: float) -> pd.Series:
    if series is None or series.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(series, errors='coerce').dropna()
    return s[(s >= lo) & (s <= hi)]


def pe_pb_valuation(
    eps_adj: pd.Series,
    bvps_adj: pd.Series,
    pe_series: pd.Series,
    pb_series: pd.Series,
    eps_latest: float,
    bvps_latest: float,
) -> dict:
    results = {}
    pe_clean = _clean_multiples(pe_series, lo=3.0, hi=60.0)
    pb_clean = _clean_multiples(pb_series, lo=0.3, hi=15.0)

    if not pe_clean.empty and eps_latest > 0:
        median_pe = float(pe_clean.median())
        results['PE Median 5N'] = round(eps_latest * median_pe, 0)
        results['_median_pe']   = round(median_pe, 2)

    if not pb_clean.empty and bvps_latest > 0:
        median_pb = float(pb_clean.median())
        results['PB Median 5N'] = round(bvps_latest * median_pb, 0)
        results['_median_pb']   = round(median_pb, 2)

    return results


# ════════════════════════════════════════════════════════════════════════════
# 7. PEG
# ════════════════════════════════════════════════════════════════════════════

def peg_valuation(eps_adj: pd.Series, pe_current: float) -> dict:
    results = {}
    if eps_adj is None or len(eps_adj.dropna()) < 2:
        return results

    s = eps_adj.dropna().sort_index()
    eps_start = float(s.iloc[0])
    eps_end   = float(s.iloc[-1])
    n_years   = len(s) - 1

    if eps_start <= 0 or eps_end <= 0 or n_years < 1:
        return results

    eps_growth_pct = ((eps_end / eps_start) ** (1 / n_years) - 1) * 100

    if eps_growth_pct <= 0:
        results['_peg_note'] = "EPS tăng trưởng âm — PEG không áp dụng"
        return results

    peg = pe_current / eps_growth_pct
    results['PEG Fair Value'] = round(eps_end * max(eps_growth_pct, 1), 0)
    results['_PEG_Ratio']     = round(peg, 2)
    if peg < 1.0:
        results['_peg_signal'] = f"PEG {peg:.2f} < 1.0 → Có thể định giá thấp"
    elif peg < 2.0:
        results['_peg_signal'] = f"PEG {peg:.2f} ≈ 1-2 → Định giá hợp lý"
    else:
        results['_peg_signal'] = f"PEG {peg:.2f} > 2.0 → Có thể định giá cao"

    return results
  # ════════════════════════════════════════════════════════════════════════════
# 8. TỔNG HỢP 9 PHƯƠNG PHÁP
# ════════════════════════════════════════════════════════════════════════════

def nine_methods_valuation(
    eps_latest: float,
    bvps_latest: float,
    pe_series: pd.Series,
    pb_series: pd.Series,
    current_price: float,
    dcf_results: Optional[dict] = None,
    graham_value: Optional[float] = None,
    ddm_value: Optional[float] = None,
    eps_adj: Optional[pd.Series] = None,
    bvps_adj: Optional[pd.Series] = None,
    shares_series: Optional[pd.Series] = None,
    net_profit_series: Optional[pd.Series] = None,
    dps_series: Optional[pd.Series] = None,
    ticker: str = "",
) -> dict:
    methods = {}

    _eps_use  = eps_adj  if eps_adj  is not None and not eps_adj.empty  else pe_series
    _bvps_use = bvps_adj if bvps_adj is not None and not bvps_adj.empty else pb_series

    pe_pb = pe_pb_valuation(
        _eps_use, _bvps_use, pe_series, pb_series, eps_latest, bvps_latest)
    for k, v in pe_pb.items():
        if not k.startswith('_') and isinstance(v, (int, float)) and v > 0:
            methods[k] = v

    pe_current = (current_price / eps_latest) if eps_latest > 0 else 0
    if _eps_use is not None and not _eps_use.empty and pe_current > 0:
        peg = peg_valuation(_eps_use if not _eps_use.empty else pe_series, pe_current)
        for k, v in peg.items():
            if not k.startswith('_') and isinstance(v, (int, float)) and v > 0:
                methods[k] = v

    if dcf_results:
        for scenario_name, res in dcf_results.items():
            price = res.get('value_per_share') if isinstance(res, dict) else res
            if price and isinstance(price, (int, float)) and price > 0:
                methods[f'DCF {scenario_name}'] = float(price)

    if graham_value and isinstance(graham_value, (int, float)) and graham_value > 0:
        methods['Graham Number'] = graham_value

    if ddm_value and isinstance(ddm_value, (int, float)) and ddm_value > 0:
        methods['DDM Gordon'] = ddm_value

    return methods


# ════════════════════════════════════════════════════════════════════════════
# 9. TÓM TẮT ĐỊNH GIÁ
# ════════════════════════════════════════════════════════════════════════════

def summarize_valuation(methods: dict, current_price: float) -> dict:
    if not methods or current_price <= 0:
        return {}

    prices = {k: v for k, v in methods.items()
              if not k.startswith('_') and isinstance(v, (int, float)) and v > 0}
    if not prices:
        return {}

    vals      = sorted(prices.values())
    avg_fair  = float(np.mean(vals))
    med_fair  = float(np.median(vals))
    p25       = float(np.percentile(vals, 25))
    p75       = float(np.percentile(vals, 75))
    upside_pct = (med_fair / current_price - 1) * 100

    if upside_pct > 15:
        verdict = "UNDERVALUED"
    elif upside_pct < -10:
        verdict = "OVERVALUED"
    else:
        verdict = "FAIRLY_VALUED"

    return {
        'median':            round(med_fair, 0),
        'verdict':           verdict,
        'upside_median_pct': round(upside_pct, 1),
        'p25':               round(p25, 0),
        'p75':               round(p75, 0),
        'avg_fair_price':    round(avg_fair, 0),
        'n_methods':         len(prices),
        'price_range_low':   round(vals[0], 0),
        'price_range_high':  round(vals[-1], 0),
        'methods_used':      list(prices.keys()),
    }


# ════════════════════════════════════════════════════════════════════════════
# 10. DUPONT DECOMPOSITION
# ════════════════════════════════════════════════════════════════════════════

def dupont_decomposition(
    revenue_series: pd.Series,
    net_profit_series: pd.Series,
    total_assets_series: pd.Series,
    equity_series: pd.Series,
) -> pd.DataFrame:
    common = (
        set(revenue_series.dropna().index)      &
        set(net_profit_series.dropna().index)   &
        set(total_assets_series.dropna().index) &
        set(equity_series.dropna().index)
    )
    if not common:
        return pd.DataFrame()

    rows = []
    for y in sorted(common):
        rev = float(revenue_series.get(y, 0))
        np_ = float(net_profit_series.get(y, 0))
        ta  = float(total_assets_series.get(y, 0))
        eq  = float(equity_series.get(y, 0))

        if ta <= 0 or eq <= 0:
            continue

        net_margin     = (np_ / rev * 100) if rev > 0 else None
        asset_turnover = (rev / ta)         if rev > 0 else None
        equity_mult    = ta / eq
        roe_dupont     = (np_ / eq * 100)

        rows.append({
            'Năm':               int(y),
            'net_margin':        round(net_margin / 100, 4)     if net_margin     is not None else None,
            'asset_turnover':    round(asset_turnover, 4)       if asset_turnover is not None else None,
            'leverage':          round(equity_mult, 4),
            'roe_dupont':        round(roe_dupont / 100, 4),
            'Biên LN ròng (%)':  round(net_margin, 2)     if net_margin     is not None else None,
            'Vòng quay TS (x)':  round(asset_turnover, 3) if asset_turnover is not None else None,
            'Đòn bẩy (x)':       round(equity_mult, 2),
            'ROE DuPont (%)':    round(roe_dupont, 2),
        })

    return pd.DataFrame(rows).set_index('Năm') if rows else pd.DataFrame()


# ════════════════════════════════════════════════════════════════════════════
# 11. SENSITIVITY TABLE (DCF)
# ════════════════════════════════════════════════════════════════════════════

def dcf_sensitivity_table(
    latest_fcff: float,
    shares_outstanding: float,
    net_debt: float = 0.0,
    wacc_range: list = None,
    g_range: list = None,
) -> pd.DataFrame:
    if wacc_range is None:
        wacc_range = [0.09, 0.095, 0.10, 0.105, 0.11, 0.115, 0.12]
    if g_range is None:
        g_range = [0.02, 0.025, 0.03, 0.035, 0.04]

    if not latest_fcff or not shares_outstanding:
        return pd.DataFrame()

    rows = {}
    for wacc in wacc_range:
        row = {}
        for g in g_range:
            if wacc <= g:
                row[f'g={g:.1%}'] = None
                continue
            try:
                pv = sum(latest_fcff * 1.05**t / (1 + wacc)**t for t in range(1, 6))
                tv = latest_fcff * 1.05**5 * (1 + g) / (wacc - g)
                ev = pv + tv / (1 + wacc)**5
                eq = ev - net_debt
                row[f'g={g:.1%}'] = round(eq / shares_outstanding, 0) if eq > 0 else None
            except Exception:
                row[f'g={g:.1%}'] = None
        rows[f'WACC={wacc:.1%}'] = row

    return pd.DataFrame(rows).T


# ════════════════════════════════════════════════════════════════════════════
# 12. WACC ENGINE
# ════════════════════════════════════════════════════════════════════════════

TICKER_SECTOR_MAP = {
    'VCB':'bank','BID':'bank','CTG':'bank','TCB':'bank','MBB':'bank',
    'ACB':'bank','STB':'bank','VPB':'bank','HDB':'bank','SHB':'bank',
    'EIB':'bank','LPB':'bank','OCB':'bank','TPB':'bank','VIB':'bank',
    'MSB':'bank','SSB':'bank','NAB':'bank','ABB':'bank','BVB':'bank',
    'HPG':'steel','HSG':'steel','NKG':'steel','TVN':'steel','SMC':'steel',
    'VIC':'real_estate','VHM':'real_estate','NLG':'real_estate',
    'KDH':'real_estate','DXG':'real_estate','PDR':'real_estate',
    'NVL':'real_estate','VRE':'real_estate','HDG':'real_estate',
    'MWG':'retail','PNJ':'retail','VNM':'retail','MSN':'retail',
    'FRT':'retail','DGW':'retail','SAB':'retail','VHC':'retail',
    'FPT':'tech','CMG':'tech','ELC':'tech','CTR':'tech',
    'GAS':'oil_gas','PLX':'oil_gas','PVD':'oil_gas','PVS':'oil_gas',
    'BSR':'oil_gas','DCM':'oil_gas','DPM':'oil_gas','PVT':'oil_gas',
    'HVN':'aviation','VJC':'aviation','ACV':'aviation','GMD':'aviation',
}
TICKER_BETA_MAP = {
    'VCB':0.85,'VNM':0.65,'HPG':1.30,'FPT':0.95,
    'MWG':1.20,'VIC':1.40,'VHM':1.40,'GAS':0.80,
}
SECTOR_WACC_TABLE = {
    'bank':        {'wacc_low':0.09,'wacc_high':0.11,'beta_mid':1.0},
    'steel':       {'wacc_low':0.10,'wacc_high':0.12,'beta_mid':1.3},
    'real_estate': {'wacc_low':0.11,'wacc_high':0.13,'beta_mid':1.4},
    'retail':      {'wacc_low':0.08,'wacc_high':0.10,'beta_mid':0.8},
    'tech':        {'wacc_low':0.08,'wacc_high':0.10,'beta_mid':0.9},
    'oil_gas':     {'wacc_low':0.09,'wacc_high':0.11,'beta_mid':1.2},
    'aviation':    {'wacc_low':0.10,'wacc_high':0.12,'beta_mid':1.5},
    'default':     {'wacc_low':0.10,'wacc_high':0.11,'beta_mid':1.0},
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
