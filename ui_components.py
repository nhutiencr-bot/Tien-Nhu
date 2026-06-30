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


def render_tab_dcf(valuation_pkg, metrics):
    st.markdown("### Định Giá Nội Tại · DCF & Graham")
    dcf = valuation_pkg.get('dcf_scenarios')
    if dcf:
        st.markdown("#### DCF — 3 Kịch Bản FCFF")
        for name, res in dcf.items():
            if res:
                pct = (res['value_per_share'] / metrics['current_price'] - 1) * 100 if metrics['current_price'] else 0
                icon = "🚀" if pct > 50 else ("⚖️" if pct > -10 else "🔻")
                st.metric(f"{icon} {name}", f"{res['value_per_share']:,.0f} đ", delta=f"{pct:+.1f}%")
    else:
        st.warning("Không tính được DCF do thiếu dữ liệu dòng tiền.")

    reverse_g = valuation_pkg.get('reverse_dcf_g_pct')
    if reverse_g is not None:
        st.markdown("#### Reverse DCF")
        st.metric("Tốc Độ Tăng Trưởng FCFF Thị Trường Ngụ Ý", f"~{reverse_g:.1f}%/năm")

    graham = valuation_pkg.get('graham_value')
    if graham:
        st.markdown("#### Graham Number")
        g_pct = (graham / metrics['current_price'] - 1) * 100 if metrics['current_price'] else 0
        c1, c2 = st.columns(2)
        c1.metric("Graham Number", f"{graham:,.0f} đ")
        c2.metric("So Với Giá Hiện Tại", f"{g_pct:+.1f}%")
        if g_pct > 10:
            st.success(f"✅ Rẻ hơn ~{g_pct:.0f}% theo Graham Number")
        elif g_pct < -10:
            st.error(f"⚠️ Đắt hơn ~{abs(g_pct):.0f}% theo Graham Number")
        else:
            st.info("Giá đang quanh mức hợp lý theo Graham Number")

    ddm = valuation_pkg.get('ddm_value')
    st.markdown("#### DDM (Gordon Growth)")
    if ddm:
        st.metric("DDM", f"{ddm:,.0f} đ")
    else:
        st.caption("DDM không áp dụng — thiếu DPS hoặc mã không chia cổ tức đều đặn.")


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


def render_tab_volume(df_price, tech, metrics):
    st.markdown("### Phân Tích Khối Lượng Giao Dịch 20 Ngày")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_price['time'], y=df_price['volume'],
                         name="Khối lượng GD", marker_color='#a855f7', opacity=0.6))
    fig.add_trace(go.Scatter(x=df_price['time'], y=df_price['volume_ma20'],
                             line=dict(color='#ec4899', width=2), name="Volume MA20"))
    fig.update_layout(template='plotly_dark',
                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                      margin=dict(t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("KL Phiên Gần Nhất", f"{tech['latest_volume']:,.0f} CP")
    c2.metric("KL TB 20 Ngày", f"{tech['avg_volume_20d']:,.0f} CP")
    c3.metric("So Với TB", f"{tech['volume_vs_avg_pct']:+.1f}%",
              delta=f"{tech['volume_vs_avg_pct']:+.1f}%")
    st.caption(f"Xu Hướng: **{tech['trend_signal']}** | CP Lưu Hành: **{metrics['issue_share_million']:,.1f} Tr CP**")


def render_tab_news(news_cards):
    for item in news_cards:
        st.markdown(f"""
        <div style='background:rgba(255,255,255,0.01);padding:16px;border-radius:10px;
                    margin-bottom:10px;border-left:4px solid #ec4899;'>
            <small style='color:#a855f7;'>📰 {item['source']}</small><br>
            <strong style='font-size:15px;color:#f1f1f6;'>{item['title']}</strong>
        </div>
        """, unsafe_allow_html=True)
