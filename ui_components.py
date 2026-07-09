"""
ui_components.py
-----------------
Tách toàn bộ component UI ra khỏi app.py để giảm tải render,
tăng tốc độ và dễ maintain.

CHANGELOG:
  - FIX ROE/ROA CAGR: bỏ hardcode "—" cho dòng có '%', thay bằng 2 chế độ:
      * is_pct_compound: False → CAGR compound bình thường (EPS, BVPS)
      * is_pct_compound: True  → Hiển thị thay đổi điểm %% tuyệt đối (pp)
        VD: ROE từ 29.66% → 26.64% = -3.02 pp (không dùng compound để tránh
        hiểu nhầm: "CAGR ROE -2.65%" gây lẫn với tăng trưởng doanh thu)
      Header cột đổi từ "CAGR" thành "CAGR / Δpp" để phân biệt 2 loại.
  - FIX Tab Multiples Section 02: P/CF và EV/EBITDA tính đúng từ key metrics
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd


def fmt(value, suffix="", decimals=2, na="—"):
    if value is None:
        return na
    try:
        if value != value:
            return na
        return f"{value:,.{decimals}f}{suffix}"
    except Exception:
        return na


def format_market_cap_billion(value_billion):
    if value_billion is None or value_billion != value_billion or value_billion <= 0:
        return "—"
    return f"{value_billion:,.0f} Tỷ VNĐ"


@st.cache_data(ttl=300)
def _cached_format_table(df_json):
    """Cache việc format bảng để tránh re-render mỗi lần."""
    df = pd.read_json(df_json)
    numeric_cols = [c for c in df.columns if c != 'Năm']
    for col in numeric_cols:
        try:
            df[col] = df[col].apply(
                lambda x: "{:,.2f}".format(float(x))
                if pd.notnull(x) and str(x).strip() != "" else "—"
            )
        except Exception:
            pass
    return df


def render_kpi_cards(metrics, fundamentals):
    kpi1, kpi2, kpi3, kpi4, kpi5, kpi6 = st.columns(6)
    kpi1.metric("Thị Giá Hiện Tại", f"{metrics.get('current_price', 0) or 0:,.0f} đ")
    kpi2.metric("Vốn Hóa", format_market_cap_billion(metrics.get('market_cap_billion', 0) or 0))
    kpi3.metric("P/E (TTM)", f"{metrics.get('pe', 0) or 0:.2f} x")
    kpi4.metric("P/B (TTM)", f"{metrics.get('pb', 0) or 0:.2f} x")
    kpi5.metric("ROE Gần Nhất", fmt(fundamentals['roe_latest'], suffix="%"))
    kpi6.metric("CAGR LNST 5N", fmt(fundamentals['net_profit_cagr_pct'], suffix="%"))


def render_tab_kqkd(df_5y_table, fundamentals, period_col='Năm'):
    label = "5 Năm" if period_col == 'Năm' else "Theo Quý"
    st.markdown(f"### Kết Quả Kinh Doanh {label}")

    if df_5y_table.empty:
        st.warning(f"Không có đủ dữ liệu BCTC {label.lower()} cho mã này.")
        return

    # Chart doanh thu + LNST
    fig_kqkd = go.Figure()
    if df_5y_table['Doanh thu thuần (tỷ)'].notna().any():
        fig_kqkd.add_trace(go.Bar(
            x=df_5y_table[period_col], y=df_5y_table['Doanh thu thuần (tỷ)'],
            name='Doanh thu thuần (tỷ)', marker_color='#a855f7', yaxis='y1'
        ))
    if df_5y_table['LNST (tỷ)'].notna().any():
        fig_kqkd.add_trace(go.Scatter(
            x=df_5y_table[period_col], y=df_5y_table['LNST (tỷ)'],
            name='LNST (tỷ)', line=dict(color='#ec4899', width=3), yaxis='y2'
        ))
    fig_kqkd.update_layout(
        template='plotly_dark',
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(type='category'),
        yaxis=dict(title='Doanh thu (tỷ)'),
        yaxis2=dict(title='LNST (tỷ)', overlaying='y', side='right'),
        legend=dict(orientation='h', y=1.1),
        margin=dict(t=40, b=20),
    )
    st.plotly_chart(fig_kqkd, use_container_width=True)

    if period_col == 'Năm':
        c1, c2 = st.columns(2)
        c1.metric("CAGR Doanh Thu (5N)", fmt(fundamentals['revenue_cagr_pct'], suffix="%"))
        c2.metric("CAGR LNST (5N)", fmt(fundamentals['net_profit_cagr_pct'], suffix="%"))

    # Chart biên lợi nhuận
    st.markdown("### Biên Lợi Nhuận & ROE")
    fig_margin = go.Figure()
    if 'ROE (%)' in df_5y_table.columns and df_5y_table['ROE (%)'].notna().any():
        fig_margin.add_trace(go.Scatter(
            x=df_5y_table[period_col], y=df_5y_table['ROE (%)'],
            name='ROE (%)', line=dict(color='#06b6d4', width=2, dash='dash')
        ))
    dtt  = df_5y_table['Doanh thu thuần (tỷ)']
    lnst = df_5y_table['LNST (tỷ)']
    if dtt.notna().any() and lnst.notna().any() and (dtt != 0).any():
        ros = (lnst / dtt.replace(0, float('nan')) * 100)
        fig_margin.add_trace(go.Scatter(
            x=df_5y_table[period_col], y=ros,
            name='ROS - Biên LNST (%)', line=dict(color='#ec4899', width=2)
        ))
    fig_margin.update_layout(
        template='plotly_dark',
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(type='category'),
        margin=dict(t=20, b=20),
    )
    st.plotly_chart(fig_margin, use_container_width=True)

    # ══════════════════════════════════════════════════════════════════
    # Bảng tổng hợp: hàng = chỉ tiêu, cột = kỳ + CAGR/Δpp + Tăng trưởng
    # ══════════════════════════════════════════════════════════════════
    st.markdown(f"### Bảng Tổng Hợp Tài Chính {label}")

    indicator_cols = [c for c in df_5y_table.columns if c != period_col]
    periods = df_5y_table[period_col].tolist()

    def _calc_cagr(series_vals, n_periods_per_year=1):
        valid = [(i, v) for i, v in enumerate(series_vals)
                 if pd.notnull(v) and v != 0]
        if len(valid) < 2:
            return None
        (i0, v0), (i1, v1) = valid[0], valid[-1]
        n_periods = i1 - i0
        if n_periods <= 0:
            return None
        n_years = n_periods / n_periods_per_year
        if v0 <= 0 or v1 <= 0 or n_years <= 0:
            return None
        try:
            return ((v1 / v0) ** (1 / n_years) - 1) * 100
        except Exception:
            return None

    def _calc_delta_pp(series_vals):
        valid = [v for v in series_vals if pd.notnull(v) and v != 0]
        if len(valid) < 2:
            return None
        return valid[-1] - valid[0]

    n_per_year = 4 if period_col == 'Quý' else 1

    def _is_pct_ratio(col_name: str) -> bool:
        lower = col_name.lower()
        return ('roe' in lower or 'roa' in lower)

    def _sparkline(series_vals):
        bars = "▁▂▃▄▅▆▇█"
        valid_vals = [v for v in series_vals if pd.notnull(v)]
        if len(valid_vals) < 2:
            return "—"
        lo, hi = min(valid_vals), max(valid_vals)
        rng = (hi - lo) if hi != lo else 1
        out = []
        for v in series_vals:
            if pd.isnull(v):
                out.append(" ")
            else:
                idx = int((v - lo) / rng * (len(bars) - 1))
                out.append(bars[idx])
        trend_up = valid_vals[-1] >= valid_vals[0]
        arrow = "🟢▲" if trend_up else "🔴▼"
        return f"{''.join(out)} {arrow}"

    rows = []
    for col in indicator_cols:
        vals = df_5y_table[col].tolist()
        row = {"Chỉ tiêu": col}

        for p, v in zip(periods, vals):
            row[p] = "—" if pd.isnull(v) else "{:,.2f}".format(float(v))

        if _is_pct_ratio(col):
            delta = _calc_delta_pp(vals)
            if delta is not None:
                sign = "+" if delta >= 0 else ""
                row["CAGR / Δpp"] = f"{sign}{delta:.2f} pp"
            else:
                row["CAGR / Δpp"] = "—"
        else:
            cagr_val = _calc_cagr(vals, n_periods_per_year=n_per_year)
            row["CAGR / Δpp"] = fmt(cagr_val, suffix="%") if cagr_val is not None else "—"

        row["Tăng trưởng"] = _sparkline(vals)
        rows.append(row)

    df_display = pd.DataFrame(rows).set_index("Chỉ tiêu")
    st.dataframe(df_display, use_container_width=True)

    st.caption(
        "CAGR = Tốc độ tăng trưởng kép giữa kỳ đầu và kỳ cuối có dữ liệu trong bảng "
        f"(theo {'năm' if period_col == 'Năm' else 'quý, quy đổi ra năm'}). "
        "Cột 'Tăng trưởng' là biểu đồ mini thể hiện xu hướng qua các kỳ. "
        "ROE/ROA hiển thị Δpp (thay đổi điểm phần trăm tuyệt đối đầu → cuối kỳ) "
        "thay vì CAGR compound để tránh hiểu nhầm về bản chất tỷ lệ sinh lời."
    )
    st.caption(
        "ℹ️ Một số năm cũ (bù từ nguồn phụ CafeF khi nguồn chính không có) có thể "
        "thiếu EPS/BVPS — 2 chỉ số này bắt buộc cần đúng số CP lưu hành của năm đó, "
        "nếu không có sẽ để trống thay vì suy đoán sai. ROE/ROA vẫn được suy ra trực "
        "tiếp từ LNST/Vốn CSH/Tổng tài sản (không cần số CP) nên vẫn hiển thị đầy đủ."
    )


_PBV_CARD_CSS = """
<style>
.pbv-hero {
  border-radius: 20px; padding: 30px 26px; margin-bottom: 20px; text-align: center;
  background: radial-gradient(circle at 50% 0%, rgba(168,85,247,0.16), rgba(15,15,25,0.35));
  border: 1px solid rgba(168,85,247,0.25);
}
.pbv-hero-eyebrow { color: #d4d4e0; font-size: 16px; font-weight: 700; margin-bottom: 2px; }
.pbv-hero-sub { color: #9a9aab; font-size: 13px; margin-bottom: 18px; }
.pbv-hero-value {
  font-size: 50px; font-weight: 800; font-family: 'Courier New', monospace;
  background: linear-gradient(90deg, #a855f7, #ec4899);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.pbv-hero-unit { color: #9a9aab; font-size: 13px; margin: 6px 0 18px; }
.pbv-pill {
  display: inline-block; padding: 9px 24px; border-radius: 30px;
  font-weight: 800; font-size: 14px; letter-spacing: 0.5px; margin-bottom: 16px;
}
.pbv-pill-cheap { background: #22c55e; color: #052e13; }
.pbv-pill-fair { background: #fbbf24; color: #3a2a00; }
.pbv-pill-expensive { background: #f43f5e; color: #3a0512; }
.pbv-hero-compare { color: #c7c7d6; font-size: 14px; }

.pbv-card {
  border-radius: 16px; padding: 18px 20px 20px; margin-bottom: 14px; position: relative;
  border: 1px solid rgba(255,255,255,0.06); background: rgba(255,255,255,0.03); min-height: 148px;
}
.pbv-card-reco {
  background: linear-gradient(135deg, rgba(168,85,247,0.16), rgba(236,72,153,0.10));
  border-color: rgba(168,85,247,0.30);
}
.pbv-card-badge {
  position: absolute; top: 14px; right: 16px;
  background: linear-gradient(90deg, #a855f7, #ec4899); color: white;
  font-size: 10px; font-weight: 800; padding: 3px 10px; border-radius: 20px; letter-spacing: 0.5px;
}
.pbv-card-title { color: #9a9aab; font-size: 12px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; }
.pbv-card-value { font-size: 27px; font-weight: 800; font-family: 'Courier New', monospace; margin-top: 8px; }
.pbv-card-pct { font-size: 14px; font-weight: 700; margin-top: 2px; }
.pbv-bar-track { width: 100%; height: 6px; border-radius: 4px; background: rgba(255,255,255,0.08); margin-top: 12px; overflow: hidden; }
.pbv-bar-fill { height: 100%; border-radius: 4px; }
.pbv-card-sub { color: #6b6b7b; font-size: 12px; margin-top: 8px; }
.val-up { color: #22c55e; } .val-down { color: #f43f5e; }
</style>
"""


def render_tab_valuation(valuation_pkg, metrics):
    st.markdown(_PBV_CARD_CSS, unsafe_allow_html=True)
    st.markdown("### 💰 PE · PB · BV Trung Bình 5 Năm")

    cp = metrics.get('current_price') or 0
    price_s = valuation_pkg.get('price_series')
    eps_adj_s = valuation_pkg.get('eps_series_adj')
    bvps_adj_s = valuation_pkg.get('bvps_series_adj')

    # FIX Bẫy 5B (mixing base): nếu có price_series + eps/bvps đã split-adjusted
    # (cùng số CP hiện tại với giá) → tự tính lại PE/PB lịch sử nhất quán,
    # thay vì dùng thẳng pe_series/pb_series thô từ vendor (có thể lệch base
    # ở các năm có phát hành CP thưởng/chia tách → PE/PB bị phình hoặc bóp méo).
    pe_all = pb_all = None
    if (price_s is not None and not price_s.empty
            and eps_adj_s is not None and not eps_adj_s.empty):
        common_y = sorted(set(price_s.index) & set(eps_adj_s.index))
        vals = {y: price_s[y] / eps_adj_s[y] for y in common_y
                if eps_adj_s[y] and eps_adj_s[y] > 0}
        if vals:
            pe_all = pd.Series(vals).sort_index()
    if (price_s is not None and not price_s.empty
            and bvps_adj_s is not None and not bvps_adj_s.empty):
        common_y = sorted(set(price_s.index) & set(bvps_adj_s.index))
        vals = {y: price_s[y] / bvps_adj_s[y] for y in common_y
                if bvps_adj_s[y] and bvps_adj_s[y] > 0}
        if vals:
            pb_all = pd.Series(vals).sort_index()

    # Fallback: không có đủ dữ liệu split-adjusted → dùng ratio thô từ vendor
    if pe_all is None:
        pe_all = valuation_pkg.get('pe_series')
    if pb_all is None:
        pb_all = valuation_pkg.get('pb_series')
    bvps_s = bvps_adj_s if bvps_adj_s is not None and not bvps_adj_s.empty \
        else valuation_pkg.get('bvps_series')

    pe_pos = pe_all[pe_all > 0] if pe_all is not None and not pe_all.empty else None
    pb_pos = pb_all[pb_all > 0] if pb_all is not None and not pb_all.empty else None

    if pb_pos is None or pb_pos.empty or not cp:
        st.warning("Không đủ dữ liệu lịch sử P/E, P/B để dựng các kịch bản định giá.")
        return

    bvps_latest = float(bvps_s.iloc[-1]) if bvps_s is not None and not bvps_s.empty else (
        cp / metrics['pb'] if metrics.get('pb') else 0)
    eps_latest = float(eps_adj_s.iloc[-1]) if eps_adj_s is not None and not eps_adj_s.empty else (
        cp / metrics['pe'] if metrics.get('pe') else 0)

    if not bvps_latest:
        st.warning("Không đủ dữ liệu BVPS để dựng các kịch bản định giá.")
        return

    latest_year = pb_all.index[-1] if pb_all is not None and not pb_all.empty else "gần nhất"
    yy = str(latest_year)[-2:]
    st.caption(f"Áp hệ số tham chiếu lên số liệu {latest_year}")

    pb_med, pb_mean, pb_max = pb_pos.median(), pb_pos.mean(), pb_pos.max()
    pe_med = pe_pos.median() if pe_pos is not None and not pe_pos.empty else None

    scenarios = [
        dict(label="PB 1.0x (Sàn)", value=bvps_latest * 1.0,
             sub=f"BVPS'{yy}", reco=False),
        dict(label="PB Median 5N", value=bvps_latest * pb_med,
             sub=f"BVPS'{yy} × PB {pb_med:.2f}x", reco=False),
        dict(label="PB TB 5N", value=bvps_latest * pb_mean,
             sub=f"BVPS'{yy} × PB {pb_mean:.2f}x", reco=True),
    ]
    if pe_med and eps_latest:
        scenarios.append(dict(label="PE Median 5N", value=eps_latest * pe_med,
                               sub=f"EPS'{yy} × PE {pe_med:.2f}x", reco=True))
    scenarios.append(dict(label="PB Cao Nhất 5N (Trần)", value=bvps_latest * pb_max,
                           sub=f"BVPS'{yy} × PB {pb_max:.2f}x", reco=False))
    scenarios = [s for s in scenarios if s['value'] and s['value'] > 0]

    if not scenarios:
        st.warning("Không đủ dữ liệu để dựng các kịch bản định giá.")
        return

    # Giá trị hợp lý = trung bình trọng số (kịch bản khuyến nghị x2)
    w_sum = sum(2 if s['reco'] else 1 for s in scenarios)
    fair_value = sum(s['value'] * (2 if s['reco'] else 1) for s in scenarios) / w_sum
    fair_pct = (fair_value / cp - 1) * 100 if cp else 0

    if fair_pct >= 10:
        pill_cls, pill_label, compare_word = "cheap", "⬇️ UNDERVALUED", "rẻ hơn"
    elif fair_pct <= -10:
        pill_cls, pill_label, compare_word = "expensive", "⬆️ OVERVALUED", "đắt hơn"
    else:
        pill_cls, pill_label, compare_word = "fair", "⚖️ FAIRLY VALUED", "ngang giá"

    st.markdown(f"""
<div class="pbv-hero">
  <div class="pbv-hero-eyebrow">Giá trị hợp lý ước tính</div>
  <div class="pbv-hero-sub">Trung bình trọng số các kịch bản</div>
  <div class="pbv-hero-value">đ{_fmt_k(fair_value)}</div>
  <div class="pbv-hero-unit">nghìn đồng / cổ phiếu</div>
  <div class="pbv-pill pbv-pill-{pill_cls}">{pill_label}</div>
  <div class="pbv-hero-compare">Giá hiện đ{_fmt_k(cp)} → {compare_word} ~{abs(fair_pct):.0f}%</div>
</div>""", unsafe_allow_html=True)

    st.markdown("#### 5 Kịch Bản Định Giá")
    st.caption("Hệ số PE/PB tham chiếu × EPS & BVPS gần nhất")

    max_val = max(s['value'] for s in scenarios)
    cols = st.columns(2)
    for i, s in enumerate(scenarios):
        pct = (s['value'] / cp - 1) * 100 if cp else 0
        width = max(15, min(100, s['value'] / max_val * 100)) if max_val else 15
        if s['reco']:
            bar_style = "background:linear-gradient(90deg,#a855f7,#ec4899);"
        elif pct < 0:
            bar_style = "background:#f43f5e;"
        else:
            bar_style = "background:#22c55e;"
        card_cls = "pbv-card pbv-card-reco" if s['reco'] else "pbv-card"
        badge_html = '<div class="pbv-card-badge">KHUYẾN NGHỊ</div>' if s['reco'] else ''
        val_cls = "val-up" if pct >= 0 else "val-down"
        cols[i % 2].markdown(f"""
<div class="{card_cls}">
  {badge_html}
  <div class="pbv-card-title">{s['label']}</div>
  <div class="pbv-card-value">{_fmt_k(s['value'])}</div>
  <div class="pbv-card-pct {val_cls}">{pct:+.0f}%</div>
  <div class="pbv-bar-track"><div class="pbv-bar-fill" style="width:{width:.0f}%;{bar_style}"></div></div>
  <div class="pbv-card-sub">{s['sub']}</div>
</div>""", unsafe_allow_html=True)

    # --- Lịch sử P/E & P/B 5 năm ---
    if pe_all is not None and not pe_all.empty:
        latest_pb = pb_all.iloc[-1] if pb_all is not None and not pb_all.empty else None
        pb_note = ""
        if latest_pb is not None and pb_med:
            if latest_pb < pb_med * 0.85:
                pb_note = f"{latest_year}: P/B đang thấp hơn trung vị lịch sử — vùng \"mua\" theo lịch sử"
            elif latest_pb > pb_med * 1.15:
                pb_note = f"{latest_year}: P/B đang cao hơn trung vị lịch sử — cân nhắc thận trọng"
            else:
                pb_note = f"{latest_year}: P/B quanh mức trung vị 5 năm"

        st.markdown("#### Lịch Sử P/E & P/B 5 Năm")
        if pb_note:
            st.caption(pb_note)
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=pe_all.index, y=pe_all.values, name='P/E',
                                   mode='lines+markers',
                                   line=dict(color='#a855f7', width=3),
                                   marker=dict(size=8, color='#a855f7', line=dict(color='white', width=1)),
                                   yaxis='y1'))
        if pb_all is not None and not pb_all.empty:
            fig2.add_trace(go.Scatter(x=pb_all.index, y=pb_all.values, name='P/B',
                                       mode='lines+markers',
                                       line=dict(color='#22c55e', width=3),
                                       marker=dict(size=8, color='#22c55e', line=dict(color='white', width=1)),
                                       yaxis='y2'))
        fig2.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            yaxis=dict(title='P/E (x)', gridcolor='rgba(255,255,255,0.06)'),
            yaxis2=dict(title='P/B (x)', overlaying='y', side='right'),
            legend=dict(orientation='h', y=1.15),
            margin=dict(t=30, b=20),
        )
        st.plotly_chart(fig2, use_container_width=True)

    # --- Giá vs Giá trị sổ sách (BVPS) ---
    if price_s is not None and not price_s.empty and bvps_s is not None and not bvps_s.empty:
        common_idx = sorted(set(price_s.index) & set(bvps_s.index))
        if common_idx:
            last_y = common_idx[-1]
            last_price = price_s.get(last_y)
            last_bvps = bvps_s.get(last_y)
            bvps_note = ""
            if last_price and last_bvps:
                premium_pct = (last_price / last_bvps - 1) * 100
                word = "premium" if premium_pct >= 0 else "chiết khấu"
                sign = ">" if last_price >= last_bvps else "<"
                bvps_note = (f"{last_y}: Giá ({last_price:,.0f}đ) {sign} "
                             f"BVPS ({last_bvps:,.0f}đ) — {word} ~{abs(premium_pct):.0f}%")

            st.markdown("#### Giá vs Giá Trị Sổ Sách (BVPS)")
            if bvps_note:
                st.caption(bvps_note)
            fig3 = go.Figure()
            fig3.add_trace(go.Bar(x=common_idx, y=[bvps_s.get(y) for y in common_idx],
                                   name='BVPS (đ)', marker_color='#0ea5e9', opacity=0.55))
            fig3.add_trace(go.Scatter(x=common_idx, y=[price_s.get(y) for y in common_idx],
                                       name='Giá (đ)', mode='lines+markers',
                                       line=dict(color='#ec4899', width=3),
                                       marker=dict(size=8, color='#ec4899', line=dict(color='white', width=1))))
            fig3.update_layout(
                template='plotly_dark',
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                legend=dict(orientation='h', y=1.15),
                margin=dict(t=30, b=20),
            )
            st.plotly_chart(fig3, use_container_width=True)


def _fmt_k(value):
    if value is None:
        return "—"
    try:
        return f"{value/1000:,.1f}K"
    except Exception:
        return "—"


_DCF_CARD_CSS = """
<style>
.dcf-card {
border-radius: 16px; padding: 20px 22px; margin-bottom: 14px;
border: 1px solid rgba(255,255,255,0.06);
}
.dcf-card-bear { background: rgba(244, 63, 94, 0.10); border-color: rgba(244,63,94,0.25); }
.dcf-card-base { background: linear-gradient(135deg, rgba(168,85,247,0.16), rgba(236,72,153,0.10)); border-color: rgba(168,85,247,0.30); }
.dcf-card-bull { background: rgba(16, 185, 129, 0.10); border-color: rgba(16,185,129,0.28); }
.dcf-card-neutral{ background: rgba(255,255,255,0.03); }
.dcf-card-header { display:flex; justify-content:space-between; align-items:center; }
.dcf-card-title { font-size: 17px; font-weight: 700; color: #f1f1f6; }
.dcf-card-badge { background: linear-gradient(90deg, #a855f7, #ec4899); color: white; font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 20px; letter-spacing: 0.5px; }
.dcf-card-sub { color: #9a9aab; font-size: 13px; margin-top: 4px; }
.dcf-card-bottom { display:flex; justify-content:space-between; align-items:flex-end; margin-top: 10px; }
.dcf-card-value { font-size: 30px; font-weight: 800; font-family: 'Courier New', monospace; }
.dcf-card-pct { font-size: 14px; font-weight: 700; }
.val-bear { color: #f43f5e; } .val-base { color: #f1f1f6; } .val-bull { color: #22c55e; }
.pct-bear { color: #f43f5e; } .pct-base { color: #22c55e; } .pct-bull { color: #22c55e; }
.simple-card { border-radius: 16px; padding: 20px 22px; margin-bottom: 14px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); }
.simple-card-eyebrow { color: #9a9aab; font-size: 12px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 6px; }
.simple-card-title { font-size: 17px; font-weight: 700; color: #f1f1f6; margin-bottom: 4px; }
.simple-card-sub { color: #9a9aab; font-size: 13px; margin-bottom: 14px; }
.graham-row { display:flex; align-items:baseline; gap: 14px; margin-bottom: 16px; }
.graham-val { font-size: 34px; font-weight: 800; font-family: 'Courier New', monospace; color: #22c55e; }
.graham-vs { color: #6b6b7b; font-size: 16px; }
.graham-cur { font-size: 34px; font-weight: 800; font-family: 'Courier New', monospace; color: #f1f1f6; }
.graham-label { color: #9a9aab; font-size: 12px; display:block; margin-top: 2px; }
.verdict-pill { border-radius: 12px; padding: 12px 16px; font-weight: 700; font-size: 15px; text-align: center; }
.verdict-cheap { background: rgba(34,197,94,0.15); color: #22c55e; }
.verdict-expensive { background: rgba(244,63,94,0.15); color: #f43f5e; }
.verdict-fair { background: rgba(255,255,255,0.06); color: #d4d4e0; }
.big-metric-value { font-size: 34px; font-weight: 800; font-family: 'Courier New', monospace; }
</style>
"""


def render_tab_dcf(valuation_pkg, metrics):
    st.markdown(_DCF_CARD_CSS, unsafe_allow_html=True)
    st.markdown("### Định Giá Nội Tại · DCF (FCFF) & Graham")

    sector_labels = {
        'bank': 'Ngân hàng', 'steel': 'Thép / Công nghiệp nặng',
        'real_estate': 'Bất động sản', 'retail': 'Bán lẻ / Tiêu dùng',
        'tech': 'Công nghệ / Viễn thông', 'oil_gas': 'Dầu khí / Hoá chất',
        'aviation': 'Hàng không / Vận tải', 'default': 'Chưa xác định (dùng WACC mặc định)',
    }
    sector = valuation_pkg.get('sector_detected', 'default')
    wacc_base_pct = valuation_pkg.get('wacc_base_pct')
    if wacc_base_pct is not None:
        st.caption(
            f"📌 Ngành nhận diện: **{sector_labels.get(sector, sector)}** · "
            f"WACC cơ sở theo ngành: **~{wacc_base_pct:.1f}%** "
            f"(áp dụng cho kịch bản DCF bên dưới — không dùng chung 10.5% cho mọi mã)."
        )

    current_price = metrics.get('current_price', 0)
    dcf = valuation_pkg.get('dcf_scenarios')

    if dcf:
        st.markdown(f"##### DCF — 3 Kịch Bản FCFF")
        order = [('Bi quan', '🔻', 'bear'), ('Cơ sở', '⚖️', 'base'), ('Tích cực', '🚀', 'bull')]
        for name, icon, tone in order:
            res = dcf.get(name)
            if not res:
                continue
            pct = (res['value_per_share'] / current_price - 1) * 100 if current_price else 0
            wacc_pct = res.get('wacc', 0) * 100
            g_pct    = res.get('g', 0) * 100
            badge_html = '<span class="dcf-card-badge">BASE</span>' if tone == 'base' else ''
            st.markdown(f"""
<div class="dcf-card dcf-card-{tone}">
  <div class="dcf-card-header">
    <span class="dcf-card-title">{icon} {name}</span>{badge_html}
  </div>
  <div class="dcf-card-sub">WACC {wacc_pct:.0f}% · g {g_pct:.1f}%</div>
  <div class="dcf-card-bottom"><span></span>
    <div style="text-align:right;">
      <div class="dcf-card-value val-{tone}">{_fmt_k(res['value_per_share'])}</div>
      <div class="dcf-card-pct pct-{tone}">{pct:+.0f}%</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)
    else:
        st.warning("Không tính được DCF do thiếu dữ liệu dòng tiền.")

    graham = valuation_pkg.get('graham_value')
    if graham:
        g_pct = (graham / current_price - 1) * 100 if current_price else 0
        if g_pct > 10:
            verdict_class, verdict_text = "verdict-cheap", f"✅ RẺ {g_pct:.0f}% theo Graham Number"
        elif g_pct < -10:
            verdict_class, verdict_text = "verdict-expensive", f"⚠️ ĐẮT {abs(g_pct):.0f}% theo Graham Number"
        else:
            verdict_class, verdict_text = "verdict-fair", "⚖️ Giá đang quanh mức hợp lý theo Graham Number"
        st.markdown(f"""
<div class="simple-card">
  <div class="simple-card-title">Graham Number √(22.5 × EPS × BVPS)</div>
  <div class="simple-card-sub">Sanity check đầu tư giá trị</div>
  <div class="graham-row">
    <div><div class="graham-val">{_fmt_k(graham)}</div><span class="graham-label">Graham</span></div>
    <span class="graham-vs">vs</span>
    <div><div class="graham-cur">{_fmt_k(current_price)}</div><span class="graham-label">Giá hiện tại</span></div>
  </div>
  <div class="verdict-pill {verdict_class}">{verdict_text}</div>
</div>""", unsafe_allow_html=True)

    reverse_g = valuation_pkg.get('reverse_dcf_g_pct')
    if reverse_g is not None:
        st.markdown(f"""
<div class="simple-card">
  <div class="simple-card-eyebrow">🔄 Reverse DCF</div>
  <div class="big-metric-value" style="color:#22c55e;">~{reverse_g:.0f}%/năm</div>
  <div class="simple-card-sub" style="margin-top:8px;margin-bottom:0;">
    Tại giá {_fmt_k(current_price)}, thị trường đang ngụ ý tốc độ tăng trưởng FCFF ~{reverse_g:.0f}%/năm.
  </div>
</div>""", unsafe_allow_html=True)

    ddm = valuation_pkg.get('ddm_value')
    if ddm:
        ddm_pct = (ddm / current_price - 1) * 100 if current_price else 0
        ddm_color = "#22c55e" if ddm_pct >= 0 else "#f43f5e"
        st.markdown(f"""
<div class="simple-card">
  <div class="simple-card-eyebrow">🔋 DDM (Gordon)</div>
  <div class="big-metric-value" style="color:{ddm_color};">{_fmt_k(ddm)}</div>
  <div class="simple-card-sub" style="margin-top:8px;margin-bottom:0;">So với giá hiện tại: {ddm_pct:+.0f}%.</div>
</div>""", unsafe_allow_html=True)
    else:
        st.markdown("""
<div class="simple-card">
  <div class="simple-card-eyebrow">🔋 DDM (Gordon)</div>
  <div class="simple-card-sub" style="margin-bottom:0;">Không áp dụng — thiếu DPS hoặc mã không chia cổ tức tiền mặt đều đặn.</div>
</div>""", unsafe_allow_html=True)


def render_tab_dupont(df_dupont):
    st.markdown("### DuPont · Chất Lượng ROE")
    st.caption("ROE = Biên Lợi Nhuận × Vòng Quay Tài Sản × Đòn Bẩy Tài Chính")

    if df_dupont is None or df_dupont.empty:
        st.warning("Không đủ dữ liệu để phân tách DuPont.")
        return

    COL_MARGIN   = 'Net margin (%)'
    COL_TURNOVER = 'Asset turnover (x)'
    COL_LEVERAGE = 'Equity multiplier (x)'
    COL_ROE      = 'ROE (%)'
    COL_YEAR     = 'Năm'

    if COL_YEAR in df_dupont.columns:
        df_dupont = df_dupont.set_index(COL_YEAR)

    fig = go.Figure()
    if COL_MARGIN in df_dupont.columns:
        fig.add_trace(go.Bar(x=df_dupont.index, y=df_dupont[COL_MARGIN],
                             name='Biên LN (%)', marker_color='#a855f7'))
    if COL_TURNOVER in df_dupont.columns:
        fig.add_trace(go.Bar(x=df_dupont.index, y=df_dupont[COL_TURNOVER] * 100,
                             name='Vòng quay TS (×100)', marker_color='#ec4899'))
    if COL_LEVERAGE in df_dupont.columns:
        fig.add_trace(go.Bar(x=df_dupont.index, y=df_dupont[COL_LEVERAGE] * 10,
                             name='Đòn bẩy (×10)', marker_color='#06b6d4'))
    fig.update_layout(barmode='stack', template='plotly_dark',
                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                      margin=dict(t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

    latest_year = df_dupont.index.max()
    r = df_dupont.loc[latest_year]
    d1, d2, d3 = st.columns(3)
    d1.metric(f"Biên LN {latest_year}",
              f"{r[COL_MARGIN]:.1f}%" if COL_MARGIN in r and r[COL_MARGIN] == r[COL_MARGIN] else '—')
    d2.metric("Vòng Quay TS",
              f"{r[COL_TURNOVER]:.2f}x" if COL_TURNOVER in r and r[COL_TURNOVER] == r[COL_TURNOVER] else '—')
    d3.metric("Đòn Bẩy",
              f"{r[COL_LEVERAGE]:.2f}x" if COL_LEVERAGE in r and r[COL_LEVERAGE] == r[COL_LEVERAGE] else '—')


def render_tab_technical(df_price, tech, metrics):
    st.markdown("### 📈 Phân Tích Kỹ Thuật")

    fig_price = go.Figure()
    fig_price.add_trace(go.Scatter(x=df_price['time'], y=df_price['close_vnd'],
                                   name='Giá đóng cửa', line=dict(color='#f0f0ff', width=1.5)))
    if 'MA20' in df_price.columns:
        fig_price.add_trace(go.Scatter(x=df_price['time'], y=df_price['MA20'],
                                       name='MA20', line=dict(color='#a855f7', width=1.5, dash='dot')))
    if 'MA50' in df_price.columns:
        fig_price.add_trace(go.Scatter(x=df_price['time'], y=df_price['MA50'],
                                       name='MA50', line=dict(color='#ec4899', width=1.5, dash='dot')))
    fig_price.update_layout(template='plotly_dark',
                            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                            margin=dict(t=20, b=20), legend=dict(orientation='h', y=1.1))
    st.plotly_chart(fig_price, use_container_width=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Giá Hiện Tại", f"{metrics.get('current_price', 0):,.0f} đ")
    c2.metric("MA20", f"{tech.get('ma20', 0):,.0f} đ" if tech.get('ma20') == tech.get('ma20') else "—")
    c3.metric("MA50", f"{tech.get('ma50', 0):,.0f} đ" if tech.get('ma50') == tech.get('ma50') else "—")
    c4.metric("Xu Hướng", tech.get('trend_signal', 'N/A'))

    st.markdown("---")
    st.markdown("#### RSI (14 phiên) — Chỉ Báo Động Lượng")
    if 'RSI14' in df_price.columns:
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(x=df_price['time'], y=df_price['RSI14'],
                                     name='RSI14', line=dict(color='#06b6d4', width=2)))
        fig_rsi.add_hline(y=70, line_dash='dash', line_color='#ff4d6d', annotation_text='Quá mua (70)')
        fig_rsi.add_hline(y=30, line_dash='dash', line_color='#10d98a', annotation_text='Quá bán (30)')
        fig_rsi.update_layout(template='plotly_dark',
                              paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                              yaxis=dict(range=[0, 100]), margin=dict(t=20, b=20))
        st.plotly_chart(fig_rsi, use_container_width=True)

    r1, r2 = st.columns(2)
    r1.metric("RSI (14)", f"{tech.get('rsi14', 50):.1f}")
    r2.metric("Tín Hiệu RSI", tech.get('rsi_signal', 'N/A'))

    st.markdown("---")
    st.markdown("#### Khối Lượng Giao Dịch 20 Ngày")
    fig_vol = go.Figure()
    fig_vol.add_trace(go.Bar(x=df_price['time'], y=df_price['volume'],
                             name="Khối lượng GD", marker_color='#a855f7', opacity=0.6))
    if 'volume_ma20' in df_price.columns:
        fig_vol.add_trace(go.Scatter(x=df_price['time'], y=df_price['volume_ma20'],
                                     line=dict(color='#ec4899', width=2), name="Volume MA20"))
    fig_vol.update_layout(template='plotly_dark',
                          paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                          margin=dict(t=20, b=20))
    st.plotly_chart(fig_vol, use_container_width=True)

    v1, v2, v3 = st.columns(3)
    v1.metric("KL Phiên Gần Nhất", f"{tech['latest_volume']:,.0f} CP")
    v2.metric("KL TB 20 Ngày",    f"{tech['avg_volume_20d']:,.0f} CP")
    v3.metric("So Với TB",        f"{tech['volume_vs_avg_pct']:+.1f}%",
              delta=f"{tech['volume_vs_avg_pct']:+.1f}%")
    st.caption(f"CP Lưu Hành: **{metrics.get('issue_share_million', 0) or 0:,.1f} Tr CP**")

    if tech.get('oil_correlation', 0.0) != 0.0:
        st.warning(f"🛢️ Tương quan giá dầu: **{tech['oil_correlation']:.2f}** — mã nhạy cảm với biến động dầu thô WTI.")


def render_tab_news(news_cards):
    for item in news_cards:
        st.markdown(f"""
<div style='background:rgba(255,255,255,0.01);padding:16px;border-radius:10px;
margin-bottom:10px;border-left:4px solid #ec4899;'>
<small style='color:#a855f7;'>📰 {item['source']}</small><br>
<strong style='font-size:15px;color:#f1f1f6;'>{item['title']}</strong>
</div>""", unsafe_allow_html=True)


def render_tab_forecast(df_5y_table, fundamentals, metrics, tech, valuation_pkg, period_col='Năm'):
    st.markdown("### 🔮 Dự Phóng 2026 – 2027 (Ngoại Suy Từ CAGR 5 Năm)")

    if df_5y_table.empty or period_col != 'Năm':
        st.info("Chỉ hỗ trợ dự phóng theo Năm.")
        return

    df_years = df_5y_table.dropna(subset=['Năm']).sort_values('Năm')
    if df_years.empty:
        st.warning("Không đủ dữ liệu để dự phóng.")
        return

    last_year   = int(df_years['Năm'].iloc[-1])
    last_revenue = df_years['Doanh thu thuần (tỷ)'].iloc[-1]
    last_profit  = df_years['LNST (tỷ)'].iloc[-1]
    rev_cagr     = fundamentals.get('revenue_cagr_pct')
    np_cagr      = fundamentals.get('net_profit_cagr_pct')

    if last_revenue is None or last_revenue != last_revenue or rev_cagr is None:
        st.warning("⚠️ Thiếu Doanh thu/CAGR để dự phóng.")
        forecast_years = revenue_fc = profit_fc = []
    else:
        g_rev = rev_cagr / 100
        g_np  = (np_cagr / 100) if (np_cagr is not None and np_cagr == np_cagr) else g_rev
        forecast_years = [last_year + 1, last_year + 2]
        revenue_fc = [last_revenue * (1 + g_rev) ** i for i in (1, 2)]
        profit_fc  = [
            (last_profit * (1 + g_np) ** i)
            if (last_profit is not None and last_profit == last_profit) else None
            for i in (1, 2)
        ]

    if forecast_years:
        st.caption(
            f"Ngoại suy cơ học: Doanh thu CAGR 5N ≈ {fmt(rev_cagr, suffix='%')}, "
            f"LNST CAGR 5N ≈ {fmt(np_cagr, suffix='%')}. "
            f"⚠️ KHÔNG phải dự báo từ công ty chứng khoán."
        )
        chart_years   = [str(last_year)] + [str(y) for y in forecast_years]
        chart_revenue = [last_revenue] + revenue_fc
        chart_profit  = [last_profit]  + profit_fc

        fig_fc = go.Figure()
        fig_fc.add_trace(go.Bar(x=chart_years, y=chart_revenue,
                                name='Doanh thu (tỷ) — dự phóng từ năm sau',
                                marker_color=['#a855f7'] + ['#c084fc'] * len(forecast_years)))
        fig_fc.add_trace(go.Scatter(x=chart_years, y=chart_profit,
                                    name='LNST dự phóng (tỷ)',
                                    line=dict(color='#10d98a', width=3), yaxis='y2'))
        fig_fc.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(type='category'),
            yaxis=dict(title='Doanh thu (tỷ)'),
            yaxis2=dict(title='LNST (tỷ)', overlaying='y', side='right'),
            legend=dict(orientation='h', y=1.1), margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_fc, use_container_width=True)

        cols = st.columns(len(forecast_years))
        for col, y, rev, np_ in zip(cols, forecast_years, revenue_fc, profit_fc):
            col.metric(f"Doanh thu {y}", fmt(rev, suffix=" tỷ", decimals=0))
            col.metric(f"LNST {y}",      fmt(np_, suffix=" tỷ", decimals=0))

    st.markdown("---")
    st.markdown("### Đánh Giá Tổng Hợp")

    def _score_to_dots(score, max_dots=5):
        score = max(0, min(max_dots, round(score)))
        filled = "●" * score
        empty  = "○" * (max_dots - score)
        color = "#ec4899" if score >= 4 else ("#fbbf24" if score >= 2 else "#8b8ba7")
        return (f"<span style='color:{color};letter-spacing:3px;font-size:1.1rem;'>{filled}</span>"
                f"<span style='color:#3a3a52;letter-spacing:3px;font-size:1.1rem;'>{empty}</span>")

    roe_latest = fundamentals.get('roe_latest') or 0
    score_financial  = 5 if roe_latest >= 20 else 4 if roe_latest >= 15 else 3 if roe_latest >= 10 else 2 if roe_latest >= 5 else 1
    pe_now           = metrics.get('pe', 0) or 0
    score_valuation  = 5 if 0 < pe_now < 8 else 4 if pe_now < 12 else 3 if pe_now < 18 else 2 if pe_now < 25 else 1
    score_competitive = 4
    growth_ref       = rev_cagr if (rev_cagr is not None and rev_cagr == rev_cagr) else 0
    score_outlook    = 5 if growth_ref >= 20 else 4 if growth_ref >= 10 else 3 if growth_ref >= 0 else 2 if growth_ref >= -10 else 1
    trend            = str(tech.get('trend_signal', '')) if tech else ''
    score_catalyst   = 4 if ('tăng' in trend.lower() or 'up' in trend.lower()) else 3

    for label, score in [
        ("Tài chính (5 năm)",      score_financial),
        (f"Định giá {last_year}",  score_valuation),
        ("Vị thế cạnh tranh",      score_competitive),
        ("Triển vọng tăng trưởng", score_outlook),
        ("Catalyst / Xu hướng giá", score_catalyst),
    ]:
        c1, c2 = st.columns([2, 1])
        c1.markdown(f"<div style='padding-top:0.3rem;'>{label}</div>", unsafe_allow_html=True)
        c2.markdown(_score_to_dots(score), unsafe_allow_html=True)

    st.caption("ℹ️ Điểm đánh giá suy ra tự động từ ROE, P/E, CAGR doanh thu và xu hướng giá.")

    st.markdown("---")
    summary = valuation_pkg.get('summary') if valuation_pkg else None
    if summary:
        rec           = summary.get('recommendation', '')
        target_min    = summary.get('target_price_min')
        target_max    = summary.get('target_price_max')
        upside_median = summary.get('upside_pct')

        if rec in ('MUA MẠNH', 'MUA'):
            rec_text, rec_color = "↑ ACCUMULATE · TÍCH LŨY", "#22c55e"
        elif rec == 'BÁN':
            rec_text, rec_color = "↓ REDUCE · GIẢM TỈ TRỌNG", "#ef4444"
        else:
            rec_text, rec_color = "→ HOLD · NẮM GIỮ", "#fbbf24"

        target_str  = f"₫{target_min:,.0f} – {target_max:,.0f}" if (target_min is not None and target_max is not None) else "—"
        upside_str  = f"({upside_median:+.0f}%)" if upside_median is not None else ""

        st.markdown(f"""
<div style="padding:1.2rem 1.4rem;border-radius:16px;
background:linear-gradient(135deg, rgba(168,85,247,0.12), rgba(236,72,153,0.08));
border:1px solid rgba(168,85,247,0.25);">
<div style="opacity:0.7;font-size:0.85rem;letter-spacing:1px;">KHUYẾN NGHỊ (9 PP HỘI TỤ)</div>
<div style="font-size:1.6rem;font-weight:800;color:{rec_color};margin:0.3rem 0;">{rec_text}</div>
<div style="opacity:0.85;">Dải mục tiêu: <strong>{target_str}</strong> {upside_str}</div>
</div>""", unsafe_allow_html=True)
        st.caption("ℹ️ Tổng hợp từ PE/PB Median 5N, DCF, Graham. Không phải lời khuyên đầu tư.")
    else:
        st.info("Chưa đủ dữ liệu để tổng hợp khuyến nghị 9 phương pháp cho mã này.")


# ─────────────────────────────────────────────────────────────────────────────
# ALIAS: app.py gọi render_tab_volume, nội dung giống render_tab_technical
# ─────────────────────────────────────────────────────────────────────────────
render_tab_volume = render_tab_technical


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3: MULTIPLES MỞ RỘNG — Card UI đẹp theo ảnh tham khảo
# ─────────────────────────────────────────────────────────────────────────────

_MULTIPLES_CSS = """
<style>
.mult-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin: 18px 0 24px;
}
@media (max-width: 640px) {
    .mult-grid { grid-template-columns: repeat(2, 1fr); }
}
.mult-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
    padding: 20px 16px 16px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    transition: border 0.2s;
}
.mult-card:hover { border-color: rgba(168,85,247,0.45); }
.mult-card-label {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.6px;
    color: #8b8ba7;
    text-transform: uppercase;
}
.mult-card-year {
    font-size: 11px;
    color: #555571;
    margin-top: -4px;
}
.mult-card-value {
    font-size: 32px;
    font-weight: 800;
    font-family: 'Courier New', monospace;
    color: #22c55e;
    line-height: 1.1;
    margin: 4px 0 2px;
}
.mult-card-value.neutral { color: #c084fc; }
.mult-card-value.warn    { color: #f59e0b; }
.mult-card-value.danger  { color: #f43f5e; }
.mult-card-value.na      { color: #555571; font-size: 18px; }
.mult-card-sub {
    font-size: 12px;
    color: #8b8ba7;
    line-height: 1.4;
}
.mult-card-sub strong { color: #c084fc; }
.mult-section-title {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: #8b8ba7;
    text-transform: uppercase;
    margin: 28px 0 4px;
    display: flex;
    align-items: center;
    gap: 10px;
}
.mult-section-title::before {
    content: '';
    display: inline-block;
    width: 28px;
    height: 28px;
    border-radius: 8px;
    background: linear-gradient(135deg, #a855f7, #ec4899);
    font-size: 14px;
    text-align: center;
    line-height: 28px;
    color: white;
    font-weight: 900;
}
.mult-note {
    background: linear-gradient(135deg, rgba(168,85,247,0.10), rgba(236,72,153,0.06));
    border: 1px solid rgba(168,85,247,0.28);
    border-radius: 12px;
    padding: 13px 16px;
    color: #c4b0ff;
    font-size: 13px;
    line-height: 1.6;
    margin: 8px 0 20px;
}
.mult-note strong { color: #e9d5ff; }
.mult-badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 2px 8px;
    border-radius: 20px;
    margin-left: 6px;
    vertical-align: middle;
}
.badge-cheap   { background: rgba(34,197,94,0.15);  color: #22c55e; }
.badge-fair    { background: rgba(168,85,247,0.15); color: #c084fc; }
.badge-dear    { background: rgba(245,158,11,0.15); color: #f59e0b; }
.badge-pricey  { background: rgba(244,63,94,0.15);  color: #f43f5e; }
.badge-na      { background: rgba(255,255,255,0.05); color: #555571; }
</style>
"""

def _mult_card(label, year, value_str, color_cls, sub_html, badge_html=""):
    return f"""
<div class="mult-card">
  <div class="mult-card-label">{label}{badge_html}</div>
  <div class="mult-card-year">{year}</div>
  <div class="mult-card-value {color_cls}">{value_str}</div>
  <div class="mult-card-sub">{sub_html}</div>
</div>"""


def _pe_color(pe):
    if pe <= 0:   return "na"
    if pe < 10:   return ""
    if pe < 15:   return "neutral"
    if pe < 20:   return "warn"
    return "danger"

def _pb_color(pb):
    if pb <= 0:   return "na"
    if pb < 1.0:  return ""
    if pb < 1.5:  return "neutral"
    if pb < 2.5:  return "warn"
    return "danger"

def _pe_sub(pe, pe_median_5y):
    if pe <= 0: return "Không có dữ liệu"
    m = f"{pe_median_5y:.1f}x" if pe_median_5y else "—"
    if pe_median_5y and pe < pe_median_5y * 0.85:
        return f"<strong>Dưới TB 5N</strong> ({m})"
    if pe_median_5y and pe > pe_median_5y * 1.15:
        return f"Trên TB 5N ({m})"
    return f"Quanh TB 5N ({m})"

def _pb_sub(pb, pb_median_5y):
    if pb <= 0: return "Không có dữ liệu"
    m = f"{pb_median_5y:.2f}x" if pb_median_5y else "—"
    if pb_median_5y and pb < pb_median_5y * 0.85:
        return f"<strong>Dưới TB 5N</strong> ({m})"
    if pb_median_5y and pb > pb_median_5y * 1.15:
        return f"Trên TB 5N ({m})"
    return f"Quanh TB 5N ({m})"

def _badge(pe, pe_med):
    if not pe_med or pe <= 0: return ""
    ratio = pe / pe_med
    if ratio < 0.85:   return '<span class="mult-badge badge-cheap">Rẻ</span>'
    if ratio < 1.0:    return '<span class="mult-badge badge-cheap">Hơi rẻ</span>'
    if ratio < 1.15:   return '<span class="mult-badge badge-fair">Hợp lý</span>'
    if ratio < 1.40:   return '<span class="mult-badge badge-dear">Hơi đắt</span>'
    return '<span class="mult-badge badge-pricey">Đắt</span>'

def render_tab_multiples(metrics, fundamentals, valuation_pkg):
    st.markdown(_MULTIPLES_CSS, unsafe_allow_html=True)

    is_bank       = metrics.get("is_bank", False)
    current_year  = __import__('datetime').datetime.today().year
    latest_year   = current_year - 1

    pe   = metrics.get("pe",  0.0) or 0.0
    pb   = metrics.get("pb",  0.0) or 0.0
    eps  = fundamentals.get("eps_latest",  0.0) or 0.0
    bvps = fundamentals.get("bvps_latest", 0.0) or 0.0

    pe_series = valuation_pkg.get("pe_series")
    pb_series = valuation_pkg.get("pb_series")

    def _median5(s):
        if s is None or s.empty: return None
        v = pd.to_numeric(s, errors='coerce').dropna()
        v = v[(v > 0) & (v < 200)]
        return float(v.median()) if not v.empty else None

    pe_med = _median5(pe_series)
    pb_med = _median5(pb_series)

    mktcap_b   = metrics.get("market_cap_billion", 0.0) or 0.0

    # ── Doanh thu (tỷ) — ưu tiên metrics, fallback fundamentals ──────────
    rev_b = (
        metrics.get("revenue_latest_billion")
        or metrics.get("revenue_billion")
        or fundamentals.get("revenue_latest_billion")
        or fundamentals.get("revenue_latest")   # một số backend để key này
        or 0.0
    )
    rev_b = float(rev_b) if rev_b else 0.0

    # ── Net Profit (tỷ) ───────────────────────────────────────────────────
    np_b = (
        metrics.get("net_profit_latest_billion")
        or metrics.get("net_profit_billion")
        or fundamentals.get("net_profit_latest_billion")
        or fundamentals.get("net_profit_latest")
        or 0.0
    )
    np_b = float(np_b) if np_b else 0.0

    # ── Equity / Total Assets (tỷ) ───────────────────────────────────────
    equity_b = (
        metrics.get("equity_billion")
        or fundamentals.get("equity_latest_billion")
        or fundamentals.get("equity_latest")
        or 0.0
    )
    equity_b = float(equity_b) if equity_b else 0.0

    total_assets_b = (
        metrics.get("total_assets_billion")
        or fundamentals.get("total_assets_latest_billion")
        or fundamentals.get("total_assets_latest")
        or 0.0
    )
    total_assets_b = float(total_assets_b) if total_assets_b else 0.0

    # ── Nợ ròng: Total Debt - Cash (tỷ) ─────────────────────────────────
    # Ưu tiên key có sẵn, fallback ước tính = Total Assets - Equity - mktcap
    net_debt_b = (
        metrics.get("net_debt_billion")
        or metrics.get("net_debt")
        or 0.0
    )
    net_debt_b = float(net_debt_b) if net_debt_b else 0.0
    # Nếu vẫn 0, ước tính thô: Tổng TS - VCSH - Vốn hóa (proxy)
    if net_debt_b == 0.0 and total_assets_b > 0 and equity_b > 0:
        net_debt_b = max(0.0, total_assets_b - equity_b - mktcap_b * 0.3)

    # ── EBITDA (tỷ): thử key trực tiếp, fallback tính từ net_profit ──────
    # Theo valuation_formulas.md: EBITDA = EBIT + D&A = LT trước thuế + lãi vay + khấu hao
    # Proxy khả dụng nhất: EBITDA ≈ net_profit / (1 - tax_rate) * margin_adj
    # Thực tế đơn giản: EBITDA ≈ net_profit * 1.3~1.5 (ngành chứng khoán/tài chính ~1.1)
    ebitda_b = (
        metrics.get("ebitda_latest_billion")
        or metrics.get("ebitda_billion")
        or fundamentals.get("ebitda_latest_billion")
        or fundamentals.get("ebitda_latest")
        or 0.0
    )
    ebitda_b = float(ebitda_b) if ebitda_b else 0.0
    # Fallback: ước tính EBITDA từ net_profit nếu có
    _ebitda_estimated = False
    if ebitda_b == 0.0 and np_b > 0:
        # Chứng khoán/tài chính: EBITDA ≈ net_profit * 1.15
        # Sản xuất/thép: EBITDA ≈ net_profit * 1.6
        # Mặc định 1.3 cho các ngành khác
        is_securities = metrics.get("is_securities", False)
        ebitda_mult = 1.15 if is_securities else 1.3
        ebitda_b = round(np_b * ebitda_mult, 2)
        _ebitda_estimated = True

    # ── CFO (tỷ): thử key trực tiếp, fallback dùng net_profit làm proxy ─
    # Theo valuation_formulas.md: P/CF = marketCap / cfo_tỷ
    cfo_b = (
        metrics.get("cfo_latest_billion")
        or metrics.get("cfo_billion")
        or fundamentals.get("cfo_latest_billion")
        or fundamentals.get("cfo_latest")
        or 0.0
    )
    cfo_b = float(cfo_b) if cfo_b else 0.0
    # Fallback: CFO ≈ net_profit (proxy phổ biến khi thiếu cash flow statement)
    _cfo_estimated = False
    if cfo_b == 0.0 and np_b > 0:
        cfo_b = np_b
        _cfo_estimated = True

    # ── EV = Market Cap + Net Debt ────────────────────────────────────────
    ev_b = mktcap_b + net_debt_b

    # ── Tính ratio cuối cùng ─────────────────────────────────────────────
    ev_ebitda_x = (ev_b / ebitda_b)   if (ebitda_b > 0 and ev_b > 0)    else None
    pcf_x       = (mktcap_b / cfo_b)  if (cfo_b > 0 and mktcap_b > 0)   else None
    ps_x        = (mktcap_b / rev_b)  if (rev_b > 0 and mktcap_b > 0)   else None

    # ── Section 01: Core multiples ────────────────────────────────────────
    st.markdown(
        '<div class="mult-section-title" style="--n:\'01\';">01 &nbsp; Định giá cốt lõi · P/E · P/B · EPS · BVPS</div>',
        unsafe_allow_html=True
    )

    pe_badge  = _badge(pe, pe_med)
    pb_badge  = _badge(pb, pb_med)
    yr = str(latest_year)

    pe_val_str  = f"{pe:.2f}x"   if pe   > 0 else "N/A"
    pb_val_str  = f"{pb:.2f}x"   if pb   > 0 else "N/A"
    eps_val_str = f"{eps:,.0f}đ"  if eps  > 0 else "N/A"
    bvp_val_str = f"{bvps:,.0f}đ" if bvps > 0 else "N/A"

    cards_01 = (
        _mult_card("P/E",  yr, pe_val_str,  _pe_color(pe),  _pe_sub(pe, pe_med),  pe_badge)
      + _mult_card("P/B",  yr, pb_val_str,  _pb_color(pb),  _pb_sub(pb, pb_med),  pb_badge)
      + _mult_card("EPS",  yr, eps_val_str, "neutral" if eps  > 0 else "na",
                   "Thu nhập / cổ phiếu"    if eps  > 0 else "Không có dữ liệu")
      + _mult_card("BVPS", yr, bvp_val_str, "neutral" if bvps > 0 else "na",
                   "Giá trị sổ sách / CP"   if bvps > 0 else "Không có dữ liệu")
    )
    st.markdown(f'<div class="mult-grid">{cards_01}</div>', unsafe_allow_html=True)

    # ── Section 02: Extended multiples ────────────────────────────────────
    st.markdown(
        '<div class="mult-section-title">02 &nbsp; Multiples mở rộng · EV/EBITDA · P/CF · P/S</div>',
        unsafe_allow_html=True
    )

    if is_bank:
        st.markdown("""
<div class="mult-note">
ℹ️ <strong>P/S và EV/EBITDA không áp dụng cho ngân hàng</strong> —
khái niệm 'Doanh thu' và 'EBITDA' không phản ánh đúng bản chất kinh doanh
(thu nhập lãi thuần, chi phí dự phòng rủi ro tín dụng có cấu trúc riêng).
Với ngân hàng nên dùng <strong>P/B + ROE, NIM, NPL, CAR</strong> thay thế.
</div>""", unsafe_allow_html=True)

        cards_bank = (
            _mult_card("P/B", yr, pb_val_str, _pb_color(pb),
                       "Định giá chính cho ngân hàng", pb_badge)
          + _mult_card("EPS", yr, eps_val_str, "neutral" if eps > 0 else "na", "—")
          + _mult_card("P/CF", yr, "N/A", "na", "Không áp dụng ngân hàng")
          + _mult_card("P/S · EV/EBITDA", yr, "N/A", "na", "Không áp dụng ngân hàng")
        )
        st.markdown(f'<div class="mult-grid">{cards_bank}</div>', unsafe_allow_html=True)

    else:
        def _pcf_sub(pcf):
            if pcf is None: return "Thiếu dữ liệu"
            note = " <span style='opacity:0.6;font-size:10px;'>(~LNST)</span>" if _cfo_estimated else ""
            if pcf < 8:   return f"<strong>Dòng tiền hấp dẫn</strong>{note}"
            if pcf < 15:  return f"Dòng tiền hợp lý{note}"
            return f"Dòng tiền đắt{note}"

        def _evebitda_sub(ev_eb):
            if ev_eb is None: return "Thiếu dữ liệu"
            note = " <span style='opacity:0.6;font-size:10px;'>(~ước tính)</span>" if _ebitda_estimated else ""
            if ev_eb < 8:   return f"<strong>Định giá rẻ</strong>{note}"
            if ev_eb < 12:  return f"Định giá hợp lý{note}"
            return f"Định giá cao{note}"

        def _ps_sub(ps):
            if ps is None: return "Thiếu dữ liệu Doanh thu"
            if ps < 1:    return "<strong>Cạnh tranh tốt</strong>"
            if ps < 2:    return "Cạnh tranh hợp lý"
            return "Premium định giá"

        ev_str  = f"{ev_ebitda_x:.2f}x" if ev_ebitda_x is not None else "N/A"
        pcf_str = f"{pcf_x:.2f}x"       if pcf_x       is not None else "N/A"
        ps_str  = f"{ps_x:.2f}x"        if ps_x        is not None else "N/A"

        def _val_color(v, thresholds):
            if v is None: return "na"
            lo, hi = thresholds
            if v < lo:  return ""
            if v < hi:  return "neutral"
            return "warn"

        # Card thứ 4: Vốn hóa / VCSH — dùng equity_b đã tính ở trên
        price_to_equity_x = (mktcap_b / equity_b) if equity_b > 0 and mktcap_b > 0 else None
        pa_str   = f"{price_to_equity_x:.2f}x" if price_to_equity_x is not None else "—"
        pa_color = _val_color(price_to_equity_x, (1.0, 2.0)) if price_to_equity_x is not None else "na"
        pa_sub   = (
            "<strong>Dưới book value</strong>" if price_to_equity_x and price_to_equity_x < 1.0
            else "Hợp lý" if price_to_equity_x and price_to_equity_x < 2.0
            else "Premium cao" if price_to_equity_x
            else "Thiếu dữ liệu Vốn CSH"
        )

        cards_ext = (
            _mult_card("EV/EBITDA", yr, ev_str,
                       _val_color(ev_ebitda_x, (8, 12)), _evebitda_sub(ev_ebitda_x))
          + _mult_card("P/CF", yr, pcf_str,
                       _val_color(pcf_x, (8, 15)), _pcf_sub(pcf_x))
          + _mult_card("P/S", yr, ps_str,
                       _val_color(ps_x, (1, 2)), _ps_sub(ps_x))
          + _mult_card("Vốn hóa / VCSH", yr, pa_str, pa_color, pa_sub)
        )
        st.markdown(f'<div class="mult-grid">{cards_ext}</div>', unsafe_allow_html=True)

        # Note giải thích khi dùng ước tính
        notes = []
        if _ebitda_estimated: notes.append("EV/EBITDA ước tính từ LNST (chưa có D&A từ nguồn API)")
        if _cfo_estimated:    notes.append("P/CF dùng LNST làm proxy CFO")
        if ev_ebitda_x is None or pcf_x is None:
            notes.append("Một số chỉ số cần thêm dữ liệu từ TCBS/CafeF")
        if notes:
            st.markdown(f"""
<div class="mult-note">
ℹ️ {' · '.join(notes)}.
</div>""", unsafe_allow_html=True)

    # ── Section 03: So sánh trực quan PE/PB với TB 5 năm ────────────────
    if pe_med or pb_med:
        st.markdown(
            '<div class="mult-section-title">03 &nbsp; So sánh với trung bình 5 năm</div>',
            unsafe_allow_html=True
        )

        cats, cur_vals, med_vals = [], [], []
        if pe > 0 and pe_med:
            cats.append("P/E"); cur_vals.append(pe); med_vals.append(pe_med)
        if pb > 0 and pb_med:
            cats.append("P/B"); cur_vals.append(pb); med_vals.append(pb_med)

        if cats:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=cats, y=med_vals, name="TB 5 năm",
                marker_color="rgba(168,85,247,0.35)",
                marker_line_color="rgba(168,85,247,0.8)",
                marker_line_width=1.5,
            ))
            fig.add_trace(go.Bar(
                x=cats, y=cur_vals, name="Hiện tại",
                marker_color="rgba(34,197,94,0.8)",
            ))
            fig.update_layout(
                barmode="group", template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                height=260,
                margin=dict(t=10, b=10, l=20, r=20),
                legend=dict(orientation="h", y=1.1),
                font=dict(color="#c4b0ff"),
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
            )
            st.plotly_chart(fig, use_container_width=True)
