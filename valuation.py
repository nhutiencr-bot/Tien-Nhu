"""
valuation.py
------------
Các hàm định giá cổ phiếu sử dụng trong pipeline.py:
  - dupont_decomposition
  - dcf_fcff_scenarios
  - reverse_dcf_implied_growth
  - graham_number
  - ddm_gordon
  - nine_methods_valuation   ← đảm bảo KHÔNG có None trong values khi trả về
  - summarize_valuation
  - forecast_income_statement ← MỚI: dự phóng KQKD 2026-2027
"""

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════════════════
# 1. DuPont Decomposition
# ══════════════════════════════════════════════════════════════════════════════

def dupont_decomposition(
    revenue_series: pd.Series,
    net_profit_series: pd.Series,
    total_assets_series: pd.Series,
    equity_series: pd.Series,
) -> pd.DataFrame:
    """
    Phân tích DuPont 3 thành phần:
      ROE = Net Margin × Asset Turnover × Equity Multiplier

    Trả về DataFrame với cột: Năm, Net Margin (%), Asset Turnover, Equity Multiplier, ROE (%)
    """
    years = sorted(
        set(revenue_series.index)
        & set(net_profit_series.index)
        & set(total_assets_series.index)
        & set(equity_series.index)
    )

    rows = []
    for y in years:
        rev = revenue_series.get(y)
        np_ = net_profit_series.get(y)
        ta  = total_assets_series.get(y)
        eq  = equity_series.get(y)

        if any(v is None or v == 0 for v in [rev, np_, ta, eq]):
            continue
        try:
            net_margin       = np_ / rev * 100          # %
            asset_turnover   = rev / ta                  # lần
            equity_multiplier = ta / eq                  # lần
            roe              = net_margin * asset_turnover * equity_multiplier / 100
            rows.append({
                "Năm":                y,
                "Net Margin (%)":     round(net_margin, 2),
                "Asset Turnover":     round(asset_turnover, 4),
                "Equity Multiplier":  round(equity_multiplier, 4),
                "ROE (%)":            round(roe, 2),
            })
        except Exception:
            continue

    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 2. DCF – FCFF Scenarios
# ══════════════════════════════════════════════════════════════════════════════

def dcf_fcff_scenarios(
    latest_fcff: float,
    shares_outstanding: float,
    net_debt: float = 0.0,
    projection_years: int = 5,
) -> dict:
    """
    DCF – FCFF với 3 kịch bản tăng trưởng:
      Bi quan: g1=5%, g_terminal=2%, wacc=11%
      Cơ sở:  g1=8%, g_terminal=3%, wacc=10.5%
      Tích cực: g1=12%, g_terminal=3.5%, wacc=10%

    Trả về dict {kịch_bản: giá_trị_VND_mỗi_cổ_phiếu}
    """
    if shares_outstanding <= 0 or latest_fcff <= 0:
        return {}

    scenarios = {
        "DCF Bi quan":  {"g1": 0.05, "g_t": 0.02, "wacc": 0.11},
        "DCF Cơ sở":    {"g1": 0.08, "g_t": 0.03, "wacc": 0.105},
        "DCF Tích cực": {"g1": 0.12, "g_t": 0.035, "wacc": 0.10},
    }

    results = {}
    for label, p in scenarios.items():
        try:
            g1, g_t, wacc = p["g1"], p["g_t"], p["wacc"]
            pv = 0.0
            fcff = latest_fcff
            for t in range(1, projection_years + 1):
                fcff = fcff * (1 + g1)
                pv  += fcff / (1 + wacc) ** t
            # Terminal value
            tv = fcff * (1 + g_t) / (wacc - g_t)
            pv += tv / (1 + wacc) ** projection_years
            # Equity value per share
            equity_value = pv - net_debt
            price = equity_value / shares_outstanding
            if price > 0:
                results[label] = round(price, 0)
        except Exception:
            continue

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 3. Reverse DCF – Implied Growth Rate
# ══════════════════════════════════════════════════════════════════════════════

