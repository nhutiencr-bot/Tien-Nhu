"""
valuation.py
------------
Các công thức phân tích cơ bản & định giá, port từ logic của bản demo gốc
(HTML/JS Vercel của tác giả) sang dạng TỰ ĐỘNG cho MỌI mã, không hardcode
số liệu riêng cho HPG. Toàn bộ hệ số tham chiếu (PE/PB median, TB 5N...)
được tự tính từ LỊCH SỬ CHÍNH MÃ ĐÓ, không cần biết "ngành" mã thuộc về.

⚠️ Đây là công cụ tham khảo/giáo dục, không phải lời khuyên đầu tư.
Số liệu phụ thuộc chất lượng & độ đầy đủ dữ liệu trả về từ vnstock.
"""

import pandas as pd
import numpy as np


# ============================================================
# 1. DUPONT DECOMPOSITION: ROE = Biên LN x Vòng quay TS x Đòn bẩy
# ============================================================

def dupont_decomposition(revenue: pd.Series, net_profit: pd.Series,
                          total_assets: pd.Series, equity: pd.Series):
    """
    Tính 3 thành phần DuPont theo từng năm (chỉ tính năm có đủ cả 4 chỉ
    tiêu). Trả về DataFrame index=năm, cột: net_margin, asset_turnover,
    leverage, roe_check (= 3 thành phần nhân lại, để đối chiếu).
    """
    years = sorted(
        set(revenue.index) & set(net_profit.index) &
        set(total_assets.index) & set(equity.index)
    )
    rows = []
    for y in years:
        rev, np_, ta, eq = revenue[y], net_profit[y], total_assets[y], equity[y]
        if rev == 0 or ta == 0 or eq == 0:
            continue
        net_margin = np_ / rev
        asset_turnover = rev / ta
        leverage = ta / eq
        roe_check = net_margin * asset_turnover * leverage
        rows.append({
            'year': y,
            'net_margin': net_margin,
            'asset_turnover': asset_turnover,
            'leverage': leverage,
            'roe_check': roe_check,
        })
    if not rows:
        return pd.DataFrame(columns=['year', 'net_margin', 'asset_turnover', 'leverage', 'roe_check']).set_index('year')
    return pd.DataFrame(rows).set_index('year')


# ============================================================
# 2. DCF (FCFF) - 3 KỊCH BẢN: Bi quan / Cơ sở / Tích cực
# ============================================================

def dcf_fcff_scenarios(latest_fcff, shares_outstanding, net_debt=0.0,
                        years=5):
    """
    DCF đơn giản hoá theo FCFF (Free Cash Flow to Firm), chiết khấu N năm
    rồi cộng giá trị cuối kỳ (terminal value, mô hình Gordon Growth).

    latest_fcff: FCFF năm gần nhất (đơn vị: VNĐ, không phải tỷ)
    shares_outstanding: số CP lưu hành
    net_debt: nợ vay thuần (= tổng nợ vay - tiền mặt); mặc định 0 nếu
              không có dữ liệu đáng tin cậy (an toàn hơn là bỏ qua đòn bẩy
              nợ thay vì đưa số sai).

    Trả về dict 3 kịch bản, mỗi kịch bản có: value_per_share, wacc, g, upside_label
    Giả định 3 kịch bản (không hardcode riêng cho 1 ngành):
        Bi quan : WACC 11.0%, g 2.0%
        Cơ sở   : WACC 10.5%, g 3.0%
        Tích cực: WACC 10.0%, g 3.5%
    """
    if latest_fcff is None or shares_outstanding is None or shares_outstanding <= 0:
        return None

    scenarios = {
        'Bi quan':  {'wacc': 0.11,  'g': 0.02},
        'Cơ sở':    {'wacc': 0.105, 'g': 0.03},
        'Tích cực': {'wacc': 0.10,  'g': 0.035},
    }

    results = {}
    for name, p in scenarios.items():
        wacc, g = p['wacc'], p['g']
        if wacc <= g:
            results[name] = None
            continue

        # Chiết khấu FCFF tăng trưởng đều g% trong `years` năm đầu
        pv_explicit = 0.0
        fcff_t = latest_fcff
        for t in range(1, years + 1):
            fcff_t = fcff_t * (1 + g)
            pv_explicit += fcff_t / ((1 + wacc) ** t)

        # Terminal value tại cuối năm `years` (Gordon Growth)
        terminal_fcff = fcff_t * (1 + g)
        terminal_value = terminal_fcff / (wacc - g)
        pv_terminal = terminal_value / ((1 + wacc) ** years)

        enterprise_value = pv_explicit + pv_terminal
        equity_value = enterprise_value - net_debt
        value_per_share = equity_value / shares_outstanding if shares_outstanding > 0 else 0

        results[name] = {
            'wacc': wacc,
            'g': g,
            'value_per_share': value_per_share,
        }

    return results


