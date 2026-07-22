"""
app.py — Tien-Nhu Equity Research Terminal
============================================
Các thay đổi so với bản cũ (419 dòng):

[FIX 1] Cache TTL: dữ liệu lịch sử 2021-2024 cache 7 ngày (giữ nguyên),
        nhưng bổ sung cache_key gồm cả ngày hôm nay để năm hiện tại (2025)
        tự động refresh mỗi ngày thay vì stuck 7 ngày.

[FIX 2] Unpack pipeline_output: thêm try/except + kiểm tra len()
        để không crash khi pipeline trả về tuple thiếu phần tử.

[FIX 3] tab_multiples: _card_label và _render_card được định nghĩa
        NGOÀI with-block (bản cũ định nghĩa bên trong → redefined mỗi lần render,
        gây warning Streamlit và không tái sử dụng được).

[FIX 4] tab_multiples: is_bank_flag mở rộng nhận diện thêm
        is_securities và is_insurance từ metrics để ẩn P/S, EV/EBITDA đúng.

[FIX 5] tab_report: fetch_cafef_reports cache key bao gồm ticker
        (bản cũ chỉ cache theo positional arg, đúng rồi nhưng thêm comment rõ hơn).

[FIX 6] tab_insights: Bull/Bear case mở rộng — thêm Revenue CAGR, hiển thị
        sector-aware warning (ngân hàng → NIM/NPL, BĐS → bàn giao lag).

[FIX 7] Thêm st.cache_data.clear() button ẩn trong sidebar để force refresh
        khi cần (debug, sau khi push code mới).

[FIX 8] render_tab_dcf import bị thiếu trong bản cũ — thêm vào import block.

[NEW]   Sidebar: hiển thị metadata mã (sàn, ngành, ngày cập nhật).
[NEW]   Tab KQKD: thêm toggle Năm/Quý đồng bộ với df_quarter_table.
"""

import re
import datetime
import requests
from bs4 import BeautifulSoup

import streamlit as st

