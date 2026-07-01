import re
import requests
from bs4 import BeautifulSoup
from cafef_reports import fetch_cafef_reports

import streamlit as st
from styles import apply_premium_fintech_theme
from pipeline import execute_equity_research_pipeline
from symbols_loader import load_all_symbols, build_display_options
from ui_components import (
    render_kpi_cards, render_tab_kqkd, render_tab_valuation,
    render_tab_dcf, render_tab_dupont, render_tab_technical, render_tab_forecast, fmt,
)

st.set_page_config(page_title="Equity Research AI", layout="wide")
apply_premium_fintech_theme()

REC_KEYWORDS = [
    "MUA", "BÁN", "TĂNG TỈ TRỌNG", "TĂNG TỶ TRỌNG",
    "GIẢM TỈ TRỌNG", "GIẢM TỶ TRỌNG", "NẮM GIỮ",
    "TRUNG LẬP", "KHẢ QUAN", "THEO DÕI", "PHÙ HỢP THỊ TRƯỜNG",
]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_cafef_reports(ticker: str, limit: int = 8):
    """Cào danh sách báo cáo phân tích từ CafeF cho 1 mã, parse khuyến nghị + giá mục tiêu từ tiêu đề."""
    url = f"https://s.cafef.vn/bao-cao-phan-tich/{ticker.lower()}.chn"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    out = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        items = soup.select("a[href*='.chn']")
        seen_links = set()

        for a in items:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or len(title) < 15 or href in seen_links:
                continue
            if "report" not in href:
                continue
            if not href.startswith("http"):
                href = "https://s.cafef.vn" + href if href.startswith("/") else "https://s.cafef.vn/" + href
            seen_links.add(href)

            rec = "—"
            for kw in REC_KEYWORDS:
                if kw in title.upper():
                    rec = kw
                    break

            target_price = None
            m = re.search(r"(?:giá mục tiêu|gmt)[:\s]*([\d.,]+)\s*(?:vnđ|đồng|đ)?", title, re.IGNORECASE)
            if m:
                raw = m.group(1).replace(".", "").replace(",", "")
                try:
                    target_price = float(raw)
                except ValueError:
                    target_price = None

            source_match = re.search(r"-\s*([A-Z]{2,6})\s*$", title)
            source = source_match.group(1) if source_match else "—"

            out.append({
                "ticker": ticker.upper(),
                "recommendation": rec,
                "target_price": target_price,
                "ref_price": None,
                "report_date": "—",
                "source": source,
                "url": href,
                "title": title,
            })
            if len(out) >= limit:
                break
    except Exception:
        return []
    return out


def fmt_price(v):
    if v in (None, "", "—"):
        return "—"
    try:
        return f"{float(v):,.0f}đ"
    except (ValueError, TypeError):
        return str(v)


st.title("🎯 AI Equity Research Terminal")
st.caption("Khởi chạy hệ thống tự động 7 bước kết hợp cơ chế kiểm toán vượt 7 bẫy BCTC đặc thù thị trường Việt Nam.")