def reverse_dcf_implied_growth(
    current_price: float,
    shares_outstanding: float,
    latest_fcff: float,
    wacc: float = 0.105,
    net_debt: float = 0.0,
    projection_years: int = 5,
    g_terminal: float = 0.03,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """
    Tìm g (tăng trưởng FCFF giai đoạn 1) sao cho DCF = current_price.
    Dùng bisection search trong khoảng [-0.30, 0.60].
    Trả về g (float) hoặc None nếu không hội tụ.
    """
    if current_price <= 0 or shares_outstanding <= 0 or latest_fcff <= 0:
        return None

    target_equity = current_price * shares_outstanding + net_debt

    def _dcf(g):
        pv = 0.0
        fcff = latest_fcff
        for t in range(1, projection_years + 1):
            fcff = fcff * (1 + g)
            pv  += fcff / (1 + wacc) ** t
        if wacc <= g_terminal:
            return float("inf")
        tv = fcff * (1 + g_terminal) / (wacc - g_terminal)
        pv += tv / (1 + wacc) ** projection_years
        return pv

    lo, hi = -0.30, 0.60
    if _dcf(lo) > target_equity:
        return lo
    if _dcf(hi) < target_equity:
        return hi

    for _ in range(max_iter):
        mid = (lo + hi) / 2
        val = _dcf(mid)
        if abs(val - target_equity) < tol * target_equity:
            return mid
        if val < target_equity:
            lo = mid
        else:
            hi = mid

    return (lo + hi) / 2


# ══════════════════════════════════════════════════════════════════════════════
# 4. Graham Number
# ══════════════════════════════════════════════════════════════════════════════

def graham_number(eps: float, bvps: float) -> float | None:
    """
    Graham Number = sqrt(22.5 × EPS × BVPS)
    Chỉ tính khi cả hai dương.
    """
    if eps > 0 and bvps > 0:
        return round(np.sqrt(22.5 * eps * bvps), 0)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 5. DDM – Gordon Growth Model
# ══════════════════════════════════════════════════════════════════════════════

def ddm_gordon(
    dps: float,
    required_return: float = 0.11,
    g: float = 0.04,
) -> float | None:
    """
    Mô hình DDM Gordon: P = DPS × (1+g) / (ke - g)
    Trả về None nếu ke <= g hoặc DPS = 0.
    """
    if dps <= 0 or required_return <= g:
        return None
    return round(dps * (1 + g) / (required_return - g), 0)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Nine Methods Valuation  ← KHÔNG trả về None trong values
# ══════════════════════════════════════════════════════════════════════════════

def nine_methods_valuation(
    eps_latest: float,
    bvps_latest: float,
    pe_series: pd.Series,
    pb_series: pd.Series,
    current_price: float,
    dcf_results: dict | None = None,
    graham_value: float | None = None,
    ddm_value: float | None = None,
) -> dict:
    """
    Tổng hợp tối đa 9 phương pháp định giá.
    CHỈ đưa vào dict các phương pháp có giá trị hợp lệ (không None, không <= 0).
    → Tránh TypeError khi ui_components.py duyệt values để vẽ biểu đồ.
    """
    results = {}

    # ── P/E Trung bình lịch sử × EPS hiện tại ──────────────────────────────
    if eps_latest > 0 and pe_series is not None and not pe_series.empty:
        pe_clean = pe_series.dropna()
        pe_clean = pe_clean[(pe_clean > 0) & (pe_clean < 200)]
        if not pe_clean.empty:
            pe_avg = pe_clean.mean()
            val = round(pe_avg * eps_latest, 0)
            if val > 0:
                results["P/E TB"] = val

    # ── P/B Trung bình lịch sử × BVPS hiện tại ────────────────────────────
    if bvps_latest > 0 and pb_series is not None and not pb_series.empty:
        pb_clean = pb_series.dropna()
        pb_clean = pb_clean[(pb_clean > 0) & (pb_clean < 50)]
        if not pb_clean.empty:
            pb_avg = pb_clean.mean()
            val = round(pb_avg * bvps_latest, 0)
            if val > 0:
                results["P/B TB"] = val

    # ── P/E Trung vị ────────────────────────────────────────────────────────
    if eps_latest > 0 and pe_series is not None and not pe_series.empty:
        pe_clean = pe_series.dropna()
        pe_clean = pe_clean[(pe_clean > 0) & (pe_clean < 200)]
        if not pe_clean.empty:
            pe_med = pe_clean.median()
            val = round(pe_med * eps_latest, 0)
            if val > 0:
                results["P/E Trung vị"] = val

    # ── Graham Number ───────────────────────────────────────────────────────
    if graham_value is not None and graham_value > 0:
        results["Graham"] = graham_value

    # ── DCF Scenarios ───────────────────────────────────────────────────────
    if dcf_results:
        for label, price in dcf_results.items():
            if price is not None and price > 0:
                results[label] = price

    # ── DDM Gordon ─────────────────────────────────────────────────────────
    if ddm_value is not None and ddm_value > 0:
        results["DDM"] = ddm_value

    # ── Relative: P/E Ngành × EPS (giả định P/E ngành = 15 nếu không có dữ liệu)
    if eps_latest > 0:
        # Dùng P/E ngành mặc định 15× (conservative)
        pe_sector = 15.0
        val = round(pe_sector * eps_latest, 0)
        if val > 0:
            results["P/E Ngành (15×)"] = val

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 7. Summarize Valuation
# ══════════════════════════════════════════════════════════════════════════════

def summarize_valuation(methods: dict, current_price: float) -> dict:
    """
    Tổng hợp kết quả định giá:
      - avg_value:   trung bình các phương pháp
      - median_value: trung vị
      - upside_pct:  % upside/downside so với giá hiện tại
      - verdict:     "UNDERVALUED" / "FAIR" / "OVERVALUED"
    """
    if not methods or current_price <= 0:
        return {}

    valid_values = [v for v in methods.values() if v is not None and v > 0]
    if not valid_values:
        return {}

    avg    = float(np.mean(valid_values))
    median = float(np.median(valid_values))
    low    = float(np.min(valid_values))
    high   = float(np.max(valid_values))
    upside = (avg / current_price - 1) * 100

    if upside > 15:
        verdict = "UNDERVALUED"
    elif upside < -15:
        verdict = "OVERVALUED"
    else:
        verdict = "FAIR VALUE"

    return {
        "avg_value":    round(avg, 0),
        "median_value": round(median, 0),
        "low_value":    round(low, 0),
        "high_value":   round(high, 0),
        "upside_pct":   round(upside, 1),
        "verdict":      verdict,
        "n_methods":    len(valid_values),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 8. Forecast Income Statement  (MỚI – Dự phóng KQKD 2026-2027)
# ══════════════════════════════════════════════════════════════════════════════

def forecast_income_statement(
    revenue_series: pd.Series,
    net_profit_series: pd.Series,
    eps_series: pd.Series,
    shares_outstanding: float = 0.0,
    forecast_years: int = 2,
    current_year: int | None = None,
) -> pd.DataFrame:
    """
    Dự phóng Doanh thu, LNST, EPS cho N năm tiếp theo dựa trên CAGR lịch sử.

    Logic:
      1. Tính CAGR doanh thu và LNST từ chuỗi lịch sử (tối đa 5 năm).
      2. Áp dụng 3 kịch bản:
           - Thận trọng: CAGR × 0.6
           - Cơ sở:      CAGR × 1.0
           - Tích cực:   CAGR × 1.4  (tối đa +50% vs cơ sở, capped ở 50%)
      3. Trả về DataFrame dạng rộng để hiển thị trực tiếp trong Streamlit.

    Columns: Năm | Kịch bản | DT Dự phóng (tỷ) | LNST Dự phóng (tỷ) | EPS Dự phóng (đ) | Tăng trưởng DT (%) | Tăng trưởng LNST (%)
    """
    import datetime
    if current_year is None:
        current_year = datetime.datetime.today().year

    # ── Tính CAGR ─────────────────────────────────────────────────────────
    def _cagr(series: pd.Series, n: int = 5) -> float | None:
        if series is None or series.empty:
            return None
        s = series.sort_index().dropna()
        s = s[s > 0]
        if len(s) < 2:
            return None
        s = s.tail(n)
        years = len(s) - 1
        if years <= 0:
            return None
        try:
            return (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1
        except Exception:
            return None

    rev_cagr = _cagr(revenue_series)
    np_cagr  = _cagr(net_profit_series)

    # Nếu không có CAGR, dùng mặc định 8% / 10%
    if rev_cagr is None or np.isnan(rev_cagr):
        rev_cagr = 0.08
    if np_cagr is None or np.isnan(np_cagr):
        np_cagr = 0.10

    # Kẹp CAGR trong khoảng hợp lý [-20%, 50%]
    rev_cagr = max(-0.20, min(0.50, rev_cagr))
    np_cagr  = max(-0.20, min(0.50, np_cagr))

    # Lấy giá trị gốc (năm gần nhất trước current_year)
    def _base(series: pd.Series) -> float:
        if series is None or series.empty:
            return 0.0
        s = series[series.index < current_year].sort_index()
        if s.empty:
            s = series.sort_index()
        valid = s.dropna()
        return float(valid.iloc[-1]) if not valid.empty else 0.0

    base_rev = _base(revenue_series)
    base_np  = _base(net_profit_series)

    scenarios = {
        "Thận trọng": 0.6,
        "Cơ sở":      1.0,
        "Tích cực":   1.4,
    }

    rows = []
    for scenario, mult in scenarios.items():
        g_rev = rev_cagr * mult
        g_np  = np_cagr  * mult

        rev = base_rev
        np_ = base_np

        for i in range(1, forecast_years + 1):
            year = current_year + i
            rev  = rev * (1 + g_rev)  if rev > 0 else 0.0
            np_  = np_  * (1 + g_np)  if np_ > 0 else 0.0
            eps  = (np_ * 1e9 / shares_outstanding) if shares_outstanding > 0 else 0.0

            rows.append({
                "Năm":                     year,
                "Kịch bản":               scenario,
                "DT Dự phóng (tỷ)":       round(rev, 1),
                "LNST Dự phóng (tỷ)":     round(np_, 1),
                "EPS Dự phóng (đ)":       round(eps, 0) if eps > 0 else None,
                "Tăng trưởng DT (%)":     round(g_rev * 100, 1),
                "Tăng trưởng LNST (%)":   round(g_np  * 100, 1),
            })

    df = pd.DataFrame(rows)
    return df
