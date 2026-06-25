import streamlit as st
import plotly.graph_objects as go

# NHẬP KHẨU CÁC FILE BỆ PHÓNG VÀO FILE GIAO DIỆN CHÍNH NÀY
from styles import apply_premium_fintech_theme
from pipeline import execute_equity_research_pipeline
from symbols_loader import load_all_symbols, build_display_options

# Cấu hình trang (Phải luôn nằm ở đầu tiên)
st.set_page_config(page_title="Equity Research AI", layout="wide")

# Gọi hàm khoác áo Fintech từ file styles.py
apply_premium_fintech_theme()

st.title("🎯 AI Equity Research Terminal")
st.caption("Khởi chạy hệ thống tự động 7 bước kết hợp cơ chế kiểm toán vượt 7 bẫy BCTC đặc thù thị trường Việt Nam.")

# --- Ô CHỌN MÃ: dropdown search toàn bộ HOSE + HNX + UPCOM ---
df_symbols = load_all_symbols()
display_list, display_to_symbol = build_display_options(df_symbols)

ticker_input = None

if display_list:
    default_label = next((lbl for lbl in display_list if lbl.startswith("BSR ")), display_list[0])
    selected_label = st.selectbox(
        f"Chọn mã cổ phiếu cần bóc tách (đang có {len(display_list)} mã trên HOSE/HNX/UPCOM):",
        options=display_list,
        index=display_list.index(default_label),
    )
    ticker_input = display_to_symbol[selected_label]
else:
    ticker_input = st.text_input(
        "Không tải được danh sách mã tự động — nhập mã thủ công (Ví dụ: BSR, FPT, HPG, VCB):",
        "BSR",
    ).strip().upper()


def fmt(value, suffix="", decimals=2, na="—"):
    """Format số an toàn, trả về 'na' nếu None/NaN."""
    if value is None:
        return na
    try:
        if value != value:  # NaN check
            return na
        return f"{value:,.{decimals}f}{suffix}"
    except Exception:
        return na


