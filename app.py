import re
import requests
from bs4 import BeautifulSoup
import streamlit as st
from styles import apply_premium_fintech_theme
from pipeline import execute_equity_research_pipeline
from symbols_loader import load_all_symbols, build_display_options
from ui_components import (
    render_kpi_cards, render_tab_kqkd, render_tab_valuation,
    render_tab_dcf, render_tab_dupont, render_tab_technical,
    render_tab_forecast, render_tab_multiples, fmt,
)

st.set_page_config(page_title="Equity Research AI", layout="wide")
apply_premium_fintech_theme()

REC_KEYWORDS = [
    "MUA", "BÁN", "TĂNG TỈ TRỌNG", "TĂNG TỶ TRỌNG", "GIẢM TỈ TRỌNG",
    "GIẢM TỶ TRỌNG", "NẮM GIỮ", "TRUNG LẬP", "KHẢ QUAN", "THEO DÕI",
    "PHÙ HỢP THỊ TRƯỜNG",
]

def fmt_price(v):
    if v in (None, "", "—"):
        return "—"
    try:
        return f"{float(v):,.0f}đ"
    except (ValueError, TypeError):
        return str(v)

st.title("🎯 AI Equity Research Terminal")
st.caption("Khởi chạy hệ thống tự động 7 bước kết hợp cơ chế kiểm toán vượt 7 bẫy BCTC đặc thù thị trường Việt Nam.")

@st.cache_data(ttl=3600, show_spinner=False)
def get_cached_pipeline(ticker):
    return execute_equity_research_pipeline(ticker)

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
        value="", placeholder="Nhập mã...",
    ).strip().upper()
    ticker_input = ticker_input_raw if ticker_input_raw else None

if not ticker_input:
    st.info("👆 Vui lòng chọn hoặc nhập mã cổ phiếu để bắt đầu phân tích.")
    st.stop()

# --- Pipeline ---
with st.spinner(f"⏳ Đang tải dữ liệu {ticker_input}... Lần đầu có thể mất 10-15s!"):
    pipeline_output = get_cached_pipeline(ticker_input)

if pipeline_output is None:
    st.error(f"Không thể tải dữ liệu cho mã {ticker_input}. Vui lòng thử mã khác.")
    st.stop()

# Dòng 70-72: unpack đúng 10 giá trị (bỏ df_quarter_table)
(df_price_clean, df_5y_table, df_balance_table,
 metrics, tech, news_cards, fundamentals, df_dupont,
 valuation_pkg, reports_pkg) = pipeline_output

# --- Header ---
st.markdown(f"## Báo Cáo Định Giá Toàn Diện: {ticker_input}")
st.caption(
    f"Nguồn: vnstock API ({metrics.get('source_used', 'N/A')}) · "
    "Tham khảo/giáo dục — không phải lời khuyên đầu tư · Đầu tư cổ phiếu có rủi ro mất vốn."
)

render_kpi_cards(metrics, fundamentals)

# --- Tabs ---
(tab_kqkd, tab_valuation, tab_multiples, tab_dcf, tab_dupont,
 tab_insights, tab_forecast, tab_technical, tab_news, tab_report) = st.tabs([
    "📋 KQKD 5 Năm",
    "💰 Định Giá PE/PB · 9PP",
    "📐 Multiples Mở Rộng",
    "🧮 DCF & Graham",
    "🔺 DuPont · ROE",
    "💡 Special Insights",
    "🔮 Dự Phóng 2026-2027",
    "📈 Technical Analysis",
    "📰 Tin Tức 30 Ngày",
    "📑 Báo Cáo Phân Tích",
])

# ── Tab 1: KQKD ──────────────────────────────────────────────────────────────
with tab_kqkd:
        render_tab_kqkd(df_5y_table, fundamentals, period_col="Năm")
    else:
        render_tab_kqkd(df_quarter_table, fundamentals, period_col="Quý")

# ── Tab 2: Định giá PE/PB ─────────────────────────────────────────────────────
with tab_valuation:
    render_tab_valuation(valuation_pkg, metrics)

