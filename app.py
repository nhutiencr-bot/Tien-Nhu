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
    st.markdown("### 📐 Multiples Mở Rộng · EV/EBITDA · P/CF · P/S")

    market_cap_b = metrics.get('market_cap_billion', 0) or 0
    revenue_b    = metrics.get('revenue_latest_billion', 0) or 0
    cfo_b        = metrics.get('cfo_latest_billion', 0) or 0
    ebitda_b     = metrics.get('ebitda_latest_billion', 0) or 0
    net_debt_b   = metrics.get('net_debt_billion', 0) or 0
    ev_b         = market_cap_b + net_debt_b

    # Sanity guard: vốn hóa < 100 tỷ gần như chắc chắn là lỗi đơn vị
    if market_cap_b < 100.0:
        market_cap_b = 0.0

    def _sane(val, lo=0.05, hi=200.0):
        return val if (val is not None and lo <= val <= hi) else None

    ps        = _sane((market_cap_b / revenue_b) if revenue_b > 0 else None)
    pcf       = _sane((market_cap_b / cfo_b)     if cfo_b > 0     else None)
    ev_ebitda = _sane((ev_b / ebitda_b)          if ebitda_b > 0  else None)

    pe_now = metrics.get('pe', 0) or 0
    pb_now = metrics.get('pb', 0) or 0

    pe_hist_series = valuation_pkg.get('pe_series')
    pb_hist_series = valuation_pkg.get('pb_series')
    pe_median_5y = float(pe_hist_series.dropna().median()) if pe_hist_series is not None and not pe_hist_series.dropna().empty else None
    pb_median_5y = float(pb_hist_series.dropna().median()) if pb_hist_series is not None and not pb_hist_series.dropna().empty else None

    is_bank_flag = metrics.get('excl_extended_multiples', False) or metrics.get('is_bank', False)

    bvps_mismatch = metrics.get('bvps_mismatch_pct')
    if bvps_mismatch is not None and bvps_mismatch > 5:
        st.caption(
            f"⚠️ BVPS gốc từ nguồn dữ liệu lệch {bvps_mismatch:.1f}% so với BVPS tự tính lại "
            f"(Vốn CSH / Số CP hiện tại) — có thể do số CP lưu hành trong bảng ratio đã cũ "
            f"(trước đợt chia cổ tức/tăng vốn gần nhất). Đã dùng số tự tính lại (mới hơn) cho P/B."
        )

    st.markdown("""<style>
.mc-card{border-radius:18px;padding:18px 16px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.09);min-height:148px;}
.mc-card-bank{background:rgba(168,85,247,0.06);border-color:rgba(168,85,247,0.18);}
.mc-label{font-size:.72rem;opacity:.5;text-transform:uppercase;letter-spacing:.5px;line-height:1.4;}
.mc-val{font-size:2.3rem;font-weight:800;font-family:'Courier New',monospace;line-height:1.1;margin:7px 0 5px;}
.mc-note1{font-size:.80rem;color:#a0a0c0;}
.mc-note2{font-size:.73rem;color:#6b6b8a;}
</style>""", unsafe_allow_html=True)

    def _mc(col, label, sublabel, val_str, color, note1, note2="", bank=False):
        cls = "mc-card mc-card-bank" if bank else "mc-card"
        col.markdown(f"""<div class="{cls}">
  <div class="mc-label">{label}<br><span style="font-size:.68rem;">{sublabel}</span></div>
  <div class="mc-val" style="color:{color};">{val_str}</div>
  <div class="mc-note1">{note1}</div>
  <div class="mc-note2">{note2}</div>
</div>""", unsafe_allow_html=True)

    def _color_vs_avg(val, avg):
        if val is None: return "#888"
        if avg and val < avg: return "#22c55e"
        return "#f0f0ff"

    def _note_vs_avg(val, avg, label="TB 5N"):
        if val is None: return "Thiếu dữ liệu", ""
        if avg and val < avg: return f"Dưới {label}", f"({avg:.2f}x)"
        if avg: return f"Trên {label}", f"({avg:.2f}x)"
        return "", ""

    latest_year = datetime.today().year - 1

    # ── Hàng 1: P/E · P/B · P/S · P/CF ─────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    n1, n2 = _note_vs_avg(pe_now or None, pe_median_5y)
    _mc(c1, "P/E", str(latest_year),
        f"{pe_now:.1f}x" if pe_now else "—",
        _color_vs_avg(pe_now or None, pe_median_5y), n1, n2)

    n1, n2 = _note_vs_avg(pb_now or None, pb_median_5y)
    _mc(c2, "P/B", str(latest_year),
        f"{pb_now:.2f}x" if pb_now else "—",
        _color_vs_avg(pb_now or None, pb_median_5y), n1, n2)

    if is_bank_flag:
        _mc(c3, "P/S", str(latest_year), "N/A", "#555",
            "Không áp dụng", "Ngân hàng", bank=True)
    else:
        ps_color = "#22c55e" if ps and ps < 1.5 else ("#f0f0ff" if ps else "#888")
        ps_note  = "Cạnh tranh tốt" if ps and ps < 1.5 else ("Bình thường" if ps else "Thiếu dữ liệu")
        _mc(c3, "P/S", str(latest_year),
            f"{ps:.2f}x" if ps else "—", ps_color, ps_note, "Vốn hóa / Doanh thu")

    pcf_color = "#22c55e" if pcf and pcf < 10 else ("#f0f0ff" if pcf else "#888")
    pcf_note  = "Dòng tiền hấp dẫn" if pcf and pcf < 10 else ("Bình thường" if pcf else "Thiếu dữ liệu")
    _mc(c4, "P/CF", str(latest_year),
        f"{pcf:.1f}x" if pcf else "—", pcf_color, pcf_note, "Vốn hóa / CFO")

    st.markdown("<div style='margin:10px 0'></div>", unsafe_allow_html=True)

    # ── Hàng 2: EV/EBITDA · Graham · DDM ────────────────────────────────────
    e1, e2, e3 = st.columns([2, 1, 1])

    if is_bank_flag:
        e1.markdown("""<div class="mc-card mc-card-bank" style="min-height:140px;">
  <div class="mc-label">EV/EBITDA</div>
  <div class="mc-val" style="color:#555;">N/A</div>
  <div class="mc-note1">Không áp dụng cho ngân hàng</div>
  <div class="mc-note2">Dùng P/B + ROE, NIM, NPL, CAR thay thế</div>
</div>""", unsafe_allow_html=True)
    else:
        ev_color = "#22c55e" if ev_ebitda and ev_ebitda < 8 else ("#f0f0ff" if ev_ebitda else "#888")
        ev_note  = "Định giá hợp lý" if ev_ebitda and ev_ebitda < 8 else ("Cao" if ev_ebitda else "Thiếu dữ liệu")
        _mc(e1, "EV/EBITDA", str(latest_year),
            f"{ev_ebitda:.1f}x" if ev_ebitda else "—", ev_color, ev_note,
            "EV = Vốn hóa + Nợ ròng")

    graham_v = valuation_pkg.get('graham_value')
    if graham_v and current_price > 0:
        pct_g   = (graham_v / current_price - 1) * 100
        g_color = "#22c55e" if pct_g > 0 else "#f43f5e"
        g_lbl   = f"{'Rẻ' if pct_g > 0 else 'Đắt'} {abs(pct_g):.0f}% theo Graham"
        _mc(e2, "Graham Number", "√(22.5×EPS×BVPS)",
            f"{graham_v/1000:.1f}K", g_color, g_lbl,
            f"Giá HT {current_price/1000:.2f}K")
    else:
        _mc(e2, "Graham Number", "√(22.5×EPS×BVPS)",
            "—", "#555",
            "N/A cho ngân hàng" if is_bank_flag else "Thiếu EPS/BVPS", "")

    ddm_v = valuation_pkg.get('ddm_value')
    dps_v = metrics.get('dps_latest')
    if ddm_v and current_price > 0:
        pct_d   = (ddm_v / current_price - 1) * 100
        d_color = "#22c55e" if pct_d > 0 else "#f43f5e"
        _mc(e3, "DDM (Gordon)",
            f"DPS {dps_v:,.0f}đ" if dps_v else "",
            f"{ddm_v/1000:.1f}K", d_color,
            f"{pct_d:+.0f}% vs giá HT", "ke=11%, g=4%")
    else:
        reason = "Không chia cổ tức TM" if not dps_v else "Không áp dụng"
        _mc(e3, "DDM (Gordon)", "",
            "—", "#555", reason, "DDM chỉ dùng cho mã chia TM đều")

    # ── Reverse DCF ───────────────────────────────────────────────────────────
    rev_g    = valuation_pkg.get('reverse_dcf_g_pct')
    wacc_pct = valuation_pkg.get('wacc_base_pct', 10.5)
    sector_lbl = {'steel':'Thép','bank':'Ngân hàng','retail':'Bán lẻ',
                  'tech':'Công nghệ','real_estate':'BĐS','oil_gas':'Dầu khí'
                  }.get(valuation_pkg.get('sector_detected',''), '')
    if rev_g is not None:
        rev_color = "#22c55e" if rev_g < 8 else ("#fbbf24" if rev_g < 15 else "#f43f5e")
        rev_interp = ("Thị trường kỳ vọng tăng trưởng thấp → tiềm năng upside"
                      if rev_g < 8 else
                      "Kỳ vọng tăng trưởng vừa phải" if rev_g < 15 else
                      "Kỳ vọng tăng trưởng rất cao — áp lực thực hiện lớn")
        st.markdown("---")
        rc1, rc2 = st.columns([1, 2])
        rc1.markdown(f"""<div style="border-radius:14px;padding:16px;
            background:rgba(6,182,212,0.07);border:1px solid rgba(6,182,212,0.2);">
  <div style="font-size:.7rem;color:#6b6b8a;text-transform:uppercase;letter-spacing:.5px;">🔄 Reverse DCF</div>
  <div style="font-size:2.2rem;font-weight:800;color:{rev_color};">~{rev_g:.0f}%<span style="font-size:1rem;">/năm</span></div>
  <div style="font-size:.8rem;color:#a0a0c0;">FCFF ngụ ý tại giá {current_price/1000:.2f}K</div>
</div>""", unsafe_allow_html=True)
        rc2.markdown(f"""<div style="padding:16px;font-size:.85rem;color:#a0a0c0;line-height:1.7;">
  {rev_interp}.<br>
  WACC tham chiếu: <b style="color:#f0f0ff;">{wacc_pct:.1f}%</b>
  {f' · Ngành: <b style="color:#a855f7;">{sector_lbl}</b>' if sector_lbl else ''}.
</div>""", unsafe_allow_html=True)

    if not is_bank_flag and not any([ps, pcf, ev_ebitda]):
        st.caption("ℹ️ P/S, P/CF, EV/EBITDA hiển thị '—' do thiếu dữ liệu Doanh thu/CFO/EBITDA từ nguồn API cho mã này.")


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



