"""
valuation.py
------------
Module định giá cổ phiếu: DuPont, DCF (FCFF), Reverse DCF, Graham Number,
DDM Gordon, tổng hợp 9 phương pháp định giá, và ước tính WACC theo ngành.
"""

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════════
# PHẦN 1 — WACC THEO NGÀNH
# ══════════════════════════════════════════════════════════════════════

TICKER_SECTOR_MAP = {
    'VCB': 'bank', 'BID': 'bank', 'CTG': 'bank', 'TCB': 'bank', 'MBB': 'bank',
    'ACB': 'bank', 'STB': 'bank', 'VPB': 'bank', 'HDB': 'bank', 'SHB': 'bank',
    'EIB': 'bank', 'LPB': 'bank', 'OCB': 'bank', 'TPB': 'bank', 'VIB': 'bank',
    'MSB': 'bank', 'SSB': 'bank', 'NAB': 'bank', 'ABB': 'bank', 'BVB': 'bank',
    'HPG': 'steel', 'HSG': 'steel', 'NKG': 'steel', 'TVN': 'steel', 'SMC': 'steel',
    'VIC': 'real_estate', 'VHM': 'real_estate', 'NLG': 'real_estate',
    'KDH': 'real_estate', 'DXG': 'real_estate', 'PDR': 'real_estate',
    'NVL': 'real_estate', 'VRE': 'real_estate', 'HDG': 'real_estate',
    'DIG': 'real_estate', 'CEO': 'real_estate',
    'VNM': 'retail', 'MWG': 'retail', 'PNJ': 'retail', 'MSN': 'retail',
    'FRT': 'retail', 'DGW': 'retail', 'SAB': 'retail', 'VHC': 'retail',
    'FPT': 'tech', 'CMG': 'tech', 'ELC': 'tech', 'CTR': 'tech',
    'GAS': 'oil_gas', 'PLX': 'oil_gas', 'PVD': 'oil_gas', 'PVS': 'oil_gas',
    'BSR': 'oil_gas', 'DCM': 'oil_gas', 'DPM': 'oil_gas', 'PVT': 'oil_gas',
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
        (['bán lẻ', 'retail', 'thực phẩm', 'tiêu dùng'], 'retail'),
        (['công nghệ', 'technology', 'viễn thông', 'phần mềm'], 'tech'),
        (['dầu khí', 'oil', 'gas', 'hoá chất', 'xăng dầu'], 'oil_gas'),
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
    wacc_adjusted = max(wacc_low - 0.01, min(wacc_high + 0.01, wacc_adjusted))
    return round(wacc_adjusted, 4)


def wacc_scenarios(base_wacc: float) -> dict:
    return {
        'Bi quan':  {'wacc': round(base_wacc + 0.005, 4), 'g': 0.02},
        'Cơ sở':   {'wacc': round(base_wacc, 4),          'g': 0.03},
        'Tích cực': {'wacc': round(base_wacc - 0.005, 4), 'g': 0.035},
    }


# ══════════════════════════════════════════════════════════════════════
# PHẦN 2 — CÁC HÀM ĐỊNH GIÁ
# ══════════════════════════════════════════════════════════════════════

def dupont_decomposition(revenue_series, net_profit_series,
                          total_assets_series, equity_series):
    years = sorted(
        set(revenue_series.index) | set(net_profit_series.index) |
        set(total_assets_series.index) | set(equity_series.index)
    )
    rows = []
    for y in years:
        rev    = revenue_series.get(y, np.nan)
        npft   = net_profit_series.get(y, np.nan)
        assets = total_assets_series.get(y, np.nan)
        equity = equity_series.get(y, np.nan)

        net_margin       = (npft / rev * 100)   if rev    not in (0, None) and pd.notna(rev)    and pd.notna(npft)   else None
        asset_turnover   = (rev / assets)        if assets not in (0, None) and pd.notna(assets) and pd.notna(rev)    else None
        equity_multiplier= (assets / equity)     if equity not in (0, None) and pd.notna(equity) and pd.notna(assets) else None

        roe = None
        if net_margin is not None and asset_turnover is not None and equity_multiplier is not None:
            roe = net_margin * asset_turnover * equity_multiplier

        rows.append({
            'Năm': y,
            'Net margin (%)':        round(net_margin, 2)        if net_margin        is not None else None,
            'Asset turnover (x)':    round(asset_turnover, 2)    if asset_turnover    is not None else None,
            'Equity multiplier (x)': round(equity_multiplier, 2) if equity_multiplier is not None else None,
            'ROE (%)':               round(roe, 2)               if roe               is not None else None,
        })
    return pd.DataFrame(rows)


def dcf_fcff_scenarios(latest_fcff, shares_outstanding, net_debt=0.0,
                        base_wacc=0.105, years=5, ticker=None):
    """
    DCF FCFF 3 kịch bản. `ticker` được nhận nhưng không dùng — giữ để
    tương thích với pipeline.py đang truyền ticker=ticker.
    """
    if latest_fcff is None or shares_outstanding in (0, None):
        return None

    # Nếu có ticker, dùng WACC theo ngành thay vì base_wacc cứng
    if ticker:
        base_wacc = estimate_wacc(ticker)

    scenarios = wacc_scenarios(base_wacc)
    result = {}

    for name, params in scenarios.items():
        wacc = params['wacc']
        g    = params['g']
        g_terminal = g / 2

        if wacc <= g_terminal:
            continue

        pv_stage1 = 0.0
        fcff_t = latest_fcff
        for t in range(1, years + 1):
            fcff_t = fcff_t * (1 + g)
            pv_stage1 += fcff_t / ((1 + wacc) ** t)

        terminal_fcff  = fcff_t * (1 + g_terminal)
        terminal_value = terminal_fcff / (wacc - g_terminal)
        pv_terminal    = terminal_value / ((1 + wacc) ** years)

        enterprise_value = pv_stage1 + pv_terminal
        equity_value     = enterprise_value - net_debt
        value_per_share  = equity_value / shares_outstanding if shares_outstanding > 0 else None

        result[name] = {
            "value_per_share":   round(value_per_share, 0) if value_per_share is not None else None,
            "enterprise_value":  round(enterprise_value, 2),
            "equity_value":      round(equity_value, 2),
            "wacc": wacc,
            "g":    g,
        }

    return result if result else None


def reverse_dcf_implied_growth(current_price, shares_outstanding, latest_fcff,
                                wacc=0.105, net_debt=0.0):
    if not latest_fcff or latest_fcff <= 0 or not shares_outstanding or shares_outstanding <= 0:
        return None
    if current_price is None or current_price <= 0:
        return None

    market_equity_value       = current_price * shares_outstanding
    enterprise_value_implied  = market_equity_value + net_debt
    denominator               = enterprise_value_implied + latest_fcff
    if denominator == 0:
        return None

    g_implied = (wacc * enterprise_value_implied - latest_fcff) / denominator
    if g_implied >= wacc:
        return None
    return round(g_implied, 4)


def graham_number(eps, bvps):
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return round(np.sqrt(22.5 * eps * bvps), 0)


def ddm_gordon(dps_series, net_profit_series=None, ticker=None):
    """
    DDM Gordon Growth — nhận series cổ tức và tự tính g từ tăng trưởng
    lợi nhuận. Tương thích với cách gọi trong pipeline.py:
        ddm_gordon(dps_series, net_profit_series, ticker=ticker)

    Trả về tuple (giá_trị, ghi_chú).
    """
    # Lấy DPS mới nhất
    if dps_series is None or (hasattr(dps_series, 'empty') and dps_series.empty):
        return None, "Không có dữ liệu cổ tức"

    if isinstance(dps_series, pd.Series):
        dps_valid = dps_series.dropna()
        dps_valid = dps_valid[dps_valid > 0]
        if dps_valid.empty:
            return None, "Cổ tức = 0 hoặc không có dữ liệu"
        dps_latest = float(dps_valid.iloc[-1])
    else:
        dps_latest = float(dps_series)
        if dps_latest <= 0:
            return None, "Cổ tức = 0"

    # Ước tính g từ tăng trưởng lợi nhuận (nếu có)
    g = 0.03  # mặc định
    if net_profit_series is not None and isinstance(net_profit_series, pd.Series):
        np_valid = net_profit_series.dropna()
        np_valid = np_valid[np_valid > 0].sort_index()
        if len(np_valid) >= 2:
            first, last = float(np_valid.iloc[0]), float(np_valid.iloc[-1])
            n = len(np_valid) - 1
            if first > 0 and last > 0 and n > 0:
                cagr_np = (last / first) ** (1 / n) - 1
                # Kẹp g trong [0%, 8%] — tránh g phi lý
                g = max(0.0, min(0.08, cagr_np))

    # WACC theo ngành làm required return
    wacc = estimate_wacc(ticker) if ticker else 0.105
    required_return = wacc

    if required_return <= g:
        # Nếu g >= r, ép g = r/2 để tránh chia âm
        g = required_return / 2

    d1    = dps_latest * (1 + g)
    value = round(d1 / (required_return - g), 0)
    note  = f"DPS={dps_latest:,.0f}đ, g={g*100:.1f}%, r={required_return*100:.1f}%"
    return value, note


def nine_methods_valuation(eps_latest, bvps_latest, pe_series, pb_series,
                            current_price, dcf_results=None,
                            graham_value=None, ddm_value=None,
                            eps_adj=None, bvps_adj=None,
                            shares_series=None, net_profit_series=None,
                            dps_series=None, ticker=None):
    """
    Tổng hợp các phương pháp định giá. Nhận đầy đủ kwargs mà pipeline.py
    truyền vào — các arg mới (eps_adj, bvps_adj, shares_series,
    net_profit_series, dps_series, ticker) được nhận nhưng dùng tuỳ ngữ cảnh.
    """
    methods = {}

    # Dùng eps_adj/bvps_adj nếu có (đã điều chỉnh split), fallback về latest
    eps_use  = eps_adj.iloc[-1]  if (eps_adj  is not None and isinstance(eps_adj,  pd.Series) and not eps_adj.empty)  else eps_latest
    bvps_use = bvps_adj.iloc[-1] if (bvps_adj is not None and isinstance(bvps_adj, pd.Series) and not bvps_adj.empty) else bvps_latest

    # 1-2. P/E lịch sử
    if pe_series is not None and not pe_series.empty and eps_use and eps_use > 0:
        pe_valid = pe_series[pe_series > 0].tail(3)
        if not pe_valid.empty:
            methods["P/E trung bình (3 năm)"] = round(pe_valid.mean() * eps_use, 0)
            methods["P/E thấp nhất (3 năm)"]  = round(pe_valid.min()  * eps_use, 0)

    # 3-4. P/B lịch sử
    if pb_series is not None and not pb_series.empty and bvps_use and bvps_use > 0:
        pb_valid = pb_series[pb_series > 0].tail(3)
        if not pb_valid.empty:
            methods["P/B trung bình (3 năm)"] = round(pb_valid.mean() * bvps_use, 0)
            methods["P/B thấp nhất (3 năm)"]  = round(pb_valid.min()  * bvps_use, 0)

    # 5. Graham Number
    if graham_value:
        methods["Graham Number"] = graham_value

    # 6. DDM Gordon
    if ddm_value is not None:
        # ddm_gordon() trả về tuple (value, note) — unpack nếu cần
        _ddm_num = ddm_value[0] if isinstance(ddm_value, tuple) else ddm_value
        if _ddm_num is not None and _ddm_num > 0:
            methods["DDM Gordon"] = _ddm_num

    # 7-9. DCF FCFF
    if dcf_results:
        for scenario_name, data in dcf_results.items():
            vps = data.get("value_per_share")
            if vps:
                methods[f"DCF FCFF - {scenario_name}"] = vps

    return methods if methods else None


def summarize_valuation(valuation_methods, current_price):
    if not valuation_methods:
        return None
    values = []
    for v in valuation_methods.values():
        if isinstance(v, tuple):
            v = v[0]  # unpack (value, note)
        try:
            fv = float(v)
            if fv > 0:
                values.append(fv)
        except (TypeError, ValueError):
            pass
    if not values:
        return None

    avg_value    = float(np.mean(values))
    median_value = float(np.median(values))
    min_value    = float(np.min(values))
    max_value    = float(np.max(values))

    upside_pct     = None
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
        "target_price_avg":    round(avg_value, 0),
        "target_price_median": round(median_value, 0),
        "target_price_min":    round(min_value, 0),
        "target_price_max":    round(max_value, 0),
        "upside_pct":          round(upside_pct, 2) if upside_pct is not None else None,
        "recommendation":      recommendation,
        "num_methods":         len(values),
    }


