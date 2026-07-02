import streamlit as st
from styles import apply_premium_fintech_theme
from pipeline import execute_equity_research_pipeline
from symbols_loader import load_all_symbols, build_display_options
from ui_components import (
    render_kpi_cards, render_tab_kqkd, render_tab_valuation,
    render_tab_dcf, render_tab_dupont, render_tab_technical,
    render_tab_news, render_tab_forecast, fmt,
)

st.set_page_config(page_title="Equity Research AI", layout="wide")
apply_premium_fintech_theme()

st.title("🎯 AI Equity Research Terminal")
st.caption("Khởi chạy hệ thống tự động 7 bước kết hợp cơ chế kiểm toán vượt 7 bẫy BCTC đặc thù thị trường Việt Nam.")

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
with st.spinner(f"⏳ Đang tải dữ liệu {ticker_input}..."):
    pipeline_output = execute_equity_research_pipeline(ticker_input)

if pipeline_output is None:
    st.error(f"Không thể tải dữ liệu cho mã {ticker_input}. Vui lòng thử mã khác.")
    st.stop()

# pipeline.py hiện tại trả về 11 giá trị (có reports_pkg ở cuối, hiện chưa
# dùng UI riêng nhưng vẫn phải unpack đủ để khớp số lượng).
(df_price_clean, df_5y_table, df_quarter_table, df_balance_table, metrics, tech,
 news_cards, fundamentals, df_dupont, valuation_pkg, reports_pkg) = pipeline_output

# --- Header ---
st.markdown(f"## Báo Cáo Định Giá Toàn Diện: {ticker_input}")
st.caption(
    f"Nguồn: vnstock API ({metrics['source_used']}) · "
    f"Tham khảo/giáo dục — không phải lời khuyên đầu tư · Đầu tư cổ phiếu có rủi ro mất vốn."
)

# --- KPI Cards ---
render_kpi_cards(metrics, fundamentals)

# --- Tabs --- (giữ nguyên 8 tab cũ + thêm lại tab Dự Phóng 2026-2027)
(tab_kqkd, tab_valuation, tab_multiples, tab_dcf, tab_dupont,
 tab_insights, tab_forecast, tab_technical, tab_news) = st.tabs([
    "📋 KQKD 5 Năm",
    "💰 Định Giá PE/PB · 9PP",
    "📐 Multiples Mở Rộng",
    "🧮 DCF & Graham",
    "🔺 DuPont · ROE",
    "💡 Special Insights",
    "🔮 Dự Phóng 2026-2027",
    "📈 Technical Analysis",
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

    market_cap_b = metrics.get('market_cap_billion', 0) or 0
    revenue_b = metrics.get('revenue_latest_billion', 0) or 0
    cfo_b = metrics.get('cfo_latest_billion', 0) or 0
    ebitda_b = metrics.get('ebitda_latest_billion', 0) or 0
    net_debt_b = metrics.get('net_debt_billion', 0) or 0

    MIN_SANE_MARKET_CAP_B = 100.0
    if market_cap_b < MIN_SANE_MARKET_CAP_B:
        market_cap_b = 0.0
    ev_b = market_cap_b + net_debt_b

    def _sane_ratio(value, min_sane=0.05, max_sane=200.0):
        if value is None:
            return None
        if not (min_sane <= value <= max_sane):
            return None
        return value

    ps = _sane_ratio((market_cap_b / revenue_b) if revenue_b > 0 else None)
    pcf = _sane_ratio((market_cap_b / cfo_b) if cfo_b > 0 else None)
    ev_ebitda = _sane_ratio((ev_b / ebitda_b) if ebitda_b > 0 else None)

    pe_now = metrics.get('pe', 0) or 0
    pb_now = metrics.get('pb', 0) or 0

    pe_hist_series = valuation_pkg.get('pe_series')
    pb_hist_series = valuation_pkg.get('pb_series')
    pe_median_5y = float(pe_hist_series.dropna().median()) if pe_hist_series is not None and not pe_hist_series.dropna().empty else None
    pb_median_5y = float(pb_hist_series.dropna().median()) if pb_hist_series is not None and not pb_hist_series.dropna().empty else None

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("P/E", f"{pe_now:.2f}x" if pe_now else "—")
    m2.metric("P/B", f"{pb_now:.2f}x" if pb_now else "—")
    m3.metric("EPS", fmt(fundamentals.get('eps_latest', 0), suffix=" đ", decimals=0))
    m4.metric("BVPS", fmt(fundamentals.get('bvps_latest', 0), suffix=" đ", decimals=0))
    div_yield = metrics.get('dividend_yield_pct')
    m5.metric("Dividend Yield", f"{div_yield:.2f}%" if div_yield else "—")
    if div_yield is None:
        st.caption("ℹ️ Mã này không có cổ tức tiền mặt trong dữ liệu 5 năm gần nhất (có thể đang giữ lại lợi nhuận để tái đầu tư/tái cơ cấu, hoặc chỉ trả cổ tức bằng cổ phiếu) — DDM (Gordon) do đó không áp dụng được, hãy tham khảo các phương pháp định giá khác (PE/PB/DCF) ở tab bên cạnh.")

    st.markdown("---")
    is_bank_flag = metrics.get('excl_extended_multiples', False) or metrics.get('is_bank', False)

    if is_bank_flag:
        st.info(
            "ℹ️ **P/S và EV/EBITDA không áp dụng cho cổ phiếu ngân hàng** — "
            "khái niệm 'Doanh thu' và 'EBITDA' không phản ánh đúng bản chất kinh doanh "
            "của ngân hàng. Với ngân hàng nên dùng P/B + ROE, NIM, NPL, CAR thay thế."
        )
        e1, e2 = st.columns(2)
        e1.metric("P/CF", f"{pcf:.2f}x" if pcf else "—")
        e2.markdown(
            "<div style='padding:0.5rem 0;opacity:0.6;'>"
            "<div style='font-size:0.85rem;'>P/S · EV/EBITDA</div>"
            "<div style='font-size:1.3rem;'>Không áp dụng</div></div>",
            unsafe_allow_html=True,
        )
    else:
        e1, e2, e3 = st.columns(3)
        e1.metric("P/S", f"{ps:.2f}x" if ps else "—")
        e2.metric("P/CF", f"{pcf:.2f}x" if pcf else "—")
        e3.metric("EV/EBITDA", f"{ev_ebitda:.2f}x" if ev_ebitda else "—")

        if not (ps and pcf and ev_ebitda):
            st.caption("ℹ️ Một số chỉ số hiển thị '—' do thiếu dữ liệu Doanh thu/Dòng tiền HĐKD/Khấu hao từ nguồn API cho mã này.")

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
    if tech['oil_correlation'] != 0.0:
        st.warning(f"🛢️ Tương quan giá dầu: **{tech['oil_correlation']:.2f}** — mã nhạy cảm với biến động dầu thô WTI.")

with tab_forecast:
    render_tab_forecast(df_5y_table, fundamentals, metrics, tech, valuation_pkg, period_col="Năm")

with tab_technical:
    render_tab_technical(df_price_clean, tech, metrics)

with tab_news:
    render_tab_news(news_cards)

# --- Disclaimer ---
st.divider()
st.caption(
    f"⚠️ **Disclaimer:** Báo cáo giáo dục/tham khảo. Nguồn: vnstock API ({metrics['source_used']}). "
    "Đối chiếu BCTC kiểm toán chính thức trước khi ra quyết định. "
    "**Không phải lời khuyên đầu tư.** Đầu tư cổ phiếu có rủi ro mất vốn."
)