import requests

import streamlit as st



# =====================================================================

# HÀM CÀO API NGẦM (NÉ ANTI-BOT) - Dán ngay trên phần vẽ Tab

# =====================================================================

def fetch_tcbs_reports(ticker):

    """Lấy danh sách báo cáo phân tích từ API ngầm TCBS"""

    url = f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/{ticker}/analysis-reports?page=0&size=15"

    headers = {

        "Accept": "application/json",

        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    }

    reports = []

    try:

        res = requests.get(url, headers=headers, timeout=5)

        if res.status_code == 200:

            data = res.json().get('listAnalysisReports', [])

            for item in data:

                # Xử lý dữ liệu trả về để khớp 100% với bảng HTML của bạn

                reports.append({

                    "ticker": ticker.upper(),

                    "report_date": item.get('publishDate', '').split('T')[0],

                    "recommendation": item.get('recommendation') or "—",

                    "target_price": item.get('targetPrice'), # Có thể là None

                    "source": item.get('source', 'Tổng hợp'),

                    "url": item.get('url', '#')

                })

    except Exception:

        pass

    return reports



# =====================================================================

# GIAO DIỆN TAB BÁO CÁO PHÂN TÍCH CỦA BẠN (GIỮ NGUYÊN HTML)

# =====================================================================