def detect_stock_dividend_years(outstanding_shares_series):
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
            if change > 0.10:
                dividend_years.append(cur_y)
    return dividend_years


def normalize_eps_bvps_series(eps_series, bvps_series, outstanding_shares_series):
    eps_adj  = eps_series.copy()  if eps_series  is not None else pd.Series(dtype=float)
    bvps_adj = bvps_series.copy() if bvps_series is not None else pd.Series(dtype=float)

    if outstanding_shares_series is None or outstanding_shares_series.empty:
        return eps_adj, bvps_adj

    s = outstanding_shares_series.sort_index().dropna()
    if len(s) < 2:
        return eps_adj, bvps_adj

    years = sorted(s.index)
    latest_shares = s[years[-1]]
    eps_adj  = eps_adj.astype(float)
    bvps_adj = bvps_adj.astype(float)

    for y in years[:-1]:
        if s[y] and s[y] > 0 and latest_shares > 0:
            mult = latest_shares / s[y]
            if abs(mult - 1.0) > 0.05:
                if y in eps_adj.index:
                    eps_adj[y]  = eps_adj[y]  / mult
                if y in bvps_adj.index:
                    bvps_adj[y] = bvps_adj[y] / mult

    return eps_adj, bvps_adj


# ══════════════════════════════════════════════════════════════════════
# PHẦN 3 — EXTENDED MULTIPLES (P/S, P/CF, EV/EBITDA)
# ══════════════════════════════════════════════════════════════════════