if ticker_input:
    pipeline_output = execute_equity_research_pipeline(ticker_input)

    if pipeline_output is not None:
        (df_price_clean, df_5y_table, df_balance_table, metrics, tech,
         news_cards, fundamentals, df_dupont, valuation_pkg) = pipeline_output

        # ============================================================
        # SECTION 1: HERO + 6 KPI CARDS
        # ============================================================
        st.markdown(f"## Báo Cáo Định Giá Toàn Diện Doanh Nghiệp: {ticker_input}")
        st.caption(
            f"Nguồn dữ liệu: vnstock API ({metrics['source_used']}) · "
            f"Tham khảo/giáo dục — không phải lời khuyên đầu tư · Đầu tư cổ phiếu có rủi ro mất vốn."
        )

        kpi1, kpi2, kpi3, kpi4, kpi5, kpi6 = st.columns(6)
        kpi1.metric("Thị Giá Hiện Tại", f"{metrics['current_price']:,.0f} đ")
        kpi2.metric("Vốn Hóa", f"{metrics['market_cap_billion']:,.0f} Tỷ VNĐ")
        kpi3.metric("P/E (TTM)", f"{metrics['pe']:.2f} x")
        kpi4.metric("P/B (TTM)", f"{metrics['pb']:.2f} x")
        kpi5.metric("ROE Gần Nhất", fmt(fundamentals['roe_latest'], suffix="%"))
        kpi6.metric("CAGR LNST 5N", fmt(fundamentals['net_profit_cagr_pct'], suffix="%"))

        # ============================================================
        # TABS — theo cấu trúc 9 section
        # ============================================================
        (tab_kqkd, tab_valuation, tab_multiples, tab_dcf, tab_dupont,
         tab_insights, tab_volume, tab_news) = st.tabs([
            "📋 KQKD 5 Năm",
            "💰 Định Giá PE/PB · 9PP",
            "📐 Multiples Mở Rộng",
            "🧮 DCF & Graham",
            "🔺 DuPont · ROE",
            "💡 Special Insights",
            "📊 Volume",
            "📰 Tin Tức 30 Ngày",
        ])

        # --- TAB: Kết quả kinh doanh 5 năm ---
        with tab_kqkd:
            st.markdown("### Kết Quả Kinh Doanh 5 Năm")

            if not df_5y_table.empty:
                fig_kqkd = go.Figure()
                fig_kqkd.add_trace(go.Bar(
                    x=df_5y_table['Năm'], y=df_5y_table['Doanh thu thuần (tỷ)'],
                    name='Doanh thu thuần (tỷ)', marker_color='#a855f7', yaxis='y1'
                ))
                fig_kqkd.add_trace(go.Scatter(
                    x=df_5y_table['Năm'], y=df_5y_table['LNST (tỷ)'],
                    name='LNST (tỷ)', line=dict(color='#ec4899', width=3), yaxis='y2'
                ))
                fig_kqkd.update_layout(
                    template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                    yaxis=dict(title='Doanh thu (tỷ)'),
                    yaxis2=dict(title='LNST (tỷ)', overlaying='y', side='right'),
                )
                st.plotly_chart(fig_kqkd, use_container_width=True)

                cagr_col1, cagr_col2 = st.columns(2)
                cagr_col1.metric("CAGR Doanh Thu (5N)", fmt(fundamentals['revenue_cagr_pct'], suffix="%"))
                cagr_col2.metric("CAGR LNST (5N)", fmt(fundamentals['net_profit_cagr_pct'], suffix="%"))

                st.markdown("### Biên Lợi Nhuận & ROE")
                fig_margin = go.Figure()
                if 'ROE (%)' in df_5y_table.columns:
                    fig_margin.add_trace(go.Scatter(
                        x=df_5y_table['Năm'], y=df_5y_table['ROE (%)'],
                        name='ROE (%)', line=dict(color='#06b6d4', width=2, dash='dash')
                    ))
                if df_5y_table['Doanh thu thuần (tỷ)'].notna().any() and df_5y_table['LNST (tỷ)'].notna().any():
                    ros = (df_5y_table['LNST (tỷ)'] / df_5y_table['Doanh thu thuần (tỷ)'] * 100)
                    fig_margin.add_trace(go.Scatter(
                        x=df_5y_table['Năm'], y=ros,
                        name='ROS - Biên LNST (%)', line=dict(color='#ec4899', width=2)
                    ))
                fig_margin.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig_margin, use_container_width=True)

                st.markdown("### Bảng Tổng Hợp Tài Chính 5 Năm")
                df_display = df_5y_table.set_index('Năm').T
                st.dataframe(df_display, use_container_width=True)
            else:
                st.warning("Không có đủ dữ liệu BCTC 5 năm cho mã này từ nguồn hiện tại.")

        # --- TAB: Định giá PE/PB + 9 phương pháp ---
        with tab_valuation:
            st.markdown("### Định Giá PE · PB · BV Trung Bình 5 Năm")
            summary = valuation_pkg.get('summary')
            methods = valuation_pkg.get('methods', {})

            if summary:
                st.markdown(f"#### Giá Trị Hợp Lý Ước Tính: **{summary['median']:,.0f} đ/CP**")
                badge_col1, badge_col2 = st.columns([1, 2])
                badge_col1.metric("Verdict", summary['verdict'])
                badge_col2.metric(
                    "So Với Giá Hiện Tại",
                    f"{summary['upside_median_pct']:+.1f}%",
                    delta=f"{summary['upside_median_pct']:+.1f}%"
                )

                st.markdown("#### Các Kịch Bản Định Giá (theo PE/PB lịch sử 5N của chính mã)")
                cols = st.columns(min(len(methods), 4) or 1)
                for i, (name, value) in enumerate(methods.items()):
                    pct = (value / metrics['current_price'] - 1) * 100 if metrics['current_price'] else 0
                    cols[i % len(cols)].metric(name, f"{value:,.0f} đ", delta=f"{pct:+.1f}%")

                st.markdown("#### Biểu Đồ So Sánh Các Phương Pháp Định Giá")
                fig_methods = go.Figure()
                names = list(methods.keys())
                values = list(methods.values())
                colors = ['#10d98a' if v >= metrics['current_price'] else '#ff4d6d' for v in values]
                fig_methods.add_trace(go.Bar(x=names, y=values, marker_color=colors, name='Giá ước tính'))
                fig_methods.add_hline(
                    y=metrics['current_price'], line_dash='dash', line_color='#fbbf24',
                    annotation_text=f"Giá hiện tại {metrics['current_price']:,.0f}đ"
                )
                fig_methods.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig_methods, use_container_width=True)
            else:
                st.warning("Không đủ dữ liệu (EPS/BVPS/lịch sử PE-PB) để chạy các phương pháp định giá cho mã này.")

            pe_series = valuation_pkg.get('pe_series')
            pb_series = valuation_pkg.get('pb_series')
            if pe_series is not None and not pe_series.empty:
                st.markdown("### Lịch Sử P/E & P/B 5 Năm")
                fig_pepb = go.Figure()
                fig_pepb.add_trace(go.Scatter(x=pe_series.index, y=pe_series.values, name='P/E', line=dict(color='#a855f7', width=2), yaxis='y1'))
                if pb_series is not None and not pb_series.empty:
                    fig_pepb.add_trace(go.Scatter(x=pb_series.index, y=pb_series.values, name='P/B', line=dict(color='#10d98a', width=2), yaxis='y2'))
                fig_pepb.update_layout(
                    template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                    yaxis=dict(title='P/E (x)'), yaxis2=dict(title='P/B (x)', overlaying='y', side='right'),
                )
                st.plotly_chart(fig_pepb, use_container_width=True)

        # --- TAB: Multiples mở rộng ---
        with tab_multiples:
            st.markdown("### Multiples Mở Rộng")
            st.caption("EV/EBITDA, P/CF, P/S — đối chiếu với trung bình lịch sử của chính mã (vnstock chưa luôn có đủ field này cho mọi nguồn).")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("P/E", f"{metrics['pe']:.2f}x")
            m2.metric("P/B", f"{metrics['pb']:.2f}x")
            m3.metric("EPS", fmt(fundamentals['eps_latest'], suffix=" đ", decimals=0))
            m4.metric("BVPS", fmt(fundamentals['bvps_latest'], suffix=" đ", decimals=0))
            st.info("ℹ️ Một số multiples (EV/EBITDA, P/CF, P/S) phụ thuộc field bổ sung mà không phải mọi nguồn dữ liệu (KBS/DNSE) đều cung cấp đầy đủ khi phải dùng nguồn dự phòng.")

        # --- TAB: DCF & Graham ---
        with tab_dcf:
            st.markdown("### Định Giá Nội Tại · DCF (FCFF) & Graham")
            dcf_scenarios = valuation_pkg.get('dcf_scenarios')

            if dcf_scenarios:
                st.markdown("#### DCF — 3 Kịch Bản FCFF")
                for name, res in dcf_scenarios.items():
                    if res:
                        pct = (res['value_per_share'] / metrics['current_price'] - 1) * 100 if metrics['current_price'] else 0
                        icon = "🚀" if pct > 50 else ("⚖️" if pct > -10 else "🔻")
                        st.metric(
                            f"{icon} {name} (WACC {res['wacc']*100:.1f}% · g {res['g']*100:.1f}%)",
                            f"{res['value_per_share']:,.0f} đ",
                            delta=f"{pct:+.1f}%"
                        )
            else:
                st.warning("Không tính được DCF do thiếu dữ liệu dòng tiền hoạt động (cash_flow) đáng tin cậy cho mã này.")

            reverse_g = valuation_pkg.get('reverse_dcf_g_pct')
            if reverse_g is not None:
                st.markdown("#### Reverse DCF")
                st.metric("Tốc Độ Tăng Trưởng FCFF Thị Trường Đang Ngụ Ý", f"~{reverse_g:.1f}%/năm")
                st.caption(f"Tại giá hiện tại {metrics['current_price']:,.0f}đ, thị trường đang giả định FCFF tăng trưởng khoảng mức trên mỗi năm.")

            graham_value = valuation_pkg.get('graham_value')
            if graham_value:
                st.markdown("#### Graham Number √(22.5 × EPS × BVPS)")
                g_pct = (graham_value / metrics['current_price'] - 1) * 100 if metrics['current_price'] else 0
                gcol1, gcol2 = st.columns(2)
                gcol1.metric("Graham Number", f"{graham_value:,.0f} đ")
                gcol2.metric("So Với Giá Hiện Tại", f"{g_pct:+.1f}%")
                if g_pct > 10:
                    st.success(f"✅ Rẻ hơn ~{g_pct:.0f}% theo Graham Number")
                elif g_pct < -10:
                    st.error(f"⚠️ Đắt hơn ~{abs(g_pct):.0f}% theo Graham Number")
                else:
                    st.info("Giá đang quanh mức hợp lý theo Graham Number")
            else:
                st.caption("Graham Number cần EPS và BVPS dương — không tính được nếu thiếu 1 trong 2.")

            ddm_value = valuation_pkg.get('ddm_value')
            st.markdown("#### DDM (Gordon Growth)")
            if ddm_value:
                st.metric("DDM (Gordon)", f"{ddm_value:,.0f} đ")
            else:
                st.caption("DDM không áp dụng được — thiếu dữ liệu cổ tức/CP (DPS) chuẩn hoá từ nguồn dữ liệu hiện tại, hoặc mã không chia cổ tức tiền mặt đều đặn.")

        # --- TAB: DuPont ---
        with tab_dupont:
            st.markdown("### DuPont · Chất Lượng ROE")
            st.caption("ROE = Biên Lợi Nhuận × Vòng Quay Tài Sản × Đòn Bẩy Tài Chính")

            if df_dupont is not None and not df_dupont.empty:
                fig_dupont = go.Figure()
                fig_dupont.add_trace(go.Bar(x=df_dupont.index, y=df_dupont['net_margin'] * 100, name='Biên LN (%)', marker_color='#a855f7'))
                fig_dupont.add_trace(go.Bar(x=df_dupont.index, y=df_dupont['asset_turnover'] * 100, name='Vòng quay TS (÷100)', marker_color='#ec4899'))
                fig_dupont.add_trace(go.Bar(x=df_dupont.index, y=df_dupont['leverage'] * 100, name='Đòn bẩy (÷100)', marker_color='#06b6d4'))
                fig_dupont.update_layout(barmode='stack', template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig_dupont, use_container_width=True)

                latest_year = df_dupont.index.max()
                latest_row = df_dupont.loc[latest_year]
                d1, d2, d3 = st.columns(3)
                d1.metric(f"Biên LN {latest_year}", f"{latest_row['net_margin']*100:.1f}%")
                d2.metric("Vòng Quay TS", f"{latest_row['asset_turnover']:.2f}x")
                d3.metric("Đòn Bẩy", f"{latest_row['leverage']:.2f}x")

                roe_now = latest_row['roe_check'] * 100
                roe_peak_year = df_dupont['roe_check'].idxmax()
                roe_peak = df_dupont['roe_check'].max() * 100
                if roe_peak_year != latest_year:
                    st.info(f"💡 ROE {latest_year} = {roe_now:.1f}% thấp hơn đỉnh {roe_peak:.1f}% ({roe_peak_year}).")
            else:
                st.warning("Không đủ dữ liệu (Doanh thu/LNST/Tổng TS/VCSH) để phân tách DuPont cho mã này.")

        # --- TAB: Special Insights (Bull/Bear) ---
        with tab_insights:
            box_bull, box_bear = st.columns(2)
            box_bull.success(
                f"**🟢 BULL CASE & CATALYSTS**\n"
                f"- Tín hiệu kỹ thuật: {tech['trend_signal']}.\n"
                f"- CAGR LNST 5N: {fmt(fundamentals['net_profit_cagr_pct'], suffix='%')}.\n"
                f"- ROE gần nhất: {fmt(fundamentals['roe_latest'], suffix='%')}."
            )
            box_bear.error(
                f"**🔴 BEAR CASE & RISKS**\n"
                f"- Rủi ro vĩ mô ảnh hưởng biên lợi nhuận.\n"
                f"- Cần kiểm soát chặt chẽ bẫy dữ liệu số lượng cổ phiếu lưu hành thay đổi.\n"
                f"- Số liệu DCF/Graham phụ thuộc giả định WACC/g cố định, chỉ mang tính tham khảo."
            )
            if tech['oil_correlation'] != 0.0:
                st.warning(f"🛢️ **Tương quan giá dầu (ngành lọc hóa dầu):** Mã `{ticker_input}` có hệ số tương quan đồng biến với giá dầu thô WTI là **{tech['oil_correlation']:.2f}**.")

        # --- TAB: Volume ---
        with tab_volume:
            st.markdown("### Phân Tích Khối Lượng Giao Dịch (Volume) 20 Ngày")
            fig_volume = go.Figure()
            fig_volume.add_trace(go.Bar(
                x=df_price_clean['time'], y=df_price_clean['volume'],
                name="Khối lượng GD", marker_color='#a855f7', opacity=0.6
            ))
            fig_volume.add_trace(go.Scatter(
                x=df_price_clean['time'], y=df_price_clean['volume_ma20'],
                line=dict(color='#ec4899', width=2), name="Volume MA20"
            ))
            fig_volume.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig_volume, use_container_width=True)

            stat_col1, stat_col2, stat_col3 = st.columns(3)
            stat_col1.metric("KL Phiên Gần Nhất", f"{tech['latest_volume']:,.0f} CP")
            stat_col2.metric("KL Trung Bình 20 Ngày", f"{tech['avg_volume_20d']:,.0f} CP")
            stat_col3.metric("So Với TB 20 Ngày", f"{tech['volume_vs_avg_pct']:+.1f}%", delta=f"{tech['volume_vs_avg_pct']:+.1f}%")
            st.caption(f"Trạng Thái Xu Hướng Giá: **{tech['trend_signal']}**　|　Khối Lượng Lưu Hành: **{metrics['issue_share_million']:,.1f} Tr CP**")

        # --- TAB: News ---
        with tab_news:
            for index, item in enumerate(news_cards):
                st.markdown(f"""
                <div style='background: rgba(255,255,255,0.01); padding: 16px; border-radius: 10px; margin-bottom: 10px; border-left: 4px solid #ec4899;'>
                    <small style='color: #a855f7;'>📰 Nguồn dữ liệu: {item['source']}</small><br>
                    <strong style='font-size: 15px; color: #f1f1f6;'>{item['title']}</strong>
                </div>
                """, unsafe_allow_html=True)

        # ============================================================
        # DISCLAIMER (đặt cuối trang, giống bản gốc của tác giả)
        # ============================================================
        st.divider()
        st.caption(
            "⚠️ **Disclaimer:** Báo cáo giáo dục/tham khảo. Số liệu lấy trực tiếp từ API vnstock "
            f"(nguồn {metrics['source_used']}) — nên đối chiếu BCTC kiểm toán chính thức trước khi ra quyết định. "
            "Các chỉ tiêu DCF/Graham/DDM dùng giả định WACC/g cố định, không phải dự báo chính xác. "
            "**Không phải lời khuyên đầu tư.** Đầu tư cổ phiếu có rủi ro mất vốn."
        )