# ── Tab 3: Multiples Mở Rộng — CARD UI ĐẸP ──────────────────────────────────
with tab_multiples:
    # Cảnh báo pha loãng nếu có
    dilution_years = valuation_pkg.get('dilution_years', [])
    if dilution_years:
        st.warning(
            f"⚠️ **Lưu ý pha loãng:** {ticker_input} phát hành thêm CP trong "
            f"năm **{', '.join(str(y) for y in dilution_years)}** "
            "(cổ tức CP hoặc phát hành quyền). "
            "EPS/BVPS lịch sử đã được điều chỉnh để so sánh chuẩn hơn."
        )
    bvps_mismatch = metrics.get('bvps_mismatch_pct')
    if bvps_mismatch is not None and bvps_mismatch > 5:
        st.caption(
            f"⚠️ BVPS gốc lệch {bvps_mismatch:.1f}% so với BVPS tự tính lại "
            "(Vốn CSH / Số CP hiện tại) — đã dùng số tự tính lại cho P/B."
        )
    ddm_note = valuation_pkg.get('ddm_note')
    if ddm_note:
        st.info(f"ℹ️ **DDM:** {ddm_note}")

    # Render card UI đẹp (thay toàn bộ code cũ)
    render_tab_multiples(metrics, fundamentals, valuation_pkg)

# ── Tab 4: DCF & Graham ───────────────────────────────────────────────────────
with tab_dcf:
    render_tab_dcf(valuation_pkg, metrics)

# ── Tab 5: DuPont ─────────────────────────────────────────────────────────────
with tab_dupont:
    render_tab_dupont(df_dupont)

# ── Tab 6: Special Insights ──────────────────────────────────────────────────
with tab_insights:
    box_bull, box_bear = st.columns(2)
    box_bull.success(
        f"**🟢 BULL CASE**\n"
        f"- Xu hướng: {tech.get('trend_signal', 'N/A')}\n"
        f"- CAGR LNST 5N: {fmt(fundamentals.get('net_profit_cagr_pct', 0), suffix='%')}\n"
        f"- ROE: {fmt(fundamentals.get('roe_latest', 0), suffix='%')}"
    )
    box_bear.error(
        "**🔴 BEAR CASE**\n"
        "- Rủi ro vĩ mô ảnh hưởng biên lợi nhuận\n"
        "- Cần kiểm tra số CP lưu hành thay đổi\n"
        "- DCF/Graham chỉ mang tính tham khảo"
    )
    if tech.get('oil_correlation', 0.0) != 0.0:
        st.warning(
            f"🛢️ Tương quan giá dầu: **{tech['oil_correlation']:.2f}** "
            "— mã nhạy cảm với biến động dầu thô WTI."
        )
    # Báo cáo CafeF nếu pipeline có
    reports_inline = reports_pkg.get("reports", []) if reports_pkg else []
    if reports_inline:
        st.markdown("---")
        st.markdown("### 📄 Báo Cáo Phân Tích (CafeF)")
        for rpt in reports_inline[:5]:
            title = rpt.get("title", "")
            url   = rpt.get("url", "#")
            src   = rpt.get("source", "CafeF")
            date  = rpt.get("date", "")
            st.markdown(
                f"[{title}]({url}) — <small style='color:#9a9aab;'>{src} · {date}</small>",
                unsafe_allow_html=True,
            )

# ── Tab 7: Dự Phóng ───────────────────────────────────────────────────────────
with tab_forecast:
    render_tab_forecast(df_5y_table, fundamentals, metrics, tech, valuation_pkg, period_col="Năm")

# ── Tab 8: Technical Analysis ─────────────────────────────────────────────────
with tab_technical:
    render_tab_technical(df_price_clean, tech, metrics)

# ── Tab 9: Tin Tức ───────────────────────────────────────────────────────────
with tab_news:
    st.subheader("📰 Tin Tức & Sự Kiện Nổi Bật")
    if news_cards and len(news_cards) > 0:
        for news in news_cards:
            title    = news.get('title', 'Không có tiêu đề')
            link     = news.get('url', '#')
            source   = news.get('source', 'Hệ thống')
            pub_date = news.get('pub_date', '—')
            if "Không có sự kiện bất thường" in title:
                st.info(title)
                continue
            st.markdown(
                f'<h5><a href="{link}" target="_blank" '
                f'style="color:white;text-decoration:none;">{title}</a></h5>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p style="color:#a0a0a0;font-size:14px;">'
                f'<span style="color:#8B5CF6;font-weight:bold;">{source}</span> | '
                f'Ngày cập nhật: {pub_date}</p>',
                unsafe_allow_html=True,
            )
            st.divider()
    else:
        st.info("Không có tin tức nào trong thời gian qua.")