def compute_extended_multiples(clean_metrics: dict) -> dict:
    """
    Tính P/S, P/CF, EV/EBITDA từ clean_metrics trả về bởi pipeline.
    Dùng trong UI tab "Multiples Mở Rộng" để hiển thị nhất quán.

    Trả về dict:
        ps               float | None  — Price-to-Sales
        pcf              float | None  — Price-to-Cash Flow
        ev_ebitda        float | None  — EV/EBITDA
        pcf_estimated    bool          — CFO dùng proxy LNST+KH hay không
        ebitda_estimated bool          — EBITDA dùng proxy hay không
        excl_extended    bool          — True nếu là ngân hàng (không áp dụng EV/EBITDA & P/S)
    """
    excl = bool(clean_metrics.get("excl_extended_multiples", False))
    result = {
        "ps":               None,
        "pcf":              None,
        "ev_ebitda":        None,
        "pcf_estimated":    False,
        "ebitda_estimated": False,
        "excl_extended":    excl,
    }
    if excl:
        return result

    mktcap_b   = clean_metrics.get("market_cap_billion",  0.0) or 0.0   # tỷ VND
    rev_b      = clean_metrics.get("revenue_latest_billion", 0.0) or 0.0
    # None = pipeline không tìm được dữ liệu → hiển thị "Thiếu dữ liệu"
    # 0.0  = tìm được nhưng = 0 → cũng không tính (chia 0)
    cfo_b      = clean_metrics.get("cfo_latest_billion",   None)
    ebitda_b   = clean_metrics.get("ebitda_latest_billion", None)
    net_debt_b = clean_metrics.get("net_debt_billion",      0.0) or 0.0

    # P/S = Vốn hóa / Doanh thu thuần
    if rev_b and rev_b > 0 and mktcap_b > 0:
        result["ps"] = round(mktcap_b / rev_b, 2)

    # P/CF = Vốn hóa / Dòng tiền HĐKD
    # Guard: cfo_b phải là số thực dương (None hoặc âm → không tính)
    if cfo_b is not None and cfo_b > 0 and mktcap_b > 0:
        result["pcf"] = round(mktcap_b / cfo_b, 2)
        result["pcf_estimated"] = bool(clean_metrics.get("cfo_is_estimated", False))

    # EV/EBITDA: EV = Vốn hóa + Nợ ròng
    # Guard: ebitda_b phải là số thực dương; EV âm (tiền > nợ + vốn hóa) → không tính
    if ebitda_b is not None and ebitda_b > 0 and mktcap_b > 0:
        ev = mktcap_b + net_debt_b
        if ev > 0:  # EV âm vô nghĩa kinh tế (thường xảy ra với CTCK có tiền mặt lớn)
            result["ev_ebitda"] = round(ev / ebitda_b, 2)
            result["ebitda_estimated"] = bool(clean_metrics.get("ebitda_is_estimated", False))

    return result