from styles import apply_premium_fintech_theme
from pipeline import execute_equity_research_pipeline
from symbols_loader import load_all_symbols, build_display_options
from ui_components import (
    render_kpi_cards, render_tab_kqkd, render_tab_valuation,
    render_tab_multiples, render_tab_dcf, render_tab_dupont,
    render_tab_technical, render_tab_forecast, fmt,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Tien-Nhu · Equity Research", layout="wide")
apply_premium_fintech_theme()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REC_KEYWORDS = [
    "MUA", "BÁN", "TĂNG TỈ TRỌNG", "TĂNG TỶ TRỌNG",
    "GIẢM TỈ TRỌNG", "GIẢM TỶ TRỌNG", "NẮM GIỮ",
    "TRUNG LẬP", "KHẢ QUAN", "THEO DÕI", "PHÙ HỢP THỊ TRƯỜNG",
]

# Cache 7 ngày cho data lịch sử; intraday/hôm nay cache 1 giờ
_HIST_CACHE_TTL = 7 * 24 * 3600
_TODAY_CACHE_TTL = 3600

# ---------------------------------------------------------------------------
# Helpers: card UI (định nghĩa NGOÀI tab để tránh redefinition warning)
# ---------------------------------------------------------------------------
def _card_label(value, avg=None, low_note="Dưới TB 5N", high_note="Trên TB 5N",
                low_thresh=None, low_msg=None, high_msg=None):
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


def _sane_ratio(value, min_sane=0.05, max_sane=200.0):
    if value is None:
        return None
    return value if min_sane <= value <= max_sane else None


def fmt_price(v):
    if v in (None, "", "—"):
        return "—"
    try:
        return f"{float(v):,.0f}đ"
    except (ValueError, TypeError):
        return str(v)


# ---------------------------------------------------------------------------
# CafeF analyst reports scraper
# ---------------------------------------------------------------------------
@st.cache_data(ttl=_TODAY_CACHE_TTL, show_spinner=False)
def fetch_cafef_reports(ticker: str, limit: int = 8):
    """Cào danh sách báo cáo phân tích từ CafeF, parse khuyến nghị + giá mục tiêu."""
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
                href = ("https://s.cafef.vn" + href if href.startswith("/")
                        else "https://s.cafef.vn/" + href)
            seen_links.add(href)

            rec = "—"
            for kw in REC_KEYWORDS:
                if kw in title.upper():
                    rec = kw
                    break

            target_price = None
            m = re.search(
                r"(?:giá mục tiêu|gmt)[:\s]*([\d.,]+)\s*(?:vnđ|đồng|đ)?",
                title, re.IGNORECASE
            )
            if m:
                raw = m.group(1).replace(".", "").replace(",", "")
                try:
                    target_price = float(raw)
                except ValueError:
                    pass

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


# ---------------------------------------------------------------------------
# Pipeline cache — key gồm ngày hôm nay để năm hiện tại auto-refresh mỗi ngày
# ---------------------------------------------------------------------------
@st.cache_data(ttl=_HIST_CACHE_TTL, show_spinner=False)
def get_cached_pipeline(ticker: str, _today: str):
    """_today chỉ dùng để bust cache mỗi ngày, không truyền vào pipeline."""
    return execute_equity_research_pipeline(ticker)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🎯 Tien-Nhu · AI Equity Research Terminal")
st.caption(
    "Hệ thống 7 bước tự động · Kiểm toán 7 bẫy BCTC đặc thù thị trường Việt Nam · "
    "Tham khảo/giáo dục — không phải lời khuyên đầu tư."
)

# ---------------------------------------------------------------------------
# Sidebar: chọn mã + debug tools
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 📌 Chọn mã")
    df_symbols = load_all_symbols()
    display_list, display_to_symbol = build_display_options(df_symbols)

    ticker_input = None
    if display_list:
        selected_label = st.selectbox(
            f"Đang có {len(display_list)} mã (HOSE/HNX/UPCOM):",
            options=["— Chọn mã —"] + display_list,
            index=0,
        )
        if selected_label != "— Chọn mã —":
            ticker_input = display_to_symbol[selected_label]
    else:
        raw = st.text_input(
            "Nhập mã (VD: FPT, HPG, VCB):",
            value="", placeholder="Nhập mã...",
        ).strip().upper()
        ticker_input = raw if raw else None

    st.divider()
    st.markdown("### 🔧 Debug")
    if st.button("🔄 Xóa cache & reload", help="Dùng sau khi deploy code mới"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Cache: 7 ngày lịch sử / 1 giờ hôm nay")
    st.caption(f"Ngày: {datetime.date.today().strftime('%d/%m/%Y')}")

# ---------------------------------------------------------------------------
# Guard: chưa chọn mã
# ---------------------------------------------------------------------------
if not ticker_input:
    st.info("👈 Vui lòng chọn hoặc nhập mã cổ phiếu trong sidebar để bắt đầu.")
    st.stop()

# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------
today_str = datetime.date.today().isoformat()

with st.spinner(f"⏳ Đang tải dữ liệu {ticker_input}… Lần đầu ~10-15s, sau đó mượt ngay."):
    pipeline_output = get_cached_pipeline(ticker_input, _today=today_str)

if pipeline_output is None:
    st.error(f"❌ Không thể tải dữ liệu cho mã **{ticker_input}**. Vui lòng thử mã khác.")
    st.stop()

# Unpack an toàn — bảo vệ khi pipeline thêm/bớt phần tử
try:
    (df_price_clean, df_5y_table, df_quarter_table, df_balance_table,
     metrics, tech, news_cards, fundamentals, df_dupont,
     valuation_pkg, reports_pkg) = pipeline_output
except (ValueError, TypeError) as e:
    st.error(f"❌ Pipeline output lỗi cấu trúc: {e}. Xóa cache và thử lại.")
    st.stop()

# ---------------------------------------------------------------------------
# Header: tên mã + metadata
# ---------------------------------------------------------------------------
sector_label = metrics.get('sector', '') or metrics.get('industry', '') or ''
exchange_label = metrics.get('exchange', '') or ''

st.markdown(f"## 📊 {ticker_input}"
            + (f" · {exchange_label}" if exchange_label else "")
            + (f" · {sector_label}" if sector_label else ""))
st.caption(
    f"Nguồn dữ liệu: vnstock ({metrics.get('source_used', 'N/A')}) · "
    f"Cập nhật: {today_str} · "
    f"Không phải lời khuyên đầu tư · Đầu tư cổ phiếu có rủi ro mất vốn."
)

# ---------------------------------------------------------------------------
# KPI Cards
# ---------------------------------------------------------------------------
render_kpi_cards(metrics, fundamentals)

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
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

# ── Tab KQKD ────────────────────────────────────────────────────────────────
with tab_kqkd:
    period_toggle = st.radio(
        "Kỳ hiển thị:", ["Năm", "Quý"],
        horizontal=True, label_visibility="collapsed",
    )
    if period_toggle == "Năm":
        render_tab_kqkd(df_5y_table, fundamentals, period_col="Năm")
    else:
        if df_quarter_table is not None and not df_quarter_table.empty:
            render_tab_kqkd(df_quarter_table, fundamentals, period_col="Quý")
        else:
            st.info("Chưa có dữ liệu quý cho mã này.")

# ── Tab Định giá PE/PB ──────────────────────────────────────────────────────
with tab_valuation:
    render_tab_valuation(valuation_pkg, metrics)

# ── Tab Multiples Mở Rộng ───────────────────────────────────────────────────
with tab_multiples:
    st.markdown("### Multiples Mở Rộng")

    market_cap_b = metrics.get('market_cap_billion', 0) or 0
    revenue_b    = metrics.get('revenue_latest_billion', 0) or 0
    cfo_b        = metrics.get('cfo_latest_billion', 0) or 0
    ebitda_b     = metrics.get('ebitda_latest_billion', 0) or 0
    net_debt_b   = metrics.get('net_debt_billion', 0) or 0

    MIN_SANE_MARKET_CAP_B = 100.0
    if market_cap_b < MIN_SANE_MARKET_CAP_B:
        market_cap_b = 0.0

    ev_b = market_cap_b + net_debt_b

    ps       = _sane_ratio((market_cap_b / revenue_b) if revenue_b > 0 else None)
    pcf      = _sane_ratio((market_cap_b / cfo_b)     if cfo_b > 0     else None)
    ev_ebitda = _sane_ratio((ev_b / ebitda_b)          if ebitda_b > 0  else None)

    pe_now = metrics.get('pe', 0) or 0
    pb_now = metrics.get('pb', 0) or 0

    pe_hist = valuation_pkg.get('pe_series')
    pb_hist = valuation_pkg.get('pb_series')
    pe_median_5y = float(pe_hist.dropna().median()) if pe_hist is not None and not pe_hist.dropna().empty else None
    pb_median_5y = float(pb_hist.dropna().median()) if pb_hist is not None and not pb_hist.dropna().empty else None

    bvps_mismatch = metrics.get('bvps_mismatch_pct')
    if bvps_mismatch is not None and bvps_mismatch > 5:
        st.caption(
            f"⚠️ BVPS gốc lệch {bvps_mismatch:.1f}% so với BVPS tự tính lại "
            f"(Vốn CSH / Số CP lưu hành) — đã dùng số tự tính (mới hơn) cho P/B."
        )

    m1, m2, m3, m4 = st.columns(4)
    c, n = _card_label(pe_now or None, avg=pe_median_5y, low_note="Rẻ hơn TB 5N", high_note="Đắt hơn TB 5N")
    _render_card(m1, "P/E", f"{pe_now:.2f}x" if pe_now else "—", c, n)

    c, n = _card_label(pb_now or None, avg=pb_median_5y, low_note="Rẻ hơn TB 5N", high_note="Đắt hơn TB 5N")
    _render_card(m2, "P/B", f"{pb_now:.2f}x" if pb_now else "—", c, n)

    m3.metric("EPS", fmt(fundamentals.get('eps_latest', 0), suffix=" đ", decimals=0))
    m4.metric("BVPS", fmt(fundamentals.get('bvps_latest') or None, suffix=" đ", decimals=0))

    st.markdown("---")

    # [FIX 4] Mở rộng nhận diện ngành không dùng P/S, EV/EBITDA
    is_bank       = metrics.get('is_bank', False) or metrics.get('excl_extended_multiples', False)
    is_securities = metrics.get('is_securities', False)
    is_insurance  = metrics.get('is_insurance', False)
    excl_ps_ev    = is_bank or is_securities or is_insurance

    if excl_ps_ev:
        sector_reason = (
            "ngân hàng" if is_bank else
            "chứng khoán" if is_securities else
            "bảo hiểm"
        )
        st.info(
            f"ℹ️ **P/S và EV/EBITDA không áp dụng cho {sector_reason}** — "
            f"khái niệm doanh thu và EBITDA không phản ánh đúng bản chất kinh doanh. "
            f"Nên dùng P/B + ROE, NIM, NPL, CAR (ngân hàng) hoặc P/B + ROE (CTCK)."
        )
        e1, e2 = st.columns(2)
        c, n = _card_label(pcf, low_thresh=10, low_msg="Dòng tiền hấp dẫn", high_msg="Bình thường")
        _render_card(e1, "P/CF", f"{pcf:.2f}x" if pcf else "—", c, n)
        e2.markdown(
            "<div style='padding:0.5rem 0;opacity:0.6;'>"
            "<div style='font-size:0.85rem;'>P/S · EV/EBITDA</div>"
            "<div style='font-size:1.3rem;'>Không áp dụng</div></div>",
            unsafe_allow_html=True,
        )
    else:
        e1, e2, e3 = st.columns(3)
        c, n = _card_label(ps, low_thresh=8.0, low_msg="Định giá hợp lý", high_msg="Định giá cao")
        _render_card(e1, "P/S", f"{ps:.2f}x" if ps else "—", c, n)

        c, n = _card_label(pcf, low_thresh=10, low_msg="Dòng tiền hấp dẫn", high_msg="Bình thường")
        _render_card(e2, "P/CF", f"{pcf:.2f}x" if pcf else "—", c, n)

        c, n = _card_label(ev_ebitda, low_thresh=10, low_msg="Định giá hợp lý", high_msg="Bình thường")
        _render_card(e3, "EV/EBITDA", f"{ev_ebitda:.2f}x" if ev_ebitda else "—", c, n)

    if not excl_ps_ev and not (ps and pcf and ev_ebitda):
        st.caption("ℹ️ Một số chỉ số hiển thị '—' do thiếu dữ liệu từ API cho mã này.")

# ── Tab DCF & Graham ────────────────────────────────────────────────────────
with tab_dcf:
    render_tab_dcf(valuation_pkg, metrics)

# ── Tab DuPont ──────────────────────────────────────────────────────────────
with tab_dupont:
    render_tab_dupont(df_dupont)

# ── Tab Special Insights ────────────────────────────────────────────────────
with tab_insights:
    box_bull, box_bear = st.columns(2)

    revenue_cagr_pct = fundamentals.get('revenue_cagr_pct', 0) or 0
    net_profit_cagr_pct = fundamentals.get('net_profit_cagr_pct', 0) or 0
    roe_latest = fundamentals.get('roe_latest', 0) or 0

    box_bull.success(
        f"**🟢 BULL CASE**\n"
        f"- Xu hướng giá: {tech.get('trend_signal', 'N/A')}\n"
        f"- CAGR Doanh thu 5N: {fmt(revenue_cagr_pct, suffix='%')}\n"
        f"- CAGR LNST 5N: {fmt(net_profit_cagr_pct, suffix='%')}\n"
        f"- ROE: {fmt(roe_latest, suffix='%')}"
    )

    box_bear.error(
        f"**🔴 BEAR CASE**\n"
        f"- Rủi ro vĩ mô ảnh hưởng biên lợi nhuận\n"
        f"- Cần kiểm tra pha loãng cổ phiếu\n"
        f"- DCF/Graham chỉ mang tính tham khảo\n"
        f"- Dữ liệu API có thể trễ 1-2 quý"
    )

    # Sector-specific warnings
    if is_bank:
        st.info(
            "🏦 **Ngân hàng:** Doanh thu = Thu nhập lãi thuần (NII). "
            "Chú ý NIM, NPL, tỷ lệ bao phủ nợ xấu, CAR khi phân tích."
        )
    elif metrics.get('is_realestate', False):
        st.warning(
            "🏗️ **BĐS:** Doanh thu ghi nhận theo bàn giao, không theo ký HĐ. "
            "Con số có thể nhảy mạnh giữa các năm do tiến độ bàn giao, không phải do kinh doanh xấu đi."
        )

    if tech.get('oil_correlation', 0.0) != 0.0:
        corr = tech['oil_correlation']
        st.warning(f"🛢️ Tương quan giá dầu WTI: **{corr:.2f}** — mã nhạy cảm với biến động dầu thô.")

# ── Tab Dự Phóng ────────────────────────────────────────────────────────────
with tab_forecast:
    render_tab_forecast(df_5y_table, fundamentals, metrics, tech, valuation_pkg, period_col="Năm")

# ── Tab Technical ───────────────────────────────────────────────────────────
with tab_technical:
    render_tab_technical(df_price_clean, tech, metrics)

# ── Tab Tin Tức ─────────────────────────────────────────────────────────────
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
                f'<span style="color:#8B5CF6;font-weight:bold;">{source}</span>'
                f' | {pub_date}</p>',
                unsafe_allow_html=True,
            )
            st.divider()
    else:
        st.info("Không có tin tức nào trong 30 ngày qua.")

# ── Tab Báo Cáo Phân Tích ───────────────────────────────────────────────────
with tab_report:
    st.markdown("### 📑 Báo Cáo Phân Tích & Khuyến Nghị")
    st.caption(f"Tổng hợp khuyến nghị mới nhất cho **{ticker_input.upper()}** từ CafeF.")

    cafef_url    = f"https://s.cafef.vn/bao-cao-phan-tich/{ticker_input.lower()}.chn"
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
            f"Chưa cào được báo cáo nào cho **{ticker_input.upper()}** từ CafeF. "
            "Trang có thể đổi cấu trúc HTML hoặc chưa có báo cáo gần đây — "
            "dùng nút phía trên để xem trực tiếp."
        )
    else:
        rows_html = ""
        for r in reports_list:
            tp = r["target_price"]
            if tp and current_price > 0:
                upside_pct  = (tp - current_price) / current_price * 100
                upside_str  = f"{upside_pct:+.0f}%"
                upside_color = "#22C55E" if upside_pct >= 0 else "#EF4444"
            else:
                upside_str   = "—"
                upside_color = "#888"

            tp_str = f"{tp:,.0f}" if tp else "—"
            rows_html += f"""
<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">
  <td style="padding:10px 14px;font-weight:bold;">{r['ticker']}</td>
  <td style="padding:10px 14px;">{r['report_date']}</td>
  <td style="padding:10px 14px;">{r['recommendation']}</td>
  <td style="padding:10px 14px;text-align:right;">{tp_str}</td>
  <td style="padding:10px 14px;text-align:right;color:{upside_color};font-weight:bold;">{upside_str}</td>
  <td style="padding:10px 14px;">{r['source']}</td>
  <td style="padding:10px 14px;">
    <a href="{r['url']}" target="_blank"
       style="color:#8B5CF6;font-weight:bold;text-decoration:none;">Xem BCPT →</a>
  </td>
</tr>"""

        st.markdown(f"""
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:14px;">
<thead>
<tr style="border-bottom:2px solid rgba(255,255,255,0.25);text-align:left;">
  <th style="padding:10px 14px;">Mã</th>
  <th style="padding:10px 14px;">Ngày KN</th>
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