with tab_report:

    st.markdown("### 📑 Báo Cáo Phân Tích & Khuyến Nghị")

    st.caption(f"Tổng hợp khuyến nghị mới nhất cho mã {ticker_input.upper()} — Dữ liệu lấy trực tiếp không cần đăng nhập.")



    cafef_url = f"https://s.cafef.vn/bao-cao-phan-tich/{ticker_input.lower()}.chn"

    vietstock_url = f"https://finance.vietstock.vn/{ticker_input.upper()}/bao-cao-phan-tich.htm"



    rep_col1, rep_col2 = st.columns(2)

    with rep_col1:

        st.link_button("🔗 Xem tất cả trên CafeF", cafef_url, use_container_width=True)

    with rep_col2:

        st.link_button("🔗 Xem tất cả trên Vietstock", vietstock_url, use_container_width=True)



    st.divider()



    with st.spinner("Đang tải danh sách báo cáo..."):

        # CHỖ ĐỔI MỚI: Gọi hàm fetch_tcbs_reports thay vì cafef

        reports_list = fetch_tcbs_reports(ticker_input)



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

                <td style="padding:10px 14px; font-weight:bold;">{r['recommendation']}</td>

                <td style="padding:10px 14px; text-align:right;">{tp_str}</td>

                <td style="padding:10px 14px; text-align:right; color:{upside_color}; font-weight:bold;">{upside_str}</td>

                <td style="padding:10px 14px;">{r['source']}</td>

                <td style="padding:10px 14px;">

                    <a href="{r['url']}" target="_blank" style="color:#8B5CF6; font-weight:bold; text-decoration:none; padding:4px 8px; border:1px solid #8B5CF6; border-radius:4px;">Xem BCPT →</a>

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
