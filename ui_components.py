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
    kpi1.metric("Thị Giá Hiện Tại", f"{metrics['current_price']:,.0f} đ")
    kpi2.metric("Vốn Hóa", format_market_cap_billion(metrics['market_cap_billion']))
    kpi3.metric("P/E (TTM)", f"{metrics['pe']:.2f} x")
    kpi4.metric("P/B (TTM)", f"{metrics['pb']:.2f} x")
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
        """
        CAGR compound giữa điểm đầu và cuối có giá trị hợp lệ.
        Trả về None nếu < 2 điểm dữ liệu hoặc giá trị âm (không có nghĩa).
        """
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
        """
        Thay đổi điểm phần trăm tuyệt đối (pp) giữa kỳ đầu và kỳ cuối.
        Dùng cho ROE/ROA vì CAGR compound trên tỷ lệ % gây hiểu nhầm.
        VD: ROE 29.66% → 26.64% = -3.02 pp (không phải -2.65% CAGR).
        """
        valid = [v for v in series_vals if pd.notnull(v) and v != 0]
        if len(valid) < 2:
            return None
        return valid[-1] - valid[0]  # Δpp = cuối - đầu

    n_per_year = 4 if period_col == 'Quý' else 1

    def _is_pct_ratio(col_name: str) -> bool:
        """ROE/ROA là tỷ lệ % — dùng Δpp thay CAGR compound."""
        lower = col_name.lower()
        return ('roe' in lower or 'roa' in lower)

    def _sparkline(series_vals):
        """Mini-bar Unicode thể hiện xu hướng."""
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

        # Giá trị từng kỳ
        for p, v in zip(periods, vals):
            row[p] = "—" if pd.isnull(v) else "{:,.2f}".format(float(v))

        # ── CAGR / Δpp ──────────────────────────────────────────────
        # FIX CHÍNH: trước đây toàn bộ cột có "%" → hardcode "—"
        # Giờ:
        #   ROE/ROA → Δpp (thay đổi điểm phần trăm tuyệt đối, suffix " pp")
        #   Còn lại → CAGR compound bình thường (suffix "%")
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


