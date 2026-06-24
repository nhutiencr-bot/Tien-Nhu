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
    # Fallback: nếu không tải được danh sách mã (lỗi mạng/nguồn data),
    # vẫn cho phép người dùng gõ tay như cách cũ để app không bị kẹt cứng.
    ticker_input = st.text_input(
        "Không tải được danh sách mã tự động — nhập mã thủ công (Ví dụ: BSR, FPT, HPG, VCB):",
        "BSR",
    ).strip().upper()

if ticker_input:
    # Gọi hàm xử lý luồng dữ liệu từ file pipeline.py
    pipeline_output = execute_equity_research_pipeline(ticker_input)

    if pipeline_output is not None:
        df_price_clean, df_income_table, df_balance_table, metrics, tech, news_cards = pipeline_output

        # --- HERO & KPI CARDS ---
        st.markdown(f"## Báo Cáo Định Giá Toàn Diện Doanh Nghiệp: {ticker_input}")

        kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
        kpi_col1.metric("Thị Giá Hiện Tại", f"{metrics['current_price']:,.0f} đ")
        kpi_col2.metric("Vốn Hóa Chuẩn Hóa", f"{metrics['market_cap_billion']:,.0f} Tỷ VNĐ")
        kpi_col3.metric("P/E (Auto-Adjusted)", f"{metrics['pe']:.2f} x")
        kpi_col4.metric("P/B Định Kỳ", f"{metrics['pb']:.2f} x")

        # --- TAB CHỨC NĂNG ---
        tab_tech_view, tab_financial_view, tab_independent_view, tab_news_digest = st.tabs([
            "📊 Khối Lượng Giao Dịch (Volume)",
            "📋 Kết Quả Kinh Doanh 5 Năm",
            "💡 Special Insights (Bull/Bear)",
            "📰 Bản Tin Thời Sự 30 Ngày"
        ])

        with tab_tech_view:
            st.markdown("### Phân tích Khối lượng Giao dịch (Volume) 20 ngày")
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
            stat_col3.metric(
                "So Với TB 20 Ngày",
                f"{tech['volume_vs_avg_pct']:+.1f}%",
                delta=f"{tech['volume_vs_avg_pct']:+.1f}%"
            )

            st.caption(f"Trạng Thái Xu Hướng Giá: **{tech['trend_signal']}**　|　Khối Lượng Lưu Hành: **{metrics['issue_share_million']:,.1f} Tr CP**")

            if tech['oil_correlation'] != 0.0:
                st.warning(f"🛢️ **Mô hình tương quan đặc thù:** Mã `{ticker_input}` có hệ số tương quan đồng biến với giá dầu thô WTI là **{tech['oil_correlation']:.2f}**.")

        with tab_financial_view:
            st.markdown("### Bảng cân đối & Kết quả kinh doanh")
            st.dataframe(df_income_table.head(15), use_container_width=True)
            st.markdown("### Cơ cấu Nguồn vốn & Tài sản")
            st.dataframe(df_balance_table.head(15), use_container_width=True)

        with tab_independent_view:
            box_bull, box_bear = st.columns(2)
            box_bull.success(f"**🟢 BULL CASE & CATALYSTS**\n- Tín hiệu kỹ thuật xác nhận trạng thái: {tech['trend_signal']}.\n- Trực quan hóa giá đã điều chỉnh giúp phản ánh đúng EPS.")
            box_bear.error(f"**🔴 BEAR CASE & RISKS**\n- Rủi ro vĩ mô ảnh hưởng biên lợi nhuận.\n- Cần kiểm soát chặt chẽ bẫy dữ liệu số lượng cổ phiếu lưu hành thay đổi.")

        with tab_news_digest:
            for index, item in enumerate(news_cards):
                st.markdown(f"""
                <div style='background: rgba(255,255,255,0.01); padding: 16px; border-radius: 10px; margin-bottom: 10px; border-left: 4px solid #ec4899;'>
                    <small style='color: #a855f7;'>📰 Nguồn dữ liệu: {item['source']}</small><br>
                    <strong style='font-size: 15px; color: #f1f1f6;'>{item['title']}</strong>
                </div>
                """, unsafe_allow_html=True)
