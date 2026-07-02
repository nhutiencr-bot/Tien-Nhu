"""
ui_components.py
-----------------
Tách toàn bộ component UI ra khỏi app.py để giảm tải render,
tăng tốc độ và dễ maintain.
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
    dtt = df_5y_table['Doanh thu thuần (tỷ)']
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

    # Bảng tổng hợp — bố cục giống ảnh mẫu: hàng = chỉ tiêu, cột = các kỳ,
    # thêm cột CAGR (tăng trưởng kép) và cột "Tăng trưởng" (mini sparkline).
    st.markdown(f"### Bảng Tổng Hợp Tài Chính {label}")

    indicator_cols = [c for c in df_5y_table.columns if c != period_col]
    periods = df_5y_table[period_col].tolist()

    def _calc_cagr(series_vals, n_periods_per_year=1):
        """CAGR giữa giá trị đầu tiên và cuối cùng có dữ liệu hợp lệ."""
        valid = [(i, v) for i, v in enumerate(series_vals) if pd.notnull(v) and v != 0]
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

    n_per_year = 4 if period_col == 'Quý' else 1
    is_pct_row = lambda name: '%' in name  # ROE/ROA: không cộng dồn theo CAGR

    def _sparkline(series_vals):
        """Sinh chuỗi mini-bar Unicode thể hiện xu hướng tăng trưởng."""
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
        return f"{''.join(out)}  {arrow}"

    rows = []
    for col in indicator_cols:
        vals = df_5y_table[col].tolist()
        row = {"Chỉ tiêu": col}
        for p, v in zip(periods, vals):
            row[p] = "—" if pd.isnull(v) else "{:,.2f}".format(float(v))
        if is_pct_row(col):
            row["CAGR"] = "—"
        else:
            cagr_val = _calc_cagr(vals, n_periods_per_year=n_per_year)
            row["CAGR"] = fmt(cagr_val, suffix="%") if cagr_val is not None else "—"
        row["Tăng trưởng"] = _sparkline(vals)
        rows.append(row)

    df_display = pd.DataFrame(rows).set_index("Chỉ tiêu")
    st.dataframe(df_display, use_container_width=True)
    st.caption(
        "CAGR = Tốc độ tăng trưởng kép giữa kỳ đầu và kỳ cuối có dữ liệu trong bảng "
        f"(theo {'năm' if period_col == 'Năm' else 'quý, quy đổi ra năm'}). "
        "Cột 'Tăng trưởng' là biểu đồ mini thể hiện xu hướng qua các kỳ."
    )
    st.caption(
        "ℹ️ Một số năm cũ (bù từ nguồn phụ CafeF khi nguồn chính không có) có thể "
        "thiếu EPS/BVPS — 2 chỉ số này bắt buộc cần đúng số CP lưu hành của năm đó, "
        "nếu không có sẽ để trống thay vì suy đoán sai. ROE/ROA vẫn được suy ra trực "
        "tiếp từ LNST/Vốn CSH/Tổng tài sản (không cần số CP) nên vẫn hiển thị đầy đủ."
    )


def render_tab_valuation(valuation_pkg, metrics):
    st.markdown("### Định Giá PE · PB · BV Trung Bình 5 Năm")
    summary = valuation_pkg.get('summary')
    methods = valuation_pkg.get('methods', {})

    if not summary:
        st.warning("Không đủ dữ liệu để chạy các phương pháp định giá.")
        return

    st.markdown(f"#### Giá Trị Hợp Lý Ước Tính: **{summary['median']:,.0f} đ/CP**")
    c1, c2 = st.columns([1, 2])
    c1.metric("Verdict", summary['verdict'])
    c2.metric("So Với Giá Hiện Tại",
              f"{summary['upside_median_pct']:+.1f}%",
              delta=f"{summary['upside_median_pct']:+.1f}%")

    if methods:
        st.markdown("#### Các Kịch Bản Định Giá")
        cols = st.columns(min(len(methods), 4) or 1)
        for i, (name, value) in enumerate(methods.items()):
            pct = (value / metrics['current_price'] - 1) * 100 if metrics['current_price'] else 0
            cols[i % len(cols)].metric(name, f"{value:,.0f} đ", delta=f"{pct:+.1f}%")

        fig = go.Figure()
        names, values = list(methods.keys()), list(methods.values())
        colors = ['#10d98a' if v >= metrics['current_price'] else '#ff4d6d' for v in values]
        fig.add_trace(go.Bar(x=names, y=values, marker_color=colors))
        fig.add_hline(y=metrics['current_price'], line_dash='dash', line_color='#fbbf24',
                      annotation_text=f"Giá hiện tại {metrics['current_price']:,.0f}đ")
        fig.update_layout(template='plotly_dark',
                          paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                          margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)

    pe_s = valuation_pkg.get('pe_series')
    pb_s = valuation_pkg.get('pb_series')
    if pe_s is not None and not pe_s.empty:
        st.markdown("### Lịch Sử P/E & P/B 5 Năm")
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=pe_s.index, y=pe_s.values, name='P/E',
                                  line=dict(color='#a855f7', width=2), yaxis='y1'))
        if pb_s is not None and not pb_s.empty:
            fig2.add_trace(go.Scatter(x=pb_s.index, y=pb_s.values, name='P/B',
                                      line=dict(color='#10d98a', width=2), yaxis='y2'))
        fig2.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            yaxis=dict(title='P/E (x)'),
            yaxis2=dict(title='P/B (x)', overlaying='y', side='right'),
            margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig2, use_container_width=True)


def _fmt_k(value):
    """Format số tiền lớn dạng rút gọn kiểu 'K' giống ảnh mẫu (31200 -> '31.2K')."""
    if value is None:
        return "—"
    try:
        return f"{value/1000:,.1f}K"
    except Exception:
        return "—"


_DCF_CARD_CSS = """
<style>
    .dcf-card {
        border-radius: 16px;
        padding: 20px 22px;
        margin-bottom: 14px;
        border: 1px solid rgba(255,255,255,0.06);
    }
    .dcf-card-bear   { background: rgba(244, 63, 94, 0.10); border-color: rgba(244,63,94,0.25); }
    .dcf-card-base   { background: linear-gradient(135deg, rgba(168,85,247,0.16), rgba(236,72,153,0.10)); border-color: rgba(168,85,247,0.30); }
    .dcf-card-bull   { background: rgba(16, 185, 129, 0.10); border-color: rgba(16,185,129,0.28); }
    .dcf-card-neutral{ background: rgba(255,255,255,0.03); }
    .dcf-card-header { display:flex; justify-content:space-between; align-items:center; }
    .dcf-card-title  { font-size: 17px; font-weight: 700; color: #f1f1f6; }
    .dcf-card-badge  {
        background: linear-gradient(90deg, #a855f7, #ec4899);
        color: white; font-size: 11px; font-weight: 700;
        padding: 3px 10px; border-radius: 20px; letter-spacing: 0.5px;
    }
    .dcf-card-sub    { color: #9a9aab; font-size: 13px; margin-top: 4px; }
    .dcf-card-bottom { display:flex; justify-content:space-between; align-items:flex-end; margin-top: 10px; }
    .dcf-card-value  { font-size: 30px; font-weight: 800; font-family: 'Courier New', monospace; }
    .dcf-card-pct    { font-size: 14px; font-weight: 700; }
    .val-bear { color: #f43f5e; } .val-base { color: #f1f1f6; } .val-bull { color: #22c55e; }
    .pct-bear { color: #f43f5e; } .pct-base { color: #22c55e; } .pct-bull { color: #22c55e; }

    .simple-card {
        border-radius: 16px; padding: 20px 22px; margin-bottom: 14px;
        background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
    }
    .simple-card-eyebrow {
        color: #9a9aab; font-size: 12px; font-weight: 700; letter-spacing: 1px;
        text-transform: uppercase; margin-bottom: 6px;
    }
    .simple-card-title { font-size: 17px; font-weight: 700; color: #f1f1f6; margin-bottom: 4px; }
    .simple-card-sub   { color: #9a9aab; font-size: 13px; margin-bottom: 14px; }
    .graham-row { display:flex; align-items:baseline; gap: 14px; margin-bottom: 16px; }
    .graham-val { font-size: 34px; font-weight: 800; font-family: 'Courier New', monospace; color: #22c55e; }
    .graham-vs  { color: #6b6b7b; font-size: 16px; }
    .graham-cur { font-size: 34px; font-weight: 800; font-family: 'Courier New', monospace; color: #f1f1f6; }
    .graham-label { color: #9a9aab; font-size: 12px; display:block; margin-top: 2px; }
    .verdict-pill {
        border-radius: 12px; padding: 12px 16px; font-weight: 700; font-size: 15px;
        text-align: center;
    }
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
        # Thứ tự cố định Bi quan → Cơ sở → Tích cực, khớp ảnh mẫu, bất kể
        # dict trả về theo thứ tự nào từ valuation.py.
        order = [
            ('Bi quan', '🔻', 'bear'), ('Cơ sở', '⚖️', 'base'), ('Tích cực', '🚀', 'bull'),
        ]
        for name, icon, tone in order:
            res = dcf.get(name)
            if not res:
                continue
            pct = (res['value_per_share'] / current_price - 1) * 100 if current_price else 0
            wacc_pct = res.get('wacc', 0) * 100
            g_pct = res.get('g', 0) * 100
            badge_html = '<span class="dcf-card-badge">BASE</span>' if tone == 'base' else ''
            st.markdown(f"""
            <div class="dcf-card dcf-card-{tone}">
                <div class="dcf-card-header">
                    <span class="dcf-card-title">{icon} {name}</span>
                    {badge_html}
                </div>
                <div class="dcf-card-sub">WACC {wacc_pct:.0f}% · g {g_pct:.1f}%</div>
                <div class="dcf-card-bottom">
                    <span></span>
                    <div style="text-align:right;">
                        <div class="dcf-card-value val-{tone}">{_fmt_k(res['value_per_share'])}</div>
                        <div class="dcf-card-pct pct-{tone}">{pct:+.0f}%</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
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
                <div>
                    <div class="graham-val">{_fmt_k(graham)}</div>
                    <span class="graham-label">Graham</span>
                </div>
                <span class="graham-vs">vs</span>
                <div>
                    <div class="graham-cur">{_fmt_k(current_price)}</div>
                    <span class="graham-label">Giá hiện tại</span>
                </div>
            </div>
            <div class="verdict-pill {verdict_class}">{verdict_text}</div>
        </div>
        """, unsafe_allow_html=True)

    reverse_g = valuation_pkg.get('reverse_dcf_g_pct')
    if reverse_g is not None:
        st.markdown(f"""
        <div class="simple-card">
            <div class="simple-card-eyebrow">🔄 Reverse DCF</div>
            <div class="big-metric-value" style="color:#22c55e;">~{reverse_g:.0f}%/năm</div>
            <div class="simple-card-sub" style="margin-top:8px;margin-bottom:0;">
                Tại giá {_fmt_k(current_price)}, thị trường đang ngụ ý tốc độ tăng trưởng FCFF ~{reverse_g:.0f}%/năm.
            </div>
        </div>
        """, unsafe_allow_html=True)

    ddm = valuation_pkg.get('ddm_value')
    if ddm:
        ddm_pct = (ddm / current_price - 1) * 100 if current_price else 0
        ddm_color = "#22c55e" if ddm_pct >= 0 else "#f43f5e"
        st.markdown(f"""
        <div class="simple-card">
            <div class="simple-card-eyebrow">🔋 DDM (Gordon)</div>
            <div class="big-metric-value" style="color:{ddm_color};">{_fmt_k(ddm)}</div>
            <div class="simple-card-sub" style="margin-top:8px;margin-bottom:0;">
                So với giá hiện tại: {ddm_pct:+.0f}%.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="simple-card">
            <div class="simple-card-eyebrow">🔋 DDM (Gordon)</div>
            <div class="simple-card-sub" style="margin-bottom:0;">
                Không áp dụng — thiếu DPS hoặc mã không chia cổ tức tiền mặt đều đặn.
            </div>
        </div>
        """, unsafe_allow_html=True)


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
    """Technical Analysis đầy đủ (data thật từ vnstock): Giá + MA20/MA50,
    RSI(14), Khối lượng GD 20 ngày, tương quan giá dầu (nếu áp dụng)."""
    st.markdown("### 📈 Phân Tích Kỹ Thuật")

    # -- Giá + MA20 + MA50 --
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

    # -- RSI(14) --
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

    # -- Khối lượng giao dịch (gộp từ tab Volume cũ) --
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
    v2.metric("KL TB 20 Ngày", f"{tech['avg_volume_20d']:,.0f} CP")
    v3.metric("So Với TB", f"{tech['volume_vs_avg_pct']:+.1f}%",
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
        </div>
        """, unsafe_allow_html=True)


def render_tab_forecast(df_5y_table, fundamentals, metrics, tech, valuation_pkg, period_col='Năm'):
    """
    Dự phóng 2026-2027 + Đánh giá tổng hợp.
    Ngoại suy đơn giản dựa trên CAGR 5 năm (Doanh thu, LNST) đã có sẵn trong
    `fundamentals`. Đây là ước tính cơ học (mechanical extrapolation), KHÔNG phải
    dự báo của công ty chứng khoán — luôn hiển thị disclaimer rõ ràng.
    """
    st.markdown("### 🔮 Dự Phóng 2026 – 2027 (Ngoại Suy Từ CAGR 5 Năm)")

    if df_5y_table.empty or period_col != 'Năm':
        st.info("Chỉ hỗ trợ dự phóng theo Năm — vui lòng xem tab KQKD 5 Năm ở chế độ 'Theo Năm'.")
        return

    df_years = df_5y_table.dropna(subset=['Năm']).sort_values('Năm')
    if df_years.empty:
        st.warning("Không đủ dữ liệu để dự phóng.")
        return

    last_year = int(df_years['Năm'].iloc[-1])
    last_revenue = df_years['Doanh thu thuần (tỷ)'].iloc[-1]
    last_profit = df_years['LNST (tỷ)'].iloc[-1]

    rev_cagr = fundamentals.get('revenue_cagr_pct')
    np_cagr = fundamentals.get('net_profit_cagr_pct')

    if last_revenue is None or last_revenue != last_revenue or rev_cagr is None:
        st.warning("⚠️ Thiếu Doanh thu/CAGR để dự phóng — hiển thị các phần khác của tab.")
        forecast_years, revenue_fc, profit_fc = [], [], []
    else:
        g_rev = rev_cagr / 100
        g_np = (np_cagr / 100) if (np_cagr is not None and np_cagr == np_cagr) else g_rev
        forecast_years = [last_year + 1, last_year + 2]
        revenue_fc = [last_revenue * (1 + g_rev) ** i for i in (1, 2)]
        profit_fc = [
            (last_profit * (1 + g_np) ** i) if (last_profit is not None and last_profit == last_profit) else None
            for i in (1, 2)
        ]

    if forecast_years:
        st.caption(
            f"Ngoại suy cơ học: Doanh thu CAGR 5N ≈ {fmt(rev_cagr, suffix='%')}, "
            f"LNST CAGR 5N ≈ {fmt(np_cagr, suffix='%')}. "
            f"⚠️ KHÔNG phải dự báo từ công ty chứng khoán — chỉ mang tính tham khảo kỹ thuật."
        )

        chart_years = [str(last_year)] + [str(y) for y in forecast_years]
        chart_revenue = [last_revenue] + revenue_fc
        chart_profit = [last_profit] + profit_fc

        fig_fc = go.Figure()
        fig_fc.add_trace(go.Bar(
            x=chart_years, y=chart_revenue,
            name='Doanh thu (tỷ) — dự phóng từ năm sau',
            marker_color=['#a855f7'] + ['#c084fc'] * len(forecast_years),
        ))
        fig_fc.add_trace(go.Scatter(
            x=chart_years, y=chart_profit,
            name='LNST dự phóng (tỷ)',
            line=dict(color='#10d98a', width=3), yaxis='y2',
        ))
        fig_fc.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(type='category'),
            yaxis=dict(title='Doanh thu (tỷ)'),
            yaxis2=dict(title='LNST (tỷ)', overlaying='y', side='right'),
            legend=dict(orientation='h', y=1.1),
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_fc, use_container_width=True)

        cols = st.columns(len(forecast_years))
        for col, y, rev, np_ in zip(cols, forecast_years, revenue_fc, profit_fc):
            col.metric(f"Doanh thu {y}", fmt(rev, suffix=" tỷ", decimals=0))
            col.metric(f"LNST {y}", fmt(np_, suffix=" tỷ", decimals=0))

    st.markdown("---")

    # ── Đánh giá tổng hợp (rating dots, phong cách dashboard tham chiếu) ──
    st.markdown("### Đánh Giá Tổng Hợp")

    def _score_to_dots(score, max_dots=5):
        score = max(0, min(max_dots, round(score)))
        filled = "●" * score
        empty = "○" * (max_dots - score)
        color = "#ec4899" if score >= 4 else ("#fbbf24" if score >= 2 else "#8b8ba7")
        return f"<span style='color:{color};letter-spacing:3px;font-size:1.1rem;'>{filled}</span>" \
               f"<span style='color:#3a3a52;letter-spacing:3px;font-size:1.1rem;'>{empty}</span>"

    roe_latest = fundamentals.get('roe_latest') or 0
    score_financial = 5 if roe_latest >= 20 else 4 if roe_latest >= 15 else 3 if roe_latest >= 10 else 2 if roe_latest >= 5 else 1

    pe_now = metrics.get('pe', 0) or 0
    score_valuation = 5 if 0 < pe_now < 8 else 4 if pe_now < 12 else 3 if pe_now < 18 else 2 if pe_now < 25 else 1

    score_competitive = 4  # mặc định trung bình-khá, không có dữ liệu thị phần định lượng

    growth_ref = rev_cagr if (rev_cagr is not None and rev_cagr == rev_cagr) else 0
    score_outlook = 5 if growth_ref >= 20 else 4 if growth_ref >= 10 else 3 if growth_ref >= 0 else 2 if growth_ref >= -10 else 1

    trend = str(tech.get('trend_signal', '')) if tech else ''
    score_catalyst = 4 if ('tăng' in trend.lower() or 'up' in trend.lower()) else 3

    ratings = [
        ("Tài chính (5 năm)", score_financial),
        (f"Định giá {last_year}", score_valuation),
        ("Vị thế cạnh tranh", score_competitive),
        ("Triển vọng tăng trưởng", score_outlook),
        ("Catalyst / Xu hướng giá", score_catalyst),
    ]
    for label, score in ratings:
        c1, c2 = st.columns([2, 1])
        c1.markdown(f"<div style='padding-top:0.3rem;'>{label}</div>", unsafe_allow_html=True)
        c2.markdown(_score_to_dots(score), unsafe_allow_html=True)

    st.caption(
        "ℹ️ Điểm đánh giá tổng hợp được suy ra tự động từ ROE, P/E, CAGR doanh thu và xu hướng giá hiện có — "
        "không thay thế cho báo cáo phân tích chuyên sâu của công ty chứng khoán."
    )

    # ── Khuyến nghị (9 PP hội tụ) — kéo từ valuation_pkg đã tính sẵn ────────
    st.markdown("---")
    summary = valuation_pkg.get('summary') if valuation_pkg else None
    if summary:
        verdict = summary.get('verdict', '')
        p25, p75 = summary.get('p25'), summary.get('p75')
        upside_median = summary.get('upside_median_pct')

        if 'UNDERVALUED' in verdict:
            rec_text, rec_color = "↑ ACCUMULATE · TÍCH LŨY", "#22c55e"
        elif 'OVERVALUED' in verdict:
            rec_text, rec_color = "↓ REDUCE · GIẢM TỈ TRỌNG", "#ef4444"
        else:
            rec_text, rec_color = "→ HOLD · NẮM GIỮ", "#fbbf24"

        target_low = min(p25, p75) if (p25 is not None and p75 is not None) else None
        target_high = max(p25, p75) if (p25 is not None and p75 is not None) else None

        target_str = (
            f"₫{target_low:,.0f} – {target_high:,.0f}"
            if target_low is not None and target_high is not None else "—"
        )
        upside_str = f"({upside_median:+.0f}%)" if upside_median is not None else ""

        st.markdown(
            f"""<div style="padding:1.2rem 1.4rem;border-radius:16px;
                background:linear-gradient(135deg, rgba(168,85,247,0.12), rgba(236,72,153,0.08));
                border:1px solid rgba(168,85,247,0.25);">
                <div style="opacity:0.7;font-size:0.85rem;letter-spacing:1px;">KHUYẾN NGHỊ (9 PP HỘI TỤ)</div>
                <div style="font-size:1.6rem;font-weight:800;color:{rec_color};margin:0.3rem 0;">{rec_text}</div>
                <div style="opacity:0.85;">Dải mục tiêu: <strong>{target_str}</strong> {upside_str}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        st.caption(
            "ℹ️ Tổng hợp từ các phương pháp PE/PB Median-TB-Sàn 5N, DCF, Graham (xem chi tiết tại tab "
            "'💰 Định Giá PE/PB · 9PP'). Không phải lời khuyên đầu tư."
        )
    else:
        st.info("Chưa đủ dữ liệu để tổng hợp khuyến nghị 9 phương pháp cho mã này.")
