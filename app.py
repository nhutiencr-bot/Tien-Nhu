import streamlit as st
from styles import apply_premium_fintech_theme
from pipeline import execute_equity_research_pipeline
from symbols_loader import load_all_symbols, build_display_options
from ui_components import (
    render_kpi_cards, render_tab_kqkd, render_tab_valuation,
    render_tab_dcf, render_tab_dupont, render_tab_volume, fmt,
)
# Lưu ý: Tôi đã bỏ render_tab_news khỏi import vì chúng ta sẽ tự render đẹp hơn ở dưới

st.set_page_config(page_title="Equity Research AI", layout="wide")
apply_premium_fintech_theme()

st.title("🎯 AI Equity Research Terminal")
st.caption("Khởi chạy hệ thống tự động 7 bước kết hợp cơ chế kiểm toán vượt 7 bẫy BCTC đặc thù thị trường Việt Nam.")

# --- BÍ QUYẾT LÀM MƯỢT (CACHE DỮ LIỆU) ---
# Dữ liệu cào về sẽ được lưu trong bộ nhớ 1 tiếng (3600 giây). 
# Bấm chuyển tab sẽ mượt ngay lập tức vì không phải cào lại!
@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_pipeline(ticker):
    return execute_equity_research_pipeline(ticker)

# --- Chọn mã: KHÔNG mặc định mã nào ---
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

# --- Chỉ chạy khi đã chọn mã ---
if not ticker_input:
    st.info("👆 Vui lòng chọn hoặc nhập mã cổ phiếu để bắt đầu phân tích.")
    st.stop()

# --- Pipeline với spinner ---
with st.spinner(f"⏳ Đang tải dữ liệu {ticker_input}... Lần đầu có thể mất 10-15s, các lần sau sẽ mượt ngay lập tức!"):
    # Gọi hàm đã bọc Cache thay vì gọi trực tiếp pipeline
    pipeline_output = get_cached_pipeline(ticker_input)

if pipeline_output is None:
    st.error(f"Không thể tải dữ liệu cho mã {ticker_input}. Vui lòng thử mã khác.")
    st.stop()

(df_price_clean, df_5y_table, df_quarter_table, df_balance_table, metrics, tech,
 news_cards, fundamentals, df_dupont, valuation_pkg, reports_pkg) = pipeline_output

# --- Header ---
st.markdown(f"## Báo Cáo Định Giá Toàn Diện: {ticker_input}")
st.caption(
    f"Nguồn: vnstock API ({metrics.get('source_used', 'N/A')}) · "
    f"Tham khảo/giáo dục — không phải lời khuyên đầu tư · Đầu tư cổ phiếu có rủi ro mất vốn."
)

# --- KPI Cards ---
render_kpi_cards(metrics, fundamentals)

