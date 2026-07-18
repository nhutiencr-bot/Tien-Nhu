"""
Tien-Nhu · Cô Tiên — Vietnamese Equity Research Dashboard
Dùng với: streamlit run app.py
Yêu cầu: pipeline.py + financial_normalizer.py + valuation.py +
          cafef_fallback.py + sector_wacc.py (cùng thư mục)
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import traceback

# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Tien-Nhu · Phân tích cổ phiếu VN",
    page_icon="🧚",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── CSS (dark theme nhất quán với HPG demo) ─────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background-color: #0a0a14; color: #f0f0ff; }
.stTabs [data-baseweb="tab-list"] {
  background: rgba(16,16,31,0.9); border-bottom: 1px solid rgba(139,92,246,0.2);
  gap: 4px; padding: 0 4px;
}
.stTabs [data-baseweb="tab"] {
  background: transparent; color: #8b8ba7; font-size: 12px; font-weight: 600;
  padding: 10px 16px; border-radius: 8px 8px 0 0;
}
.stTabs [aria-selected="true"] {
  background: rgba(168,85,247,0.12); color: #a855f7;
  border-bottom: 2px solid #a855f7;
}
.stSelectbox > div > div { background: #16162a; color: #f0f0ff; border-color: rgba(139,92,246,0.3); }
.stTextInput > div > div > input {
  background: #16162a; color: #f0f0ff; border-color: rgba(139,92,246,0.3);
  font-size: 18px; font-weight: 700; text-transform: uppercase;
}
.stButton > button {
  background: linear-gradient(135deg, #a855f7, #ec4899);
  color: white; border: none; border-radius: 10px;
  padding: 10px 28px; font-weight: 700; font-size: 14px;
}
.stButton > button:hover { opacity: 0.88; }
.stDataFrame { background: rgba(28,28,48,0.6); }
.stExpander { border: 1px solid rgba(139,92,246,0.15); border-radius: 12px; }

/* Cards */
.hero-box {
  background: linear-gradient(135deg, rgba(168,85,247,0.13), rgba(236,72,153,0.08));
  border: 1px solid rgba(139,92,246,0.25); border-radius: 24px;
  padding: 28px 32px; margin-bottom: 20px;
}
.kpi-card {
  background: rgba(28,28,48,0.65); border: 1px solid rgba(139,92,246,0.18);
  border-radius: 16px; padding: 16px 14px; text-align: center;
}
.kpi-label { font-size: 10px; color: #5a5a72; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }
.kpi-value { font-size: 20px; font-weight: 800; margin: 5px 0 4px; font-variant-numeric: tabular-nums; }
.kpi-delta { font-size: 11px; font-weight: 600; }
.card {
  background: rgba(28,28,48,0.55); border: 1px solid rgba(139,92,246,0.15);
  border-radius: 20px; padding: 20px 18px; margin-bottom: 16px;
}
.card-title { font-size: 14px; font-weight: 700; color: #f0f0ff; margin-bottom: 4px; }
.card-sub   { font-size: 11px; color: #5a5a72; margin-bottom: 12px; }
.sec-hdr { font-size: 16px; font-weight: 700; color: #f0f0ff; margin: 20px 0 10px; }
.tag { display:inline-block; font-size:10px; font-weight:700; letter-spacing:.8px;
       text-transform:uppercase; padding:3px 9px; border-radius:6px; color:#fff; margin-right:6px; }
.tag-purple { background:#a855f7; }
.tag-pink   { background:#ec4899; }
.tag-cyan   { background:#06b6d4; color:#000; }
.tag-green  { background:#10d98a; color:#000; }
.tag-amber  { background:#fbbf24; color:#000; }
.pos  { color: #10d98a !important; font-weight: 700; }
.neg  { color: #ff4d6d !important; font-weight: 700; }
.neu  { color: #fbbf24 !important; font-weight: 700; }

/* Val cards */
.val-card {
  background: rgba(20,20,40,.5); border: 1px solid rgba(139,92,246,.18);
  border-radius: 16px; padding: 16px; text-align: center;
}
.val-card.rec {
  background: linear-gradient(135deg,rgba(168,85,247,.22),rgba(236,72,153,.14));
  border-color: rgba(236,72,153,.4);
}
.insight-box {
  background: linear-gradient(135deg,rgba(6,182,212,.08),rgba(139,92,246,.06));
  border: 1px solid rgba(6,182,212,.2); border-radius: 18px; padding: 18px;
}
.news-row {
  padding: 12px 0; border-bottom: 1px solid rgba(139,92,246,.08);
}
.disclaimer {
  background: rgba(251,191,36,.06); border: 1px solid rgba(251,191,36,.2);
  border-radius: 12px; padding: 12px 16px; font-size: 11px; color: #8b8ba7;
}
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 1.2rem; padding-bottom: 3rem; }
</style>
""", unsafe_allow_html=True)

# ─── Plotly theme helpers ────────────────────────────────────────────────────
CHART_GRID = "rgba(139,92,246,0.07)"
CHART_TEXT = "#8b8ba7"
PURPLE, PINK, CYAN, GREEN, RED, AMBER = "#a855f7","#ec4899","#06b6d4","#10d98a","#ff4d6d","#fbbf24"

def _base_layout(**extra):
    base = dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter", color=CHART_TEXT, size=11),
        legend=dict(orientation="h", y=-0.18, x=.5, xanchor="center",
                    font=dict(size=10, color=CHART_TEXT), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=0, r=0, t=8, b=40),
        xaxis=dict(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT)),
        yaxis=dict(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT)),
    )
    base.update(extra)
    return base

def _chart(fig, h=260, **extra):
    fig.update_layout(**_base_layout(height=h, **extra))
    return fig

def _fmt(val, decimals=1, suffix=""):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    return f"{val:,.{decimals}f}{suffix}"

def _pct_color(val):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "neu"
    return "pos" if val >= 0 else "neg"

def _arrow(val):
    if val is None: return ""
    return "▲" if val >= 0 else "▼"


# ─── TOP BAR / SEARCH ────────────────────────────────────────────────────────
st.markdown("""
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
  <span style="font-size:22px">🧚</span>
  <span style="font-size:20px;font-weight:800;color:#a855f7">Tien-Nhu</span>
  <span style="font-size:13px;color:#5a5a72">· Phân tích cổ phiếu Việt Nam</span>
</div>
""", unsafe_allow_html=True)