# ── Tab 10: Báo Cáo Phân Tích ────────────────────────────────────────────────
def fetch_tcbs_reports(ticker):
    url = (f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/"
           f"{ticker}/analysis-reports?page=0&size=15")
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    reports = []
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json().get('listAnalysisReports', [])
            for item in data:
                reports.append({
                    "ticker":         ticker.upper(),
                    "report_date":    item.get('publishDate', '').split('T')[0],
                    "recommendation": item.get('recommendation') or "—",
                    "target_price":   item.get('targetPrice'),
                    "source":         item.get('source', 'Tổng hợp'),
                    "url":            item.get('url', '#'),
                })
    except Exception:
        pass
    return reports

with tab_report:
    st.markdown("### 📑 Báo Cáo Phân Tích & Khuyến Nghị")
    st.caption(
        f"Tổng hợp khuyến nghị mới nhất cho mã {ticker_input.upper()} "
        "— Dữ liệu lấy trực tiếp không cần đăng nhập."
    )

    cafef_url     = f"https://s.cafef.vn/bao-cao-phan-tich/{ticker_input.lower()}.chn"
    vietstock_url = f"https://finance.vietstock.vn/{ticker_input.upper()}/bao-cao-phan-tich.htm"

    rep_col1, rep_col2 = st.columns(2)
    with rep_col1:
        st.link_button("🔗 Xem tất cả trên CafeF", cafef_url, use_container_width=True)
    with rep_col2:
        st.link_button("🔗 Xem tất cả trên Vietstock", vietstock_url, use_container_width=True)

    st.divider()

    with st.spinner("Đang tải danh sách báo cáo..."):
        reports_list  = fetch_tcbs_reports(ticker_input)
        current_price = metrics.get("current_price", 0) or 0

    if not reports_list:
        st.info(
            f"Hiện chưa lấy được báo cáo nào cho mã {ticker_input.upper()}. "
            "Có thể không có báo cáo gần đây — dùng nút phía trên để xem trực tiếp."
        )
    else:
        rows_html = ""
        for r in reports_list:
            tp = r.get("target_price")
            if tp and current_price > 0:
                upside_pct   = (tp - current_price) / current_price * 100
                upside_str   = f"{upside_pct:+.0f}%"
                upside_color = "#22C55E" if upside_pct >= 0 else "#EF4444"
            else:
                upside_str, upside_color = "—", "#888"
            tp_str = f"{tp:,.0f}" if tp else "—"
            rows_html += f"""
<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">
  <td style="padding:10px 14px;font-weight:bold;">{r['ticker']}</td>
  <td style="padding:10px 14px;">{r['report_date']}</td>
  <td style="padding:10px 14px;font-weight:bold;">{r['recommendation']}</td>
  <td style="padding:10px 14px;text-align:right;">{tp_str}</td>
  <td style="padding:10px 14px;text-align:right;color:{upside_color};font-weight:bold;">{upside_str}</td>
  <td style="padding:10px 14px;">{r['source']}</td>
  <td style="padding:10px 14px;">
    <a href="{r['url']}" target="_blank"
       style="color:#8B5CF6;font-weight:bold;text-decoration:none;
              padding:4px 8px;border:1px solid #8B5CF6;border-radius:4px;">
      Xem BCPT →
    </a>
  </td>
</tr>"""

        st.markdown(f"""
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:14px;">
  <thead>
    <tr style="border-bottom:2px solid rgba(255,255,255,0.25);text-align:left;">
      <th style="padding:10px 14px;">Mã</th>
      <th style="padding:10px 14px;">Ngày</th>
      <th style="padding:10px 14px;">Khuyến nghị</th>
      <th style="padding:10px 14px;text-align:right;">Giá mục tiêu</th>
      <th style="padding:10px 14px;text-align:right;">% upside</th>
      <th style="padding:10px 14px;">CTCK</th>
      <th style="padding:10px 14px;">Link</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
</div>""", unsafe_allow_html=True)

    st.divider()
    st.caption(
        "⚠️ **Disclaimer:** Báo cáo giáo dục/tham khảo. "
        "Đối chiếu BCTC kiểm toán chính thức trước khi ra quyết định. "
        "**Không phải lời khuyên đầu tư.** Đầu tư cổ phiếu có rủi ro mất vốn."
    )