# --- Tabs ---
(tab_kqkd, tab_valuation, tab_multiples, tab_dcf, tab_dupont,
 tab_insights, tab_volume, tab_news, tab_reports) = st.tabs([
    "📋 KQKD 5 Năm",
    "💰 Định Giá PE/PB · 9PP",
    "📐 Multiples Mở Rộng",
    "🧮 DCF & Graham",
    "🔺 DuPont · ROE",
    "💡 Special Insights",
    "📊 Volume",
    "📰 Tin Tức 30 Ngày",
    "📑 Báo Cáo Phân Tích",
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
    m1.metric("P/E", f"{metrics.get('pe', 0):.2f}x")
    m2.metric("P/B", f"{metrics.get('pb', 0):.2f}x")
    m3.metric("EPS", fmt(fundamentals.get('eps_latest', 0), suffix=" đ", decimals=0))
    m4.metric("BVPS", fmt(fundamentals.get('bvps_latest', 0), suffix=" đ", decimals=0))
    st.info("ℹ️ EV/EBITDA, P/CF, P/S phụ thuộc field bổ sung không phải nguồn nào cũng có.")

with tab_dcf:
    render_tab_dcf(valuation_pkg, metrics)

with tab_dupont:
    render_tab_dupont(df_dupont)

with tab_insights:
    box_bull, box_bear = st.columns(2)
    box_bull.success(
        f"**🟢 BULL CASE**\n"
        f"- Xu hướng: {tech.get('trend_signal', 'N/A')}\n"
        f"- CAGR LNST 5N: {fmt(fundamentals.get('net_profit_cagr_pct', 0), suffix='%')}\n"
        f"- ROE: {fmt(fundamentals.get('roe_latest', 0), suffix='%')}"
    )
    box_bear.error(
        f"**🔴 BEAR CASE**\n"
        f"- Rủi ro vĩ mô ảnh hưởng biên lợi nhuận\n"
        f"- Cần kiểm tra số CP lưu hành thay đổi\n"
        f"- DCF/Graham chỉ mang tính tham khảo"
    )
    if tech.get('oil_correlation', 0.0) != 0.0:
        st.warning(f"🛢️ Tương quan giá dầu: **{tech['oil_correlation']:.2f}** — mã nhạy cảm với biến động dầu thô WTI.")

with tab_volume:
    render_tab_volume(df_price_clean, tech, metrics)

# --- TAB TIN TỨC: TIÊU ĐỀ TRẮNG + TÊN NGUỒN MÀU TÍM CHỦ ĐẠO ---
with tab_news:
    st.subheader("📰 Tin Tức & Sự Kiện Nổi Bật")
    if news_cards and len(news_cards) > 0:
        for news in news_cards:
            title = news.get('title', 'Không có tiêu đề')
            link = news.get('url', '#')
            source = news.get('source', 'Hệ thống')
            pub_date = news.get('pub_date', '—')
            
            if "Không có sự kiện bất thường" in title:
                st.info(title)
                continue
                
            # 1. Tiêu đề chữ trắng, link click được (đã bỏ icon 📰 và 🔗)
            st.markdown(
                f'<h5><a href="{link}" target="_blank" style="color: white; text-decoration: none;">{title}</a></h5>', 
                unsafe_allow_html=True
            )
            
            # 2. Chỉ hiện tên nguồn (màu tím) + ngày, bỏ chữ "Nguồn:"
            st.markdown(
                f'<p style="color: #a0a0a0; font-size: 14px;"><span style="color: #8B5CF6; font-weight: bold;">{source}</span> | Ngày cập nhật: {pub_date}</p>', 
                unsafe_allow_html=True
            )
            
            st.divider()
    else:
        st.info("Không có tin tức nào trong thời gian qua.")

# --- TAB BÁO CÁO PHÂN TÍCH (CafeF) ---
with tab_reports:
    st.subheader("📑 Báo Cáo Phân Tích & Khuyến Nghị")
    reports = reports_pkg.get("reports", []) if reports_pkg else []
    is_specific = reports_pkg.get("is_ticker_specific", False) if reports_pkg else False

    if not is_specific and reports:
        st.info(
            f"Chưa tìm thấy báo cáo riêng cho mã {ticker_input} trong danh sách mới nhất. "
            "Dưới đây là các báo cáo phân tích mới nhất trên toàn thị trường."
        )

    if reports:
        for r in reports:
            st.markdown(
                f'<h5><a href="{r["url"]}" target="_blank" style="color: white; text-decoration: none;">{r["title"]}</a></h5>',
                unsafe_allow_html=True
            )
            st.markdown(
                f'<p style="color: #a0a0a0; font-size: 14px;"><span style="color: #8B5CF6; font-weight: bold;">{r["source"]}</span> | Ngày cập nhật: {r["pub_date"]}</p>',
                unsafe_allow_html=True
            )
            st.divider()
        st.caption(f"Nguồn: Tổng hợp từ {' + '.join(reports_pkg.get('sources_used', ['CafeF']))} · Tham khảo, không phải khuyến nghị đầu tư.")
    else:
        st.info("Không tải được báo cáo phân tích vào lúc này. Vui lòng thử lại sau.")


# --- Disclaimer ---
st.divider()
st.caption(
    f"⚠️ **Disclaimer:** Báo cáo giáo dục/tham khảo. Nguồn: vnstock API ({metrics.get('source_used', 'N/A')}). "
    "Đối chiếu BCTC kiểm toán chính thức trước khi ra quyết định. "
    "**Không phải lời khuyên đầu tư.** Đầu tư cổ phiếu có rủi ro mất vốn."
)