# --- BÍ QUYẾT LÀM MƯỢT (CACHE DỮ LIỆU) ---
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
    ev_b = market_cap_b + net_debt_b

    ps = (market_cap_b / revenue_b) if revenue_b > 0 else None
    pcf = (market_cap_b / cfo_b) if cfo_b > 0 else None
    ev_ebitda = (ev_b / ebitda_b) if ebitda_b > 0 else None

    pe_now = metrics.get('pe', 0) or 0
    pb_now = metrics.get('pb', 0) or 0

    def _card_label(value, avg=None, low_note="Dưới TB 5N", high_note="Trên TB 5N",
                     low_thresh=None, low_msg=None, high_msg=None):
        """Trả về (màu, dòng chú thích nhỏ) theo phong cách dashboard tham chiếu."""
        if value is None:
            return "#888", "Thiếu dữ liệu"
        if avg is not None and avg > 0:
            if value < avg:
                return "#22c55e", f"{low_note} ({avg:.2f}x)"
            return "#ef4444", f"{high_note} ({avg:.2f}x)"
        if low_thresh is not None:
            if value < low_thresh:
                return "#22c55e", low_msg or "Hấp dẫn"
            return "#eab308", high_msg or "Bình thường"
        return "#e5e7eb", ""

    def _render_card(col, title, value_str, color, note):
        col.markdown(
            f"""<div style="padding:0.5rem 0;">
                <div style="opacity:0.7;font-size:0.85rem;">{title}</div>
                <div style="font-size:1.9rem;font-weight:700;color:{color};">{value_str}</div>
                <div style="opacity:0.75;font-size:0.8rem;">{note}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    m1, m2, m3, m4 = st.columns(4)
    c, n = _card_label(pe_now if pe_now else None, low_thresh=15, low_msg="Định giá hấp dẫn", high_msg="Định giá cao")
    _render_card(m1, "P/E", f"{pe_now:.2f}x" if pe_now else "—", c, n)
    c, n = _card_label(pb_now if pb_now else None, low_thresh=2, low_msg="Định giá hấp dẫn", high_msg="Định giá cao")
    _render_card(m2, "P/B", f"{pb_now:.2f}x" if pb_now else "—", c, n)
    m3.metric("EPS", fmt(fundamentals.get('eps_latest', 0), suffix=" đ", decimals=0))
    m4.metric("BVPS", fmt(fundamentals.get('bvps_latest', 0), suffix=" đ", decimals=0))

    st.markdown("---")
    e1, e2, e3 = st.columns(3)
    c, n = _card_label(ps, low_thresh=1.5, low_msg="Cạnh tranh tốt", high_msg="Định giá cao")
    _render_card(e1, "P/S", f"{ps:.2f}x" if ps else "—", c, n)
    c, n = _card_label(pcf, low_thresh=10, low_msg="Dòng tiền hấp dẫn", high_msg="Bình thường")
    _render_card(e2, "P/CF", f"{pcf:.2f}x" if pcf else "—", c, n)
    c, n = _card_label(ev_ebitda, low_thresh=10, low_msg="Định giá hợp lý", high_msg="Bình thường")
    _render_card(e3, "EV/EBITDA", f"{ev_ebitda:.2f}x" if ev_ebitda else "—", c, n)

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

with tab_forecast:
    render_tab_forecast(df_5y_table, fundamentals, metrics, tech, valuation_pkg, period_col="Năm")

with tab_technical:
    render_tab_technical(df_price_clean, tech, metrics)

# --- TAB TIN TỨC ---
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

            st.markdown(
                f'<h5><a href="{link}" target="_blank" style="color: white; text-decoration: none;">{title}</a></h5>',
                unsafe_allow_html=True
            )
            st.markdown(
                f'<p style="color: #a0a0a0; font-size: 14px;"><span style="color: #8B5CF6; font-weight: bold;">{source}</span> | Ngày cập nhật: {pub_date}</p>',
                unsafe_allow_html=True
            )
            st.divider()
    else:
        st.info("Không có tin tức nào trong thời gian qua.")

# --- TAB BÁO CÁO PHÂN TÍCH ---
with tab_report:
    st.markdown("### 📑 Báo Cáo Phân Tích & Khuyến Nghị")
    st.caption(f"Tổng hợp khuyến nghị mới nhất cho mã {ticker_input.upper()} từ CafeF — không cần đăng nhập.")

    cafef_url = f"https://s.cafef.vn/bao-cao-phan-tich/{ticker_input.lower()}.chn"
    vietstock_url = f"https://finance.vietstock.vn/{ticker_input.upper()}/bao-cao-phan-tich.htm"

    rep_col1, rep_col2 = st.columns(2)
    with rep_col1:
        st.link_button("🔗 Xem tất cả trên CafeF", cafef_url, use_container_width=True)
    with rep_col2:
        st.link_button("🔗 Xem tất cả trên Vietstock", vietstock_url, use_container_width=True)

    st.divider()

    with st.spinner("Đang tải danh sách báo cáo..."):
        reports_list = fetch_cafef_reports(ticker_input)

    current_price = metrics.get("current_price", 0) or 0

    if not reports_list:
        st.info(
            f"Hiện chưa cào được báo cáo nào cho mã {ticker_input.upper()} từ CafeF. "
            "Có thể trang đã đổi cấu trúc HTML hoặc không có báo cáo gần đây — dùng nút phía trên để xem trực tiếp."
        )
    else:
        rows_html = ""
        for r in reports_list:
            tp = r["target_price"]
            if tp and current_price > 0:
                upside_pct = (tp - current_price) / current_price * 100
                upside_str = f"{upside_pct:+.0f}%"
                upside_color = "#22C55E" if upside_pct >= 0 else "#EF4444"
            else:
                upside_str = "—"
                upside_color = "#888"

            tp_str = f"{tp:,.0f}" if tp else "—"

            rows_html += f"""
            <tr style="border-bottom: 1px solid rgba(255,255,255,0.08);">
                <td style="padding:10px 14px; font-weight:bold;">{r['ticker']}</td>
                <td style="padding:10px 14px;">{r['report_date']}</td>
                <td style="padding:10px 14px;">{r['recommendation']}</td>
                <td style="padding:10px 14px; text-align:right;">{tp_str}</td>
                <td style="padding:10px 14px; text-align:right; color:{upside_color}; font-weight:bold;">{upside_str}</td>
                <td style="padding:10px 14px;">{r['source']}</td>
                <td style="padding:10px 14px;">
                    <a href="{r['url']}" target="_blank" style="color:#8B5CF6; font-weight:bold; text-decoration:none;">Xem BCPT →</a>
                </td>
            </tr>
            """

        table_html = f"""
        <div style="overflow-x:auto;">
        <table style="width:100%; border-collapse:collapse; font-size:14px;">
            <thead>
                <tr style="border-bottom: 2px solid rgba(255,255,255,0.25); text-align:left;">
                    <th style="padding:10px 14px;">Mã</th>
                    <th style="padding:10px 14px;">Ngày khuyến nghị</th>
                    <th style="padding:10px 14px;">Khuyến nghị</th>
                    <th style="padding:10px 14px; text-align:right;">Giá mục tiêu</th>
                    <th style="padding:10px 14px; text-align:right;">% upside</th>
                    <th style="padding:10px 14px;">CTCK</th>
                    <th style="padding:10px 14px;">Link BCPT</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        </div>
        """
        st.markdown(table_html, unsafe_allow_html=True)

    st.divider()
    st.caption(
        "⚠️ **Disclaimer:** Báo cáo giáo dục/tham khảo. "
        "Đối chiếu BCTC kiểm toán chính thức trước khi ra quyết định. "
        "**Không phải lời khuyên đầu tư.** Đầu tư cổ phiếu có rủi ro mất vốn."
    )
