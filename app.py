import streamlit as st

from styles import apply_premium_fintech_theme
from pipeline import execute_equity_research_pipeline
from symbols_loader import load_all_symbols, build_display_options
from ui_components import (
    render_kpi_cards, render_tab_kqkd, render_tab_valuation,
    render_tab_dcf, render_tab_dupont, render_tab_volume, render_tab_news, fmt,
)

st.set_page_config(page_title="Equity Research AI", layout="wide")
apply_premium_fintech_theme()

st.title("🎯 AI Equity Research Terminal")
st.caption("Khởi chạy hệ thống tự động 7 bước kết hợp cơ chế kiểm toán vượt 7 bẫy BCTC đặc thù thị trường Việt Nam.")

# --- Chọn mã ---
df_symbols = load_all_symbols()
display_list, display_to_symbol = build_display_options(df_symbols)

ticker_input = None
if display_list:
    selected_label = st.selectbox(
        f"Chọn mã cổ phiếu cần bóc tách (đang có {len(display_list)} mã trên HOSE/HNX/UPCOM):",
        options=["— Chọn mã cổ phiếu —"] + display_list,
        index=0,
    )
    if selected_label != "— Chọn mã cổ phiếu —":
        ticker_input = display_to_symbol[selected_label]
else:
    ticker_input_raw = st.text_input(
        "Nhập mã cổ phiếu (VD: FPT, HPG, VCB, TCB):",
        value="",
        placeholder="Nhập mã...",
    ).strip().upper()
    ticker_input = ticker_input_raw if ticker_input_raw else None

if not ticker_input:
    st.info("👆 Vui lòng chọn hoặc nhập mã cổ phiếu để bắt đầu phân tích.")
    st.stop()

# --- Pipeline ---
with st.spinner(f"⏳ Đang tải dữ liệu {ticker_input}..."):
    pipeline_output = execute_equity_research_pipeline(ticker_input)

if pipeline_output is None:
    st.error(f"Không thể tải dữ liệu cho mã {ticker_input}. Vui lòng thử mã khác.")
    st.stop()

# ── Unpack 11 items (pipeline trả về 11 — có reports_pkg ở cuối) ──────────
(df_price_clean, df_5y_table, df_quarter_table, df_balance_table,
 metrics, tech, news_cards, fundamentals, df_dupont,
 valuation_pkg, reports_pkg) = pipeline_output

# --- Header ---
st.markdown(f"## Báo Cáo Định Giá Toàn Diện: {ticker_input}")
st.caption(
    f"Nguồn: vnstock API ({metrics['source_used']}) · "
    "Tham khảo/giáo dục — không phải lời khuyên đầu tư · Đầu tư cổ phiếu có rủi ro mất vốn."
)

# --- KPI Cards ---
render_kpi_cards(metrics, fundamentals)

# --- Tabs ---
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

with tab_kqkd:
    che_do_xem = st.selectbox(
        "Xem dữ liệu theo:",
        options=["Theo Năm", "Theo Quý"],
        index=0,
        key="che_do_xem_kqkd",
    )
    if che_do_xem == "Theo Năm":
        render_tab_kqkd(df_5y_table, fundamentals, period_col="Năm")
    else:
        render_tab_kqkd(df_quarter_table, fundamentals, period_col="Quý")

with tab_valuation:
    render_tab_valuation(valuation_pkg, metrics)

with tab_multiples:
    st.markdown("### Multiples Mở Rộng")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("P/E", f"{metrics['pe']:.2f}x")
    m2.metric("P/B", f"{metrics['pb']:.2f}x")
    m3.metric("EPS",  fmt(fundamentals['eps_latest'],  suffix=" đ", decimals=0))
    m4.metric("BVPS", fmt(fundamentals['bvps_latest'], suffix=" đ", decimals=0))
    st.info("ℹ️ EV/EBITDA, P/CF, P/S phụ thuộc field bổ sung không phải nguồn nào cũng có.")

with tab_dcf:
    render_tab_dcf(valuation_pkg, metrics)

with tab_dupont:
    render_tab_dupont(df_dupont)

with tab_insights:
    box_bull, box_bear = st.columns(2)
    box_bull.success(
        f"**🟢 BULL CASE**\n"
        f"- Xu hướng: {tech['trend_signal']}\n"
        f"- CAGR LNST 5N: {fmt(fundamentals['net_profit_cagr_pct'], suffix='%')}\n"
        f"- ROE: {fmt(fundamentals['roe_latest'], suffix='%')}"
    )
    box_bear.error(
        f"**🔴 BEAR CASE**\n"
        f"- Rủi ro vĩ mô ảnh hưởng biên lợi nhuận\n"
        f"- Cần kiểm tra số CP lưu hành thay đổi\n"
        f"- DCF/Graham chỉ mang tính tham khảo"
    )
    if tech.get('oil_correlation', 0.0) != 0.0:
        st.warning(
            f"🛢️ Tương quan giá dầu: **{tech['oil_correlation']:.2f}** "
            "— mã nhạy cảm với biến động dầu thô WTI."
        )

    # Hiển thị báo cáo phân tích nếu có
    reports = reports_pkg.get("reports", []) if reports_pkg else []
    if reports:
        st.markdown("---")
        st.markdown("### 📄 Báo Cáo Phân Tích CafeF")
        for rpt in reports[:5]:
            title = rpt.get("title", "")
            url   = rpt.get("url", "#")
            src   = rpt.get("source", "CafeF")
            date  = rpt.get("date", "")
            st.markdown(
                f"[{title}]({url}) — <small style='color:#9a9aab;'>{src} · {date}</small>",
                unsafe_allow_html=True,
            )

with tab_volume:
    render_tab_volume(df_price_clean, tech, metrics)

with tab_news:
    render_tab_news(news_cards)

# --- Disclaimer ---
st.divider()
st.caption(
    f"⚠️ **Disclaimer:** Báo cáo giáo dục/tham khảo. Nguồn: vnstock API ({metrics['source_used']}). "
    "Đối chiếu BCTC kiểm toán chính thức trước khi ra quyết định. "
    "**Không phải lời khuyên đầu tư.** Đầu tư cổ phiếu có rủi ro mất vốn."
)