def reverse_dcf_implied_growth(current_price, shares_outstanding, latest_fcff,
                                wacc=0.105, years=5, net_debt=0.0,
                                g_min=-0.05, g_max=0.20, tol=1.0):
    """
    Reverse DCF: dò ngược tốc độ tăng trưởng g mà thị trường đang "ngụ ý"
    tại giá hiện tại, bằng binary search trên hàm dcf_fcff_scenarios-style.
    Trả về g (float, có thể None nếu không hội tụ hoặc thiếu input).
    """
    if not all([current_price, shares_outstanding, latest_fcff]) or shares_outstanding <= 0:
        return None

    target_equity_value = current_price * shares_outstanding

    def equity_value_for_g(g):
        if wacc <= g:
            return None
        pv_explicit = 0.0
        fcff_t = latest_fcff
        for t in range(1, years + 1):
            fcff_t = fcff_t * (1 + g)
            pv_explicit += fcff_t / ((1 + wacc) ** t)
        terminal_fcff = fcff_t * (1 + g)
        terminal_value = terminal_fcff / (wacc - g)
        pv_terminal = terminal_value / ((1 + wacc) ** years)
        return pv_explicit + pv_terminal - net_debt

    lo, hi = g_min, min(g_max, wacc - 0.001)
    for _ in range(60):
        mid = (lo + hi) / 2
        val = equity_value_for_g(mid)
        if val is None:
            hi = mid
            continue
        if abs(val - target_equity_value) < tol:
            return mid
        if val < target_equity_value:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ============================================================
# 3. GRAHAM NUMBER: sqrt(22.5 x EPS x BVPS)
# ============================================================

def graham_number(eps, bvps):
    """Sanity-check giá trị theo công thức Benjamin Graham. None nếu input âm/0."""
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return (22.5 * eps * bvps) ** 0.5


# ============================================================
# 4. DDM (DIVIDEND DISCOUNT MODEL - GORDON GROWTH)
# ============================================================

def ddm_gordon(dps, required_return=0.12, g=0.03):
    """
    DDM Gordon Growth: Value = DPS x (1+g) / (r - g)
    dps: cổ tức trên mỗi cổ phiếu năm gần nhất (VNĐ)
    Trả về None nếu DPS <= 0 (không chia cổ tức -> DDM không phù hợp,
    giống ghi chú trong bản demo gốc).
    """
    if dps is None or dps <= 0 or required_return <= g:
        return None
    return dps * (1 + g) / (required_return - g)


# ============================================================
# 5. 9 PHƯƠNG PHÁP ĐỊNH GIÁ TỔNG HỢP (dùng PE/PB lịch sử của CHÍNH MÃ)
# ============================================================

def nine_methods_valuation(eps_latest, bvps_latest, pe_series: pd.Series,
                            pb_series: pd.Series, current_price,
                            dcf_results=None, graham_value=None, ddm_value=None):
    """
    Tổng hợp các phương pháp định giá dùng hệ số PE/PB LỊCH SỬ CỦA CHÍNH
    MÃ (median 5N, trung bình 5N, sàn min 5N) -- không cần biết ngành.

    Trả về dict {method_name: estimated_price}, bỏ qua method nào thiếu input.
    """
    methods = {}

    pe_hist = pe_series.dropna() if pe_series is not None else pd.Series(dtype=float)
    pb_hist = pb_series.dropna() if pb_series is not None else pd.Series(dtype=float)

    if eps_latest and eps_latest > 0:
        if not pe_hist.empty:
            methods['PE Median 5N'] = float(pe_hist.median()) * eps_latest
            methods['PE TB 5N'] = float(pe_hist.mean()) * eps_latest

    if bvps_latest and bvps_latest > 0:
        if not pb_hist.empty:
            methods['PB Median 5N'] = float(pb_hist.median()) * bvps_latest
            methods['PB TB 5N'] = float(pb_hist.mean()) * bvps_latest
            methods['PB Sàn 5N (min)'] = float(pb_hist.min()) * bvps_latest

    if dcf_results:
        for name, res in dcf_results.items():
            if res:
                methods[f'DCF ({name})'] = res['value_per_share']

    if graham_value:
        methods['Graham Number'] = graham_value

    if ddm_value:
        methods['DDM (Gordon)'] = ddm_value

    return methods


def summarize_valuation(methods: dict, current_price):
    """
    Tính trung bình, median, dải hợp lý (P25-P75) từ dict các phương pháp,
    và % upside/downside so với giá hiện tại.
    Trả về dict tổng hợp, hoặc None nếu không có method nào.
    """
    values = [v for v in methods.values() if v and v > 0]
    if not values:
        return None

    arr = np.array(values)
    mean_val = float(np.mean(arr))
    median_val = float(np.median(arr))
    p25 = float(np.percentile(arr, 25))
    p75 = float(np.percentile(arr, 75))

    upside_mean = (mean_val / current_price - 1) * 100 if current_price else 0
    upside_median = (median_val / current_price - 1) * 100 if current_price else 0

    if upside_median > 15:
        verdict = "UNDERVALUED · RẺ"
    elif upside_median < -15:
        verdict = "OVERVALUED · ĐẮT"
    else:
        verdict = "FAIRLY VALUED · HỢP LÝ"

    return {
        'mean': mean_val,
        'median': median_val,
        'p25': p25,
        'p75': p75,
        'upside_mean_pct': upside_mean,
        'upside_median_pct': upside_median,
        'verdict': verdict,
    }