col_inp, col_btn, col_spacer = st.columns([2, 1, 5])
with col_inp:
    ticker_input = st.text_input(
        label="Mã cổ phiếu",
        placeholder="VD: HPG, VNM, VCB...",
        label_visibility="collapsed",
        max_chars=10,
    ).upper().strip()
with col_btn:
    run_btn = st.button("🔍 Phân tích", use_container_width=True)

if not ticker_input:
    st.markdown("""
    <div style="text-align:center;padding:80px 0;color:#5a5a72">
      <div style="font-size:48px">🧚</div>
      <div style="font-size:18px;font-weight:600;margin-top:12px;color:#8b8ba7">
        Nhập mã cổ phiếu để bắt đầu phân tích
      </div>
      <div style="font-size:13px;margin-top:8px">
        Hỗ trợ hơn 1,500 mã trên HOSE · HNX · UPCOM
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ─── RUN PIPELINE ────────────────────────────────────────────────────────────
@st.cache_data(ttl=1800, show_spinner=False)
def _run_pipeline(ticker):
    from pipeline import execute_equity_research_pipeline
    return execute_equity_research_pipeline(ticker)

with st.spinner(f"⏳ Đang tải dữ liệu {ticker_input}..."):
    try:
        result = _run_pipeline(ticker_input)
    except Exception as e:
        st.error(f"❌ Lỗi khi tải pipeline: {e}")
        st.code(traceback.format_exc())
        st.stop()

if result is None:
    st.error(f"❌ Không lấy được dữ liệu cho mã **{ticker_input}**. Kiểm tra lại mã hoặc thử lại sau.")
    st.stop()

# Unpack 10-tuple
(df_price, df_5y_table, df_quarter_table, df_balance,
 metrics, technical_summary,
 news_list, fundamentals, df_dupont, valuation_pkg) = result

# ─── Derived values ───────────────────────────────────────────────────────────
current_price   = metrics.get("current_price", 0)
market_cap_b    = metrics.get("market_cap_billion", 0)
pe_cur          = metrics.get("pe", 0)
pb_cur          = metrics.get("pb", 0)
issue_share_m   = metrics.get("issue_share_million", 0)
source_used     = metrics.get("source_used", "—")
is_bank         = metrics.get("is_bank", False)
rev_cagr        = fundamentals.get("revenue_cagr_pct")
np_cagr         = fundamentals.get("net_profit_cagr_pct")
roe_latest      = fundamentals.get("roe_latest")
roa_latest      = fundamentals.get("roa_latest")
eps_latest      = fundamentals.get("eps_latest", 0)
bvps_latest     = fundamentals.get("bvps_latest", 0)
dps_latest      = metrics.get("dps_latest")
div_yield       = metrics.get("dividend_yield_pct")

val_methods     = valuation_pkg.get("methods") or {}
val_summary     = valuation_pkg.get("summary") or {}
dcf_scenarios   = valuation_pkg.get("dcf_scenarios") or {}
graham_value    = valuation_pkg.get("graham_value")
ddm_value       = valuation_pkg.get("ddm_value")
ddm_note        = valuation_pkg.get("ddm_note", "")
rev_dcf_g       = valuation_pkg.get("reverse_dcf_g_pct")
pe_series       = valuation_pkg.get("pe_series", pd.Series(dtype=float))
pb_series       = valuation_pkg.get("pb_series", pd.Series(dtype=float))
bvps_series     = valuation_pkg.get("bvps_series", pd.Series(dtype=float))
price_series    = valuation_pkg.get("price_series", pd.Series(dtype=float))

tp_base         = val_summary.get("base_target") if val_summary else None
upside_pct      = ((tp_base / current_price - 1) * 100) if tp_base and current_price > 0 else None

years_avail = sorted([
    y for y in df_5y_table['Năm'].tolist() if pd.notna(y)
]) if not df_5y_table.empty else []

def _col(col):
    """Safely get a column from df_5y_table as a year-indexed Series."""
    if col not in df_5y_table.columns:
        return pd.Series(dtype=float)
    s = df_5y_table.set_index('Năm')[col]
    return pd.to_numeric(s, errors='coerce')

rev_s   = _col('Doanh thu thuần (tỷ)')
np_s    = _col('LNST (tỷ)')
eq_s    = _col('Vốn CSH (tỷ)')
ta_s    = _col('Tổng tài sản (tỷ)')
eps_s   = _col('EPS (đ)')
bvps_s  = _col('BVPS (đ)')
roe_s   = _col('ROE (%)')
roa_s   = _col('ROA (%)')
ros_s   = _col('ROS (%)')
cfo_s   = _col('LCFD HĐKD (tỷ)')
price_eoy_s = _col('Giá cuối năm (đ)')


# ═══════════════════════════════════════════════════════════════════════════════
# HERO SECTION
# ═══════════════════════════════════════════════════════════════════════════════
prev_close = float(df_price['close_vnd'].iloc[-2]) if len(df_price) >= 2 else current_price
chg_abs = current_price - prev_close
chg_pct = (chg_abs / prev_close * 100) if prev_close > 0 else 0
chg_cls = "pos" if chg_abs >= 0 else "neg"

st.markdown(f"""
<div class="hero-box">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px">
    <div>
      <span style="display:inline-block;background:linear-gradient(135deg,#a855f7,#ec4899);
        color:#fff;padding:4px 14px;border-radius:999px;font-weight:700;font-size:13px;margin-bottom:8px">
        📊 {ticker_input} · HOSE/HNX
      </span>
      <div style="font-size:34px;font-weight:800;letter-spacing:-1px;
        background:linear-gradient(90deg,#fff,#c4b5fd);-webkit-background-clip:text;
        -webkit-text-fill-color:transparent">{ticker_input}</div>
      <div style="color:#8b8ba7;font-size:13px;margin-top:2px">
        Nguồn: {source_used} · Cập nhật: {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M')}
      </div>
    </div>
    <div style="text-align:right">
      <div style="font-size:44px;font-weight:900;letter-spacing:-1.5px;color:#f0f0ff">
        {_fmt(current_price, 0)}<small style="font-size:18px;color:#8b8ba7;font-weight:400"> đ</small>
      </div>
      <div class="{chg_cls}" style="font-size:16px">
        {_arrow(chg_abs)} {_fmt(abs(chg_abs), 0)}đ ({_fmt(abs(chg_pct), 2)}%)
      </div>
      <div style="color:#8b8ba7;font-size:12px;margin-top:4px">
        Vốn hóa: {_fmt(market_cap_b, 0)} tỷ đồng
      </div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# KPI strip
k_cols = st.columns(6)
kpi_items = [
    ("P/E",        _fmt(pe_cur, 1, "x"),   "neu", "Hiện tại"),
    ("P/B",        _fmt(pb_cur, 2, "x"),   "neu", "Hiện tại"),
    ("ROE",        _fmt(roe_latest, 1, "%"), _pct_color(roe_latest), "Năm gần nhất"),
    ("EPS",        _fmt(eps_latest, 0, "đ"), "pos" if eps_latest > 0 else "neg", "Năm gần nhất"),
    ("BVPS",       _fmt(bvps_latest, 0, "đ"), "neu", "Năm gần nhất"),
    ("Target (base)", _fmt(tp_base, 0, "đ") if tp_base else "—",
     _pct_color(upside_pct), f"Upside: {_fmt(upside_pct, 1, '%')}" if upside_pct else "Đang tính"),
]
for col, (label, val, cls, delta) in zip(k_cols, kpi_items):
    with col:
        st.markdown(f"""
        <div class="kpi-card">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value {cls}">{val}</div>
          <div class="kpi-delta" style="color:#8b8ba7">{delta}</div>
        </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════
TAB_NAMES = [
    "📈 KQKD",
    "💰 PE/PB",
    "🔢 Multiples",
    "🏗️ DCF/Graham",
    "🔁 DuPont",
    "✨ Insights",
    "🔮 Dự phóng",
    "📐 Technical",
    "📰 Tin tức",
    "📋 Báo cáo",
]
tabs = st.tabs(TAB_NAMES)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 · KQKD
# ─────────────────────────────────────────────────────────────────────────────
with tabs[0]:
    view = st.radio("Xem theo:", ["Năm", "Quý"], horizontal=True, label_visibility="collapsed")
    st.markdown("<br>", unsafe_allow_html=True)

    if view == "Năm":
        table_df = df_5y_table
        idx_col  = "Năm"
    else:
        table_df = df_quarter_table if (df_quarter_table is not None and not df_quarter_table.empty) else pd.DataFrame()
        idx_col  = "Quý" if not table_df.empty and "Quý" in table_df.columns else None

    if not table_df.empty and idx_col:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<div class="card-title">Doanh thu & LNST</div><div class="card-sub">Đơn vị: tỷ đồng</div>', unsafe_allow_html=True)
            if view == "Năm":
                x_labels = [str(y) for y in rev_s.index]
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_trace(go.Bar(name="Doanh thu", x=x_labels, y=rev_s.values,
                                     marker_color=PURPLE, opacity=.85), secondary_y=False)
                fig.add_trace(go.Scatter(name="LNST", x=x_labels, y=np_s.values,
                                         line=dict(color=PINK, width=3),
                                         marker=dict(size=7, color=PINK, line=dict(color="#fff", width=2)),
                                         mode="lines+markers"), secondary_y=True)
                fig.update_yaxes(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT), secondary_y=False)
                fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT), secondary_y=True)
                _chart(fig, 260, xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT)))
            else:
                _rev_q = pd.to_numeric(table_df['Doanh thu thuần (tỷ)'], errors='coerce')
                _np_q  = pd.to_numeric(table_df['LNST (tỷ)'],             errors='coerce')
                x_q    = table_df['Quý'].tolist()
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_trace(go.Bar(name="Doanh thu", x=x_q, y=_rev_q.values,
                                     marker_color=PURPLE, opacity=.75), secondary_y=False)
                fig.add_trace(go.Scatter(name="LNST", x=x_q, y=_np_q.values,
                                         line=dict(color=PINK, width=2),
                                         marker=dict(size=6, color=PINK), mode="lines+markers"), secondary_y=True)
                fig.update_yaxes(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT), secondary_y=False)
                fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT), secondary_y=True)
                _chart(fig, 260, xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT)))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        with c2:
            st.markdown('<div class="card-title">ROE & ROS (%)</div>', unsafe_allow_html=True)
            if view == "Năm":
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(
                    name="ROE (%)", x=[str(y) for y in roe_s.index], y=roe_s.values,
                    line=dict(color=CYAN, width=3), mode="lines+markers",
                    marker=dict(size=7, color=CYAN, line=dict(color="#fff", width=2))))
                fig2.add_trace(go.Scatter(
                    name="ROS (%)", x=[str(y) for y in ros_s.index], y=ros_s.values,
                    line=dict(color=PINK, width=3, dash="dot"), mode="lines+markers",
                    marker=dict(size=7, color=PINK, line=dict(color="#fff", width=2))))
                _chart(fig2, 260, yaxis=dict(gridcolor=CHART_GRID, ticksuffix="%",
                                              tickfont=dict(color=CHART_TEXT)),
                       xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT)))
            else:
                _roe_q = pd.to_numeric(table_df['ROE (%)'], errors='coerce')
                _ros_q = pd.to_numeric(table_df['ROS (%)'], errors='coerce')
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(name="ROE", x=x_q, y=_roe_q.values,
                                          line=dict(color=CYAN, width=2), mode="lines+markers"))
                fig2.add_trace(go.Scatter(name="ROS", x=x_q, y=_ros_q.values,
                                          line=dict(color=PINK, width=2, dash="dot"), mode="lines+markers"))
                _chart(fig2, 260, yaxis=dict(gridcolor=CHART_GRID, ticksuffix="%",
                                              tickfont=dict(color=CHART_TEXT)),
                       xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT)))
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})

    # Table
    st.markdown(f'<div class="sec-hdr"><span class="tag tag-purple">Bảng</span>Dữ liệu tài chính {"theo Năm" if view=="Năm" else "theo Quý"}</div>', unsafe_allow_html=True)
    if not table_df.empty:
        num_cols = [c for c in table_df.columns if c not in ['Năm', 'Quý']]
        fmt_table = table_df.copy()
        for c in num_cols:
            fmt_table[c] = pd.to_numeric(fmt_table[c], errors='coerce').map(
                lambda v: f"{v:,.1f}" if pd.notna(v) else "—")
        st.dataframe(fmt_table.set_index(idx_col), use_container_width=True)

        if view == "Năm":
            cagr_row = {
                "Chỉ tiêu": "CAGR 5N",
                "Doanh thu thuần (tỷ)": f"{_fmt(rev_cagr, 1, '%')}",
                "LNST (tỷ)":            f"{_fmt(np_cagr, 1, '%')}",
            }
            st.markdown(f"""
            <div style="display:flex;gap:24px;margin-top:12px;flex-wrap:wrap">
              <div class="kpi-card" style="flex:1;min-width:160px">
                <div class="kpi-label">CAGR Doanh thu 5N</div>
                <div class="kpi-value {_pct_color(rev_cagr)}">{_fmt(rev_cagr, 1, '%')}</div>
              </div>
              <div class="kpi-card" style="flex:1;min-width:160px">
                <div class="kpi-label">CAGR LNST 5N</div>
                <div class="kpi-value {_pct_color(np_cagr)}">{_fmt(np_cagr, 1, '%')}</div>
              </div>
              <div class="kpi-card" style="flex:1;min-width:160px">
                <div class="kpi-label">ROE năm gần nhất</div>
                <div class="kpi-value {_pct_color(roe_latest)}">{_fmt(roe_latest, 1, '%')}</div>
              </div>
              <div class="kpi-card" style="flex:1;min-width:160px">
                <div class="kpi-label">ROA năm gần nhất</div>
                <div class="kpi-value {_pct_color(roa_latest)}">{_fmt(roa_latest, 1, '%')}</div>
              </div>
            </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 · PE/PB Định giá
# ─────────────────────────────────────────────────────────────────────────────
with tabs[1]:
    # Valuation scenario cards
    if val_methods:
        st.markdown('<div class="sec-hdr"><span class="tag tag-pink">Kịch bản</span>Giá trị hợp lý ước tính</div>', unsafe_allow_html=True)

        scenarios = []
        scenario_keys = [
            ("pe_bear",  "PE Bear",   RED),
            ("pe_base",  "PE Base",   PURPLE),
            ("pe_bull",  "PE Bull",   GREEN),
            ("pb_bear",  "PB Bear",   RED),
            ("pb_base",  "PB Base",   PURPLE),
            ("pb_bull",  "PB Bull",   GREEN),
        ]
        for key, label, color in scenario_keys:
            val = val_methods.get(key)
            if val and isinstance(val, dict):
                tp = val.get("target_price") or val.get("value")
                if tp:
                    upside = (tp / current_price - 1) * 100 if current_price > 0 else 0
                    scenarios.append((label, tp, color, upside))
            elif isinstance(val, (int, float)) and val > 0:
                upside = (val / current_price - 1) * 100 if current_price > 0 else 0
                scenarios.append((label, val, color, upside))

        if scenarios:
            s_cols = st.columns(len(scenarios))
            for i, (label, tp, color, upside) in enumerate(scenarios):
                with s_cols[i]:
                    u_color = GREEN if upside >= 0 else RED
                    st.markdown(f"""
                    <div class="val-card">
                      <div style="font-size:10px;color:#5a5a72;text-transform:uppercase;letter-spacing:.7px;font-weight:600">{label}</div>
                      <div style="font-size:22px;font-weight:800;color:{color};margin:8px 0 4px">{_fmt(tp, 0)}<span style="font-size:12px;color:#8b8ba7">đ</span></div>
                      <div style="font-size:12px;font-weight:700;color:{u_color}">{_arrow(upside)} {_fmt(abs(upside), 1, '%')}</div>
                    </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)

    with c1:
        st.markdown('<div class="card-title">Lịch sử P/E & P/B</div>', unsafe_allow_html=True)
        if not pe_series.empty or not pb_series.empty:
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            if not pe_series.empty:
                xs = [str(y) for y in pe_series.index]
                fig.add_trace(go.Scatter(name="P/E", x=xs, y=pe_series.values,
                                         line=dict(color=PURPLE, width=3),
                                         marker=dict(size=7, color=PURPLE, line=dict(color="#fff", width=2)),
                                         mode="lines+markers"), secondary_y=False)
            if not pb_series.empty:
                xs2 = [str(y) for y in pb_series.index]
                fig.add_trace(go.Scatter(name="P/B", x=xs2, y=pb_series.values,
                                         line=dict(color=GREEN, width=3),
                                         marker=dict(size=7, color=GREEN, line=dict(color="#fff", width=2)),
                                         mode="lines+markers"), secondary_y=True)
            fig.update_yaxes(title_text="P/E", title_font=dict(color=PURPLE, size=10),
                             tickfont=dict(color=PURPLE), gridcolor=CHART_GRID, secondary_y=False)
            fig.update_yaxes(title_text="P/B", title_font=dict(color=GREEN, size=10),
                             tickfont=dict(color=GREEN), gridcolor="rgba(0,0,0,0)", secondary_y=True)
            _chart(fig, 260, xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT)))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("Chưa có dữ liệu P/E, P/B lịch sử")

    with c2:
        st.markdown('<div class="card-title">Giá vs BVPS</div>', unsafe_allow_html=True)
        if not bvps_series.empty:
            bvps_k = bvps_series / 1000
            price_k = price_series / 1000 if not price_series.empty else pd.Series(dtype=float)
            xs = [str(y) for y in bvps_k.index]
            fig2 = go.Figure()
            fig2.add_trace(go.Bar(name="BVPS (K đ)", x=xs, y=bvps_k.values,
                                  marker_color=PURPLE, opacity=.75))
            if not price_k.empty:
                xs_p = [str(y) for y in price_k.index]
                fig2.add_trace(go.Scatter(name="Giá (K đ)", x=xs_p, y=price_k.values,
                                          line=dict(color=PINK, width=3),
                                          marker=dict(size=7, color=PINK, line=dict(color="#fff", width=2)),
                                          mode="lines+markers"))
            _chart(fig2, 260,
                   yaxis=dict(gridcolor=CHART_GRID, ticksuffix="K", tickfont=dict(color=CHART_TEXT)),
                   xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT)))
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("Chưa có dữ liệu BVPS")

    if val_summary:
        st.markdown('<div class="sec-hdr"><span class="tag tag-green">Tổng hợp</span>Consensus định giá</div>', unsafe_allow_html=True)
        s1, s2, s3, s4 = st.columns(4)
        for col, (label, val, cls) in zip([s1, s2, s3, s4], [
            ("Trung bình",  _fmt(val_summary.get("mean_target"), 0, "đ"),  _pct_color(val_summary.get("upside_mean_pct"))),
            ("Median",      _fmt(val_summary.get("median_target"), 0, "đ"), _pct_color(val_summary.get("upside_median_pct"))),
            ("P25 (thấp)",  _fmt(val_summary.get("p25_target"), 0, "đ"),   "neg"),
            ("P75 (cao)",   _fmt(val_summary.get("p75_target"), 0, "đ"),   "pos"),
        ]):
            with col:
                st.markdown(f"""
                <div class="kpi-card">
                  <div class="kpi-label">{label}</div>
                  <div class="kpi-value {cls}">{val}</div>
                </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 · Extended Multiples
# ─────────────────────────────────────────────────────────────────────────────
with tabs[2]:
    st.markdown('<div class="sec-hdr"><span class="tag tag-cyan">Multiples</span>Chỉ số định giá mở rộng</div>', unsafe_allow_html=True)
    rev_b   = metrics.get("revenue_latest_billion", 0)
    cfo_b   = metrics.get("cfo_latest_billion")
    ebitda_b = metrics.get("ebitda_latest_billion")
    net_debt_b = metrics.get("net_debt_billion", 0)
    excl    = metrics.get("excl_extended_multiples", False)

    if excl:
        st.info("🏦 Cổ phiếu ngân hàng: P/S, P/CF, EV/EBITDA không phù hợp để phân tích. Dùng P/BV, P/E, ROE.")
    else:
        ev = market_cap_b + net_debt_b if market_cap_b > 0 else 0
        ps  = market_cap_b / rev_b   if rev_b   and rev_b > 0   else None
        pcf = market_cap_b / cfo_b   if cfo_b   and cfo_b > 0   else None
        ev_ebitda = ev / ebitda_b    if ebitda_b and ebitda_b > 0 else None

        m_cols = st.columns(5)
        mult_items = [
            ("P/E",        _fmt(pe_cur, 1, "x"),        ""),
            ("P/B",        _fmt(pb_cur, 2, "x"),        ""),
            ("P/S",        _fmt(ps, 2, "x"),            "~" if ps else ""),
            ("P/CF",       _fmt(pcf, 1, "x") + (" ~" if metrics.get("cfo_is_estimated") else ""), ""),
            ("EV/EBITDA",  _fmt(ev_ebitda, 1, "x") + (" ~" if metrics.get("ebitda_is_estimated") else ""), ""),
        ]
        for col, (label, val, note) in zip(m_cols, mult_items):
            with col:
                st.markdown(f"""
                <div class="kpi-card">
                  <div class="kpi-label">{label}</div>
                  <div class="kpi-value neu">{val}</div>
                  <div style="font-size:10px;color:#5a5a72">{note}</div>
                </div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        detail_rows = [
            ("Doanh thu thuần", f"{_fmt(rev_b, 0)} tỷ"),
            ("CFO (LCTT HĐKD)", f"{_fmt(cfo_b, 0)} tỷ" + (" *ước tính*" if metrics.get("cfo_is_estimated") else "")),
            ("EBITDA", f"{_fmt(ebitda_b, 0)} tỷ" + (" *ước tính*" if metrics.get("ebitda_is_estimated") else "")),
            ("Net Debt", f"{_fmt(net_debt_b, 0)} tỷ"),
            ("Enterprise Value", f"{_fmt(ev, 0)} tỷ"),
            ("Vốn hóa", f"{_fmt(market_cap_b, 0)} tỷ"),
        ]
        d1, d2 = st.columns(2)
        for i, (k, v) in enumerate(detail_rows):
            col = d1 if i % 2 == 0 else d2
            with col:
                st.markdown(f"""
                <div style="display:flex;justify-content:space-between;padding:8px 0;
                     border-bottom:1px solid rgba(139,92,246,.08)">
                  <span style="color:#8b8ba7;font-size:13px">{k}</span>
                  <span style="color:#f0f0ff;font-weight:600;font-size:13px">{v}</span>
                </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 · DCF / Graham / DDM
# ─────────────────────────────────────────────────────────────────────────────
with tabs[3]:
    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown('<div class="card-title">DCF — FCFF 3 kịch bản</div>', unsafe_allow_html=True)
        if dcf_scenarios:
            for sc_name, sc_data in dcf_scenarios.items():
                if not isinstance(sc_data, dict):
                    continue
                tp_dcf = sc_data.get("target_price") or sc_data.get("value") or sc_data.get("fair_value")
                if not tp_dcf:
                    continue
                ups = (tp_dcf / current_price - 1) * 100 if current_price > 0 else 0
                u_color = GREEN if ups >= 0 else RED
                st.markdown(f"""
                <div style="display:flex;justify-content:space-between;align-items:center;
                     padding:10px 0;border-bottom:1px solid rgba(139,92,246,.08)">
                  <span style="color:#8b8ba7;font-size:13px;text-transform:capitalize">{sc_name}</span>
                  <div style="text-align:right">
                    <div style="color:#f0f0ff;font-weight:700">{_fmt(tp_dcf, 0)}đ</div>
                    <div style="color:{u_color};font-size:11px">{_arrow(ups)} {_fmt(abs(ups), 1, '%')}</div>
                  </div>
                </div>""", unsafe_allow_html=True)
            if rev_dcf_g is not None:
                st.markdown(f"""
                <div style="margin-top:14px;padding:12px;background:rgba(251,191,36,.08);
                     border:1px solid rgba(251,191,36,.2);border-radius:10px">
                  <div style="font-size:11px;color:#5a5a72">Reverse DCF — Tăng trưởng ẩn</div>
                  <div style="font-size:20px;font-weight:800;color:#fbbf24">{_fmt(rev_dcf_g, 1, '%')}/năm</div>
                </div>""", unsafe_allow_html=True)
        else:
            st.info("Chưa đủ dữ liệu FCFF để tính DCF")

    with c2:
        st.markdown('<div class="card-title">Graham Number</div><div class="card-sub">√(22.5 × EPS × BVPS)</div>', unsafe_allow_html=True)
        if graham_value and graham_value > 0:
            g_upside = (graham_value / current_price - 1) * 100
            g_color  = GREEN if g_upside >= 0 else RED
            st.markdown(f"""
            <div style="text-align:center;padding:20px 0">
              <div style="font-size:42px;font-weight:900;color:#a855f7">{_fmt(graham_value, 0)}<span style="font-size:16px;color:#8b8ba7">đ</span></div>
              <div style="color:{g_color};font-weight:700;margin-top:8px">{_arrow(g_upside)} {_fmt(abs(g_upside), 1, '%')} so với giá hiện tại</div>
            </div>""", unsafe_allow_html=True)
            for label, val in [("EPS", f"{_fmt(eps_latest, 0)}đ"), ("BVPS", f"{_fmt(bvps_latest, 0)}đ"),
                                ("Giá hiện tại", f"{_fmt(current_price, 0)}đ")]:
                st.markdown(f"""
                <div style="display:flex;justify-content:space-between;padding:8px 0;
                     border-bottom:1px solid rgba(139,92,246,.08)">
                  <span style="color:#8b8ba7;font-size:13px">{label}</span>
                  <span style="color:#f0f0ff;font-weight:600">{val}</span>
                </div>""", unsafe_allow_html=True)
        else:
            st.info("Chưa đủ dữ liệu EPS/BVPS để tính Graham Number")

    with c3:
        st.markdown('<div class="card-title">DDM (Gordon Growth)</div>', unsafe_allow_html=True)
        if ddm_value and ddm_value > 0:
            d_upside = (ddm_value / current_price - 1) * 100
            d_color  = GREEN if d_upside >= 0 else RED
            st.markdown(f"""
            <div style="text-align:center;padding:20px 0">
              <div style="font-size:42px;font-weight:900;color:#06b6d4">{_fmt(ddm_value, 0)}<span style="font-size:16px;color:#8b8ba7">đ</span></div>
              <div style="color:{d_color};font-weight:700;margin-top:8px">{_arrow(d_upside)} {_fmt(abs(d_upside), 1, '%')}</div>
            </div>""", unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:11px;color:#5a5a72;margin-top:8px">{ddm_note}</div>', unsafe_allow_html=True)
            for label, val in [("DPS gần nhất", f"{_fmt(dps_latest, 0)}đ"), ("Div Yield", f"{_fmt(div_yield, 2, '%')}")]:
                st.markdown(f"""
                <div style="display:flex;justify-content:space-between;padding:8px 0;
                     border-bottom:1px solid rgba(139,92,246,.08)">
                  <span style="color:#8b8ba7;font-size:13px">{label}</span>
                  <span style="color:#f0f0ff;font-weight:600">{val}</span>
                </div>""", unsafe_allow_html=True)
        else:
            st.info("Cổ phiếu không có / ít chi trả cổ tức — DDM không phù hợp")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 · DuPont
# ─────────────────────────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown('<div class="sec-hdr"><span class="tag tag-amber">DuPont</span>ROE = Biên × Vòng quay × Đòn bẩy</div>', unsafe_allow_html=True)

    if df_dupont is not None and not df_dupont.empty:
        c1, c2 = st.columns([2, 1])
        with c1:
            dup_years  = [str(y) for y in df_dupont.index] if df_dupont.index.name != 'year' else [str(y) for y in df_dupont.index]
            margin_col  = next((c for c in df_dupont.columns if 'margin' in c.lower() or 'biên' in c.lower()), None)
            turnover_col = next((c for c in df_dupont.columns if 'turn' in c.lower() or 'vòng' in c.lower()), None)
            leverage_col = next((c for c in df_dupont.columns if 'lever' in c.lower() or 'đòn' in c.lower()), None)

            fig_dup = go.Figure()
            if margin_col:
                fig_dup.add_trace(go.Bar(name="Biên LN (%)", x=dup_years,
                                         y=pd.to_numeric(df_dupont[margin_col], errors='coerce').values,
                                         marker_color=PINK))
            if turnover_col:
                fig_dup.add_trace(go.Bar(name="Vòng quay TS (%)", x=dup_years,
                                         y=pd.to_numeric(df_dupont[turnover_col], errors='coerce').values,
                                         marker_color=CYAN))
            if leverage_col:
                fig_dup.add_trace(go.Bar(name="Đòn bẩy (%)", x=dup_years,
                                         y=pd.to_numeric(df_dupont[leverage_col], errors='coerce').values,
                                         marker_color=PURPLE))
            _chart(fig_dup, 260, barmode="group",
                   yaxis=dict(gridcolor=CHART_GRID, ticksuffix="%", tickfont=dict(color=CHART_TEXT)),
                   xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT)))
            st.plotly_chart(fig_dup, use_container_width=True, config={"displayModeBar": False})

        with c2:
            st.markdown('<div class="card-title">Phân tích ROE gần nhất</div>', unsafe_allow_html=True)
            latest_row = df_dupont.iloc[-1] if not df_dupont.empty else pd.Series()
            for col_key, label in [(margin_col, "Biên LN"), (turnover_col, "Vòng quay TS"),
                                   (leverage_col, "Đòn bẩy tài chính")]:
                if col_key and col_key in latest_row:
                    val = latest_row[col_key]
                    try: val = float(val)
                    except: val = None
                    st.markdown(f"""
                    <div style="padding:10px 0;border-bottom:1px solid rgba(139,92,246,.08)">
                      <div style="font-size:10px;color:#5a5a72;text-transform:uppercase">{label}</div>
                      <div style="font-size:18px;font-weight:800;color:{PURPLE}">{_fmt(val, 2, '%') if val else '—'}</div>
                    </div>""", unsafe_allow_html=True)
            st.markdown(f"""
            <div style="padding:10px 0">
              <div style="font-size:10px;color:#5a5a72;text-transform:uppercase">ROE tổng hợp</div>
              <div style="font-size:24px;font-weight:900;color:{GREEN}">{_fmt(roe_latest, 1, '%')}</div>
            </div>""", unsafe_allow_html=True)

        with st.expander("📋 Bảng DuPont chi tiết"):
            st.dataframe(df_dupont, use_container_width=True)
    else:
        st.info("Chưa đủ dữ liệu để tính DuPont")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 · Insights
# ─────────────────────────────────────────────────────────────────────────────
with tabs[5]:
    st.markdown('<div class="sec-hdr"><span class="tag tag-cyan">Key Insights</span>Tổng hợp phân tích</div>', unsafe_allow_html=True)

    strengths, risks, catalysts = [], [], []

    if roe_latest and roe_latest > 15:
        strengths.append(f"ROE {_fmt(roe_latest, 1, '%')} — vượt ngưỡng hiệu quả 15%")
    if roe_latest and roe_latest < 10:
        risks.append(f"ROE {_fmt(roe_latest, 1, '%')} — dưới ngưỡng kỳ vọng")
    if rev_cagr and rev_cagr > 10:
        strengths.append(f"Tăng trưởng doanh thu CAGR 5N: {_fmt(rev_cagr, 1, '%')} — tốt")
    if rev_cagr and rev_cagr < 0:
        risks.append(f"Doanh thu suy giảm CAGR: {_fmt(rev_cagr, 1, '%')}")
    if pe_cur and 0 < pe_cur < 12:
        strengths.append(f"P/E {_fmt(pe_cur, 1, 'x')} — định giá hấp dẫn (thấp)")
    if pe_cur and pe_cur > 30:
        risks.append(f"P/E {_fmt(pe_cur, 1, 'x')} — định giá cao, rủi ro correction")
    if pb_cur and 0 < pb_cur < 1:
        strengths.append(f"P/B {_fmt(pb_cur, 2, 'x')} — giao dịch dưới giá trị sổ sách")
    if upside_pct and upside_pct > 15:
        catalysts.append(f"Target price {_fmt(tp_base, 0)}đ — upside {_fmt(upside_pct, 1, '%')}")
    if not strengths: strengths.append("Chưa đủ dữ liệu để tự động tổng hợp điểm mạnh")
    if not risks: risks.append("Chưa đủ dữ liệu để tự động phát hiện rủi ro")
    if not catalysts: catalysts.append("Analyst target chưa có hoặc upside <15%")

    ic1, ic2, ic3 = st.columns(3)
    for col, title, ico, color, items in [
        (ic1, "Điểm mạnh", "💪", GREEN, strengths),
        (ic2, "Rủi ro",    "⚠️", RED,   risks),
        (ic3, "Catalyst",  "🚀", CYAN,  catalysts),
    ]:
        with col:
            items_html = "".join(f'<div style="margin:6px 0;font-size:13px;color:#c0c0d8">• {i}</div>' for i in items)
            st.markdown(f"""
            <div class="insight-box">
              <div style="font-size:24px;margin-bottom:8px">{ico}</div>
              <div style="font-size:15px;font-weight:700;color:{color};margin-bottom:10px">{title}</div>
              {items_html}
            </div>""", unsafe_allow_html=True)

    # Summary bar chart (all valuation methods)
    st.markdown('<div class="sec-hdr" style="margin-top:28px"><span class="tag tag-purple">Tổng hợp</span>Biểu đồ đa phương pháp định giá</div>', unsafe_allow_html=True)
    if val_methods:
        method_names, method_vals = [], []
        for k, v in val_methods.items():
            tp = None
            if isinstance(v, dict):
                tp = v.get("target_price") or v.get("value") or v.get("fair_value")
            elif isinstance(v, (int, float)) and v > 0:
                tp = v
            if tp and tp > 0:
                method_names.append(k.upper().replace("_", " "))
                method_vals.append(tp)

        if method_names:
            bar_colors = [GREEN if v > current_price else RED for v in method_vals]
            fig_sum = go.Figure()
            fig_sum.add_trace(go.Bar(x=method_names, y=method_vals,
                                     marker_color=bar_colors, opacity=.85))
            if current_price > 0:
                fig_sum.add_hline(y=current_price, line=dict(color=AMBER, width=2, dash="dash"),
                                  annotation_text=f"Giá hiện tại {_fmt(current_price,0)}đ",
                                  annotation_font=dict(color=AMBER, size=10),
                                  annotation_position="top right")
            _chart(fig_sum, 300,
                   yaxis=dict(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT)),
                   xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT, size=9)))
            st.plotly_chart(fig_sum, use_container_width=True, config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
# TAB 7 · Dự phóng
# ─────────────────────────────────────────────────────────────────────────────
with tabs[6]:
    st.markdown('<div class="sec-hdr"><span class="tag tag-purple">Dự phóng</span>Broker Consensus Forecast</div>', unsafe_allow_html=True)
    st.info("🔮 Dự phóng từ broker được hiển thị khi có dữ liệu từ Vietstock/CafeF analyst reports. "
            "Hiện tại pipeline tự động tính từ CAGR lịch sử.")

    if not rev_s.empty and len(rev_s) >= 2:
        last_rev = rev_s.iloc[-1]
        last_np  = np_s.iloc[-1] if not np_s.empty else None
        last_year = rev_s.index[-1]

        def _project(base, cagr_pct, n=2):
            if base is None or cagr_pct is None: return []
            g = cagr_pct / 100
            return [round(base * ((1 + g) ** i), 0) for i in range(1, n + 1)]

        fcast_rev = _project(last_rev, rev_cagr)
        fcast_np  = _project(last_np, np_cagr)
        fcast_yrs = [str(last_year + i) for i in range(1, 3)]

        cf1, cf2 = st.columns(2)
        with cf1:
            fig_fc = go.Figure()
            all_yrs = [str(y) for y in rev_s.index] + fcast_yrs
            all_rev = list(rev_s.values) + fcast_rev
            all_np  = list(np_s.values) + fcast_np if not np_s.empty and fcast_np else []

            n_hist = len(rev_s)
            fig_fc.add_trace(go.Bar(name="Doanh thu thực tế",
                                    x=all_yrs[:n_hist], y=all_rev[:n_hist],
                                    marker_color=PURPLE, opacity=.85))
            if fcast_rev:
                fig_fc.add_trace(go.Bar(name="Dự phóng doanh thu",
                                        x=fcast_yrs, y=fcast_rev,
                                        marker_color=PURPLE, opacity=.4,
                                        marker_pattern_shape="/"))
            _chart(fig_fc, 260,
                   yaxis=dict(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT)),
                   xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=CHART_TEXT)))
            st.plotly_chart(fig_fc, use_container_width=True, config={"displayModeBar": False})

        with cf2:
            st.markdown('<div class="card-title">Bảng dự phóng</div>', unsafe_allow_html=True)
            for i, yr in enumerate(fcast_yrs):
                rev_f = fcast_rev[i] if i < len(fcast_rev) else None
                np_f  = fcast_np[i]  if i < len(fcast_np) else None
                st.markdown(f"""
                <div style="padding:10px 0;border-bottom:1px solid rgba(139,92,246,.08)">
                  <div style="color:#a855f7;font-weight:700;margin-bottom:4px">{yr}E</div>
                  <div style="display:flex;gap:24px">
                    <div><div style="font-size:10px;color:#5a5a72">Doanh thu</div>
                         <div style="font-size:14px;font-weight:700;color:#f0f0ff">{_fmt(rev_f, 0)} tỷ</div></div>
                    <div><div style="font-size:10px;color:#5a5a72">LNST</div>
                         <div style="font-size:14px;font-weight:700;color:#10d98a">{_fmt(np_f, 0)} tỷ</div></div>
                  </div>
                </div>""", unsafe_allow_html=True)
            st.markdown(f"""
            <div style="margin-top:12px;font-size:11px;color:#5a5a72">
              * Dựa trên CAGR lịch sử: DT {_fmt(rev_cagr,1,'%')}/năm · LNST {_fmt(np_cagr,1,'%')}/năm
            </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 8 · Technical
# ─────────────────────────────────────────────────────────────────────────────
with tabs[7]:
    st.markdown('<div class="sec-hdr"><span class="tag tag-amber">Technical</span>Phân tích kỹ thuật</div>', unsafe_allow_html=True)

    if df_price is not None and not df_price.empty and 'close_vnd' in df_price.columns:
        # Price chart
        fig_price = go.Figure()
        fig_price.add_trace(go.Scatter(
            name="Giá đóng cửa", x=df_price['time'], y=df_price['close_vnd'],
            line=dict(color=PURPLE, width=2), fill="tozeroy",
            fillcolor="rgba(168,85,247,0.08)"))
        if 'MA20' in df_price.columns:
            fig_price.add_trace(go.Scatter(
                name="MA20", x=df_price['time'], y=df_price['MA20'],
                line=dict(color=AMBER, width=1.5, dash="dot")))
        _chart(fig_price, 300,
               yaxis=dict(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT)),
               xaxis=dict(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT),
                          rangeslider=dict(visible=False)))
        st.plotly_chart(fig_price, use_container_width=True, config={"displayModeBar": True})

        # Volume
        if 'volume' in df_price.columns:
            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(
                name="Khối lượng", x=df_price['time'], y=df_price['volume'],
                marker_color=CYAN, opacity=.7))
            if 'volume_ma20' in df_price.columns:
                fig_vol.add_trace(go.Scatter(
                    name="MA20 KL", x=df_price['time'], y=df_price['volume_ma20'],
                    line=dict(color=AMBER, width=1.5)))
            _chart(fig_vol, 160,
                   yaxis=dict(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT)),
                   xaxis=dict(gridcolor=CHART_GRID, tickfont=dict(color=CHART_TEXT)))
            st.plotly_chart(fig_vol, use_container_width=True, config={"displayModeBar": False})

    # Technical indicators
    t1, t2, t3, t4 = st.columns(4)
    ma20 = technical_summary.get("ma20")
    trend = technical_summary.get("trend_signal", "—")
    vol_vs_avg = technical_summary.get("volume_vs_avg_pct", 0)
    avg_vol = technical_summary.get("avg_volume_20d", 0)

    for col, (label, val, cls) in zip([t1, t2, t3, t4], [
        ("Xu hướng",     trend,                             "pos" if "khả" in trend.lower() else "neg"),
        ("MA20",         f"{_fmt(ma20, 0)}đ" if ma20 else "—", _pct_color(current_price - ma20 if ma20 else None)),
        ("KL so MA20",   f"{_arrow(vol_vs_avg)} {_fmt(abs(vol_vs_avg), 1, '%')}", _pct_color(vol_vs_avg)),
        ("KL TB 20D",    f"{int(avg_vol / 1000)}K" if avg_vol > 0 else "—", "neu"),
    ]):
        with col:
            st.markdown(f"""
            <div class="kpi-card">
              <div class="kpi-label">{label}</div>
              <div class="kpi-value {cls}" style="font-size:15px">{val}</div>
            </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 9 · Tin tức
# ─────────────────────────────────────────────────────────────────────────────
with tabs[8]:
    st.markdown(f'<div class="sec-hdr"><span class="tag tag-cyan">Tin tức</span>Tin mới nhất về {ticker_input}</div>', unsafe_allow_html=True)

    if news_list:
        for news in news_list[:15]:
            title    = news.get("title", "—")
            source   = news.get("source", "—")
            url      = news.get("url", "#")
            pub_date = news.get("pub_date", "—")
            st.markdown(f"""
            <div class="news-row">
              <a href="{url}" target="_blank" style="color:#c4b5fd;font-weight:600;
                 font-size:13px;text-decoration:none;line-height:1.5">{title}</a>
              <div style="margin-top:4px;font-size:11px;color:#5a5a72">
                🗞️ {source} &nbsp;·&nbsp; 🕒 {pub_date}
              </div>
            </div>""", unsafe_allow_html=True)
    else:
        st.info("Chưa có tin tức")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 10 · Báo cáo phân tích
# ─────────────────────────────────────────────────────────────────────────────
with tabs[9]:
    st.markdown('<div class="sec-hdr"><span class="tag tag-purple">Báo cáo</span>Analyst Reports</div>', unsafe_allow_html=True)
    st.info("📋 Tính năng báo cáo analyst đang phát triển. "
            "Dữ liệu sẽ được lấy từ Vietstock, CafeF, và các công ty chứng khoán.")
    st.markdown("""
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px">
      <a href="https://www.vietstock.vn" target="_blank" style="text-decoration:none">
        <div class="kpi-card" style="cursor:pointer">
          <div style="font-size:20px;margin-bottom:6px">📊</div>
          <div class="kpi-label">Vietstock</div>
          <div style="color:#a855f7;font-size:12px;margin-top:4px">Xem báo cáo →</div>
        </div>
      </a>
      <a href="https://cafef.vn" target="_blank" style="text-decoration:none">
        <div class="kpi-card" style="cursor:pointer">
          <div style="font-size:20px;margin-bottom:6px">📰</div>
          <div class="kpi-label">CafeF</div>
          <div style="color:#a855f7;font-size:12px;margin-top:4px">Xem tin tức →</div>
        </div>
      </a>
      <a href="https://www.dnse.com.vn" target="_blank" style="text-decoration:none">
        <div class="kpi-card" style="cursor:pointer">
          <div style="font-size:20px;margin-bottom:6px">🏢</div>
          <div class="kpi-label">DNSE Research</div>
          <div style="color:#a855f7;font-size:12px;margin-top:4px">Xem phân tích →</div>
        </div>
      </a>
    </div>""", unsafe_allow_html=True)


# ─── Disclaimer ──────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="disclaimer" style="margin-top:40px">
  ⚠️ <strong>Khuyến cáo quan trọng:</strong> Dashboard này chỉ mang tính chất tham khảo,
  không phải khuyến nghị đầu tư chính thức. Dữ liệu được lấy tự động từ {source_used} / CafeF / DNSE
  và có thể có sai lệch. Nhà đầu tư nên tự nghiên cứu và tham khảo chuyên gia trước khi đưa ra
  quyết định đầu tư. Quá khứ không đảm bảo cho tương lai.
</div>
<footer style="margin-top:32px;padding-top:16px;border-top:1px solid rgba(139,92,246,.15);
  color:#5a5a72;font-size:11px;text-align:center;line-height:2">
  🧚 <strong style="color:#a855f7">Tien-Nhu · Cô Tiên</strong> · Phân tích cổ phiếu Việt Nam<br>
  Hỗ trợ 1,500+ mã HOSE · HNX · UPCOM · Cập nhật tự động mỗi 30 phút
</footer>
""", unsafe_allow_html=True)