def render_tab_valuation(valuation_pkg, metrics):
    """Tab Định Giá PE/PB · 9PP — hiển thị đầy đủ theo ngành."""
    current_price = metrics.get('current_price', 0) or 0
    summary = valuation_pkg.get('summary')
    methods = valuation_pkg.get('methods', {})
    sector = valuation_pkg.get('sector_detected', 'default')
    is_bank = metrics.get('is_bank', False)

    SECTOR_LABELS = {
        'bank': '🏦 Ngân hàng', 'steel': '⚙️ Thép / Công nghiệp nặng',
        'real_estate': '🏢 Bất động sản', 'retail': '🛍️ Bán lẻ / Tiêu dùng',
        'tech': '💻 Công nghệ / Viễn thông', 'oil_gas': '🛢️ Dầu khí / Hoá chất',
        'aviation': '✈️ Hàng không / Vận tải', 'default': '📊 Chung',
    }
    SECTOR_NOTES = {
        'bank': 'Ưu tiên **P/B + ROE** (không dùng PE/P/S). P/B < 1.5x = vùng mua, > 3.0x = vùng bán.',
        'steel': 'Chu kỳ → **P/B + EV/EBITDA**. PE median có thể lệch do đáy chu kỳ. P/B < 1.3x = mua.',
        'real_estate': 'Ưu tiên **P/B + NAV**. PE không có ý nghĩa do doanh thu không đều.',
        'retail': '**PE + PEG** là chính. PEG < 1.0 = rẻ. PE hợp lý: 15–25x.',
        'tech': '**PE + PEG + Revenue Growth**. PE 20–40x hợp lý nếu tăng trưởng cao.',
        'oil_gas': '**EV/EBITDA + P/CF** là chính. EV/EBITDA < 5x = hợp lý.',
        'default': 'Dùng median 5N của chính mã để định giá (PE/PB/EV-EBITDA/DCF/Graham).',
    }

    st.markdown(f"### 💰 Định Giá · 9 Phương Pháp Hội Tụ")
    st.caption(
        f"📌 **{SECTOR_LABELS.get(sector, sector)}** — "
        + SECTOR_NOTES.get(sector, SECTOR_NOTES['default'])
    )

    if not summary:
        st.warning("Không đủ dữ liệu để chạy các phương pháp định giá.")
        return

    # ── Verdict header ───────────────────────────────────────────────────────
    upside = summary.get('upside_median_pct', 0) or 0
    verdict = summary.get('verdict', '')
    if 'UNDERVALUED' in verdict:
        vcolor, vbg = '#22c55e', 'rgba(34,197,94,0.12)'
    elif 'OVERVALUED' in verdict:
        vcolor, vbg = '#f43f5e', 'rgba(244,63,94,0.12)'
    else:
        vcolor, vbg = '#fbbf24', 'rgba(251,191,36,0.10)'

    p25 = summary.get('p25', 0); p75 = summary.get('p75', 0)
    median_val = summary.get('median', 0)
    st.markdown(f"""
<div style="border-radius:16px;padding:18px 22px;background:{vbg};border:1px solid {vcolor}33;margin-bottom:12px;">
  <div style="font-size:0.8rem;opacity:0.7;letter-spacing:1px;text-transform:uppercase;">Kết Luận · {len(methods)} Phương Pháp</div>
  <div style="font-size:1.8rem;font-weight:800;color:{vcolor};">{verdict}</div>
  <div style="display:flex;gap:24px;margin-top:8px;font-size:0.9rem;">
    <span>Giá HT: <b>₫{current_price:,.0f}</b></span>
    <span>Median: <b>₫{median_val:,.0f}</b></span>
    <span>Upside: <b style="color:{vcolor};">{upside:+.1f}%</b></span>
    <span>Dải P25-P75: <b>₫{p25:,.0f} – ₫{p75:,.0f}</b></span>
  </div>
</div>""", unsafe_allow_html=True)

    if not methods:
        st.warning("Chưa có dữ liệu phương pháp định giá.")
        return

    # ── Nhóm phương pháp ────────────────────────────────────────────────────
    GROUP_ORDER = [
        ('📐 Multiples (Median lịch sử)', ['PE Median 5N', 'PE TB 5N', 'PB Median 5N', 'PB TB 5N', 'PB Sàn 5N (min)']),
        ('📊 Multiples Mở Rộng', ['EV/EBITDA Median 5N', 'P/CF Median 5N', 'P/S Median 5N']),
        ('🔬 Nội Tại (Intrinsic)', ['DCF (Bi quan)', 'DCF (Cơ sở)', 'DCF (Tích cực)', 'Graham Number', 'DDM (Gordon)']),
    ]

    for group_title, group_keys in GROUP_ORDER:
        group_methods = {k: methods[k] for k in group_keys if k in methods}
        if not group_methods:
            continue
        st.markdown(f"#### {group_title}")
        cols = st.columns(min(len(group_methods), 4))
        for i, (name, val) in enumerate(group_methods.items()):
            pct = (val / current_price - 1) * 100 if current_price else 0
            arrow = "↑" if pct >= 0 else "↓"
            cols[i % len(cols)].metric(
                name,
                f"₫{val:,.0f}",
                delta=f"{arrow} {abs(pct):.1f}%",
            )

    # ── Bar chart hội tụ ─────────────────────────────────────────────────────
    st.markdown("#### 📈 Biểu Đồ Hội Tụ 9 Phương Pháp")
    names = list(methods.keys())
    values = list(methods.values())
    colors = ['#22c55e' if v >= current_price else '#f43f5e' for v in values]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=names, y=values, marker_color=colors, text=[f"₫{v:,.0f}" for v in values],
                         textposition='outside', textfont=dict(size=10)))
    fig.add_hline(y=current_price, line_dash='dash', line_color='#fbbf24', line_width=2,
                  annotation_text=f"Giá HT ₫{current_price:,.0f}", annotation_position="top left")
    if p25 and p75:
        fig.add_hrect(y0=p25, y1=p75, fillcolor='rgba(168,85,247,0.08)',
                      line_color='rgba(168,85,247,0.3)', line_width=1,
                      annotation_text="Dải P25–P75", annotation_position="top right")
    fig.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='rgba(0,0,0,0)', margin=dict(t=40, b=20),
                      yaxis=dict(title='Giá ước tính (đ)'),
                      xaxis=dict(tickangle=-30))
    st.plotly_chart(fig, use_container_width=True)

    # ── Lịch sử P/E & P/B 5 năm ─────────────────────────────────────────────
    pe_s = valuation_pkg.get('pe_series')
    pb_s = valuation_pkg.get('pb_series')
    if pe_s is not None and not pe_s.dropna().empty:
        st.markdown("#### 📉 Lịch Sử P/E & P/B 5 Năm")
        pe_clean = pe_s.dropna()
        pb_clean = pb_s.dropna() if pb_s is not None else pd.Series(dtype=float)

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=pe_clean.index.astype(str), y=pe_clean.values,
                                   name='P/E', line=dict(color='#a855f7', width=2),
                                   mode='lines+markers'))
        if not pb_clean.empty:
            fig2.add_trace(go.Scatter(x=pb_clean.index.astype(str), y=pb_clean.values,
                                       name='P/B', line=dict(color='#10d98a', width=2),
                                       mode='lines+markers', yaxis='y2'))
        # Vạch median
        if not pe_clean.empty:
            fig2.add_hline(y=float(pe_clean.median()), line_dash='dot',
                           line_color='rgba(168,85,247,0.5)',
                           annotation_text=f"PE Median {pe_clean.median():.1f}x")
        fig2.update_layout(
            template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            yaxis=dict(title='P/E (x)', side='left'),
            yaxis2=dict(title='P/B (x)', overlaying='y', side='right'),
            legend=dict(orientation='h', y=1.1),
            margin=dict(t=30, b=20),
        )
        st.plotly_chart(fig2, use_container_width=True)

        # ── Bảng thống kê chi tiết P/E & P/B ───────────────────────────────
        pe_now = metrics.get('pe', 0) or 0
        pb_now = metrics.get('pb', 0) or 0

        st.markdown("#### 📊 Thông Số P/E & P/B Chi Tiết")
        pe_cols = st.columns(5)
        pb_cols = st.columns(5)

        def _cmp_color(now, ref):
            if not ref or ref == 0:
                return "#e5e7eb"
            return "#22c55e" if now < ref else "#f43f5e"

        if not pe_clean.empty:
            pe_min = float(pe_clean.min())
            pe_max = float(pe_clean.max())
            pe_med = float(pe_clean.median())
            pe_avg = float(pe_clean.mean())
            for col, label, val in zip(pe_cols,
                ["PE Hiện Tại", "PE Median 5N", "PE TB 5N", "PE Min 5N", "PE Max 5N"],
                [pe_now, pe_med, pe_avg, pe_min, pe_max]):
                c = _cmp_color(pe_now, val) if label != "PE Hiện Tại" else "#f0f0ff"
                col.markdown(
                    f"<div style='text-align:center'>"
                    f"<div style='font-size:.75rem;opacity:.7'>{label}</div>"
                    f"<div style='font-size:1.5rem;font-weight:700;color:{c}'>{val:.1f}x</div>"
                    f"</div>", unsafe_allow_html=True)

        if not pb_clean.empty:
            pb_min = float(pb_clean.min())
            pb_max = float(pb_clean.max())
            pb_med = float(pb_clean.median())
            pb_avg = float(pb_clean.mean())
            for col, label, val in zip(pb_cols,
                ["PB Hiện Tại", "PB Median 5N", "PB TB 5N", "PB Sàn 5N", "PB Đỉnh 5N"],
                [pb_now, pb_med, pb_avg, pb_min, pb_max]):
                c = _cmp_color(pb_now, val) if label != "PB Hiện Tại" else "#f0f0ff"
                col.markdown(
                    f"<div style='text-align:center'>"
                    f"<div style='font-size:.75rem;opacity:.7'>{label}</div>"
                    f"<div style='font-size:1.5rem;font-weight:700;color:{c}'>{val:.2f}x</div>"
                    f"</div>", unsafe_allow_html=True)

        # ── 5 kịch bản định giá PE/PB (card style như ảnh tham chiếu) ───────
        bvps_latest = metrics.get('bvps_latest') or 0
        eps_latest  = metrics.get('eps_latest') or 0
        st.markdown("---")
        st.markdown("#### 🃏 5 Kịch Bản Định Giá PE · PB")
        st.caption("Hệ số PE/PB tham chiếu × EPS & BVPS năm gần nhất")

        scenarios = []
        if bvps_latest > 0 and not pb_clean.empty:
            scenarios.append(("PB 1.0x (Sàn)", bvps_latest * 1.0, "BVPS (book value)"))
            scenarios.append(("PB Median 5N", bvps_latest * pb_med,
                               f"BVPS × PB {pb_med:.2f}x"))
            scenarios.append(("PB TB 5N", bvps_latest * pb_avg,
                               f"BVPS × PB {pb_avg:.2f}x", True))
        if eps_latest > 0 and not pe_clean.empty:
            scenarios.append(("PE Median 5N", eps_latest * pe_med,
                               f"EPS × PE {pe_med:.1f}x", True))
            scenarios.append(("PE TB 5N", eps_latest * pe_avg,
                               f"EPS × PE {pe_avg:.1f}x"))

        if scenarios:
            max_val = max(s[1] for s in scenarios)
            card_cols = st.columns(len(scenarios))
            for col, s in zip(card_cols, scenarios):
                name, val, note = s[0], s[1], s[2]
                is_rec = len(s) > 3 and s[3]
                pct = (val / current_price - 1) * 100 if current_price else 0
                bar_w = int(val / max_val * 100)
                bar_color = "#22c55e" if pct >= 0 else "#f43f5e"
                val_color = "#22c55e" if pct >= 0 else "#f43f5e"
                badge = "<div style='background:linear-gradient(90deg,#a855f7,#ec4899);color:white;font-size:9px;padding:2px 7px;border-radius:20px;display:inline-block;margin-bottom:4px;'>KHUYẾN NGHỊ</div>" if is_rec else ""
                col.markdown(f"""
<div style="border-radius:14px;padding:14px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);height:100%">
  {badge}
  <div style="font-size:0.72rem;opacity:0.6;text-transform:uppercase;letter-spacing:.5px;">{name}</div>
  <div style="font-size:1.6rem;font-weight:800;color:{val_color};font-family:'Courier New',monospace;">
    {val/1000:.1f}<span style="font-size:1rem;">K</span>
  </div>
  <div style="font-size:0.8rem;font-weight:700;color:{val_color};">{pct:+.0f}%</div>
  <div style="height:4px;border-radius:2px;background:rgba(255,255,255,0.08);margin:8px 0;">
    <div style="width:{bar_w}%;height:4px;border-radius:2px;background:{bar_color};"></div>
  </div>
  <div style="font-size:0.7rem;opacity:0.55;">{note}</div>
</div>""", unsafe_allow_html=True)

        # ── Giá vs BVPS chart ────────────────────────────────────────────────
        price_series = valuation_pkg.get('price_series')
        bvps_series  = valuation_pkg.get('bvps_series')
        if price_series is not None and bvps_series is not None:
            st.markdown("---")
            st.markdown("#### 📈 Giá vs Giá Trị Sổ Sách (BVPS)")
            yr_latest = max(bvps_series.dropna().index) if not bvps_series.dropna().empty else "?"
            if bvps_latest > 0 and current_price > 0:
                prem = (current_price / bvps_latest - 1) * 100
                st.caption(f"{yr_latest}: Giá ({current_price:,.0f}đ) {'>' if prem >= 0 else '<'} BVPS ({bvps_latest:,.0f}đ) — {'premium' if prem >= 0 else 'discount'} ~{abs(prem):.0f}%")
            fig3 = go.Figure()
            xs = bvps_series.dropna().index.astype(str)
            fig3.add_trace(go.Bar(x=xs, y=bvps_series.dropna().values / 1000,
                                   name='BVPS (K đ)', marker_color='#06b6d4', opacity=0.7))
            if price_series is not None and not price_series.dropna().empty:
                xp = price_series.dropna().index.astype(str)
                fig3.add_trace(go.Scatter(x=xp, y=price_series.dropna().values / 1000,
                                          name='Giá (K đ)', line=dict(color='#ec4899', width=2),
                                          mode='lines+markers', yaxis='y'))
            fig3.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)', margin=dict(t=20, b=20),
                                yaxis=dict(title='Nghìn đồng'),
                                legend=dict(orientation='h', y=1.1))
            st.plotly_chart(fig3, use_container_width=True)

    st.caption(
        "ℹ️ Giá ước tính = Hệ số Median lịch sử 5N của chính mã × Số liệu năm gần nhất. "
        "DCF 3 kịch bản chi tiết xem tab **🧮 DCF & Graham**. "
        "Tham khảo/giáo dục — không phải lời khuyên đầu tư."
    )


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
            value_per_share = res if isinstance(res, (int, float)) else (res.get('value_per_share') if isinstance(res, dict) else None)
            if value_per_share is None:
                continue
            pct = (value_per_share / current_price - 1) * 100 if current_price else 0
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

    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_dupont.index, y=df_dupont['net_margin'] * 100,
                         name='Biên LN (%)', marker_color='#a855f7'))
    fig.add_trace(go.Bar(x=df_dupont.index, y=df_dupont['asset_turnover'] * 100,
                         name='Vòng quay TS', marker_color='#ec4899'))
    fig.add_trace(go.Bar(x=df_dupont.index, y=df_dupont['leverage'] * 100,
                         name='Đòn bẩy', marker_color='#06b6d4'))
    fig.update_layout(barmode='stack', template='plotly_dark',
                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                      margin=dict(t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

    latest_year = df_dupont.index.max()
    r = df_dupont.loc[latest_year]
    d1, d2, d3 = st.columns(3)
    d1.metric(f"Biên LN {latest_year}", f"{r['net_margin']*100:.1f}%")
    d2.metric("Vòng Quay TS", f"{r['asset_turnover']:.2f}x")
    d3.metric("Đòn Bẩy", f"{r['leverage']:.2f}x")


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
    st.caption(f"CP Lưu Hành: **{metrics['issue_share_million']:,.1f} Tr CP**")

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
        verdict      = summary.get('verdict', '')
        p25, p75     = summary.get('p25'), summary.get('p75')
        upside_median = summary.get('upside_median_pct')

        if 'UNDERVALUED' in verdict:
            rec_text, rec_color = "↑ ACCUMULATE · TÍCH LŨY", "#22c55e"
        elif 'OVERVALUED' in verdict:
            rec_text, rec_color = "↓ REDUCE · GIẢM TỈ TRỌNG", "#ef4444"
        else:
            rec_text, rec_color = "→ HOLD · NẮM GIỮ", "#fbbf24"

        target_low  = min(p25, p75) if (p25 is not None and p75 is not None) else None
        target_high = max(p25, p75) if (p25 is not None and p75 is not None) else None
        target_str  = f"₫{target_low:,.0f} – {target_high:,.0f}" if target_low is not None else "—"
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
