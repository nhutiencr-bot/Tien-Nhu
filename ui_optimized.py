"""
ui_optimized.py — UI/UX nhẹ & nhanh cho Tien-Nhu Streamlit App
================================================================
Nguyên tắc:
  - Không render chart nếu user chưa chọn ticker
  - Dùng st.expander cho nội dung phụ (ẩn mặc định)
  - Dùng st.tabs thay vì nhiều section cùng lúc (render lazy)
  - Metric cards thuần HTML/CSS thay vì Plotly gauge (nhẹ hơn 10x)
  - Chỉ vẽ chart khi có dữ liệu hợp lệ
"""

import streamlit as st
import pandas as pd
from typing import Optional
import performance as perf   # file performance.py cùng thư mục


# ─────────────────────────────────────────────
# CSS NỀN — inject 1 lần, nhẹ, không dùng JS
# ─────────────────────────────────────────────

def inject_css():
    """Gọi 1 lần đầu app.py. Style toàn app."""
    st.markdown("""
    <style>
    /* ===== FONT & NỀN ===== */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* ===== METRIC CARD ===== */
    .metric-card {
        background: #0e1117;
        border: 1px solid #1e2530;
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 8px;
        transition: border-color 0.2s;
    }
    .metric-card:hover { border-color: #3a7bd5; }
    .metric-label {
        font-size: 11px;
        color: #8892a4;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 4px;
    }
    .metric-value {
        font-size: 22px;
        font-weight: 700;
        color: #ffffff;
        line-height: 1.2;
    }
    .metric-delta-up   { color: #00B050; font-size: 13px; }
    .metric-delta-down { color: #FF4444; font-size: 13px; }

    /* ===== TICKER BADGE ===== */
    .ticker-badge {
        display: inline-block;
        background: #1e2d45;
        color: #5ba3f5;
        font-weight: 700;
        font-size: 13px;
        padding: 3px 10px;
        border-radius: 6px;
        letter-spacing: 0.05em;
    }

    /* ===== TABLE COMPACT ===== */
    .compact-table { font-size: 13px; }
    .compact-table th {
        background: #161b22 !important;
        color: #8892a4 !important;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }

    /* ===== SIDEBAR ===== */
    [data-testid="stSidebar"] {
        background: #0a0d14;
        border-right: 1px solid #1e2530;
    }

    /* ===== CHIA CỘT DIVIDER ===== */
    hr { border-color: #1e2530; margin: 12px 0; }

    /* ===== SPINNER ===== */
    .stSpinner > div { border-top-color: #3a7bd5 !important; }

    /* ===== ẨN WATERMARK STREAMLIT (Community Cloud) ===== */
    #MainMenu, footer, header { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────
# METRIC CARDS — thuần HTML, không dùng Plotly
# ─────────────────────────────────────────────

def metric_card(label: str, value: str, delta: Optional[float] = None, suffix: str = ""):
    """
    Card chỉ số nhẹ. Thay thế cho st.metric() để kiểm soát style.
    """
    delta_html = ""
    if delta is not None:
        sign = "+" if delta >= 0 else ""
        cls  = "metric-delta-up" if delta >= 0 else "metric-delta-down"
        arrow = "▲" if delta >= 0 else "▼"
        delta_html = f'<div class="{cls}">{arrow} {sign}{delta:.2f}%</div>'

    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}{' ' + suffix if suffix else ''}</div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)


def ticker_badge(ticker: str):
    st.markdown(f'<span class="ticker-badge">{ticker}</span>', unsafe_allow_html=True)


# ─────────────────────────────────────────────
# SIDEBAR — chọn ticker, tối ưu selectbox
# ─────────────────────────────────────────────

def render_sidebar() -> tuple[str, str]:
    """
    Sidebar tối ưu: chỉ load ticker list 1 lần (cached_resource).
    Trả về (ticker_chon, tab_chon).
    """
    with st.sidebar:
        st.markdown("### 📊 Tien-Nhu Research")
        st.caption("Phân tích cổ phiếu Việt Nam")
        st.divider()

        # Ticker list — chỉ gọi 1 lần nhờ @st.cache_resource
        all_tickers = perf.get_ticker_list()

        # Gợi ý nhanh (không cần gọi API)
        popular = ["VCB", "HPG", "FPT", "TCB", "MWG", "VHM", "MSN", "SSI"]
        quick = st.selectbox("⚡ Cổ phiếu phổ biến", [""] + popular,
                             format_func=lambda x: "Chọn nhanh..." if x == "" else x,
                             key="quick_pick")

        # Tìm kiếm ticker đầy đủ
        search = st.text_input("🔍 Tìm mã CK", value=quick or "",
                               placeholder="VD: HPG, FPT...",
                               key="ticker_search").upper().strip()

        # Validate
        ticker = search if search in all_tickers else None
        if search and not ticker:
            st.error(f"❌ Không tìm thấy mã '{search}'")

        st.divider()

        # Chọn tab phân tích
        tab = st.radio(
            "Xem",
            ["📈 Giá & KL", "📋 BCTC", "💰 Định giá", "📰 Tin tức"],
            key="nav_tab"
        )

        perf.render_performance_sidebar()

    return (ticker or ""), tab


# ─────────────────────────────────────────────
# CHART — chỉ render khi cần, dùng plotly nhẹ
# ─────────────────────────────────────────────

def render_price_chart(df: pd.DataFrame, ticker: str):
    """
    Vẽ chart giá tối ưu:
    - Chỉ 250 phiên gần nhất (không cần 5 năm để hiển thị)
    - Dùng plotly go.Scatter (nhẹ hơn OHLC đầy đủ khi chưa zoom)
    - Không vẽ nếu df rỗng
    """
    if df is None or df.empty:
        st.info("Không có dữ liệu giá để hiển thị.")
        return

    # Lấy tối đa 250 phiên gần nhất — đủ xem xu hướng, tiết kiệm RAM
    df_plot = df.tail(250).copy()

    try:
        px = perf.lazy_import("plotly.express")

        # Chọn chế độ xem
        mode = st.radio("Chế độ", ["Đường", "Nến (OHLC)"], horizontal=True, key="chart_mode")

        if mode == "Đường":
            price_col = next((c for c in ["close", "Close", "closePrice"] if c in df_plot.columns), None)
            date_col  = next((c for c in ["time", "date", "tradingDate"] if c in df_plot.columns), None)

            if price_col and date_col:
                fig = px.line(
                    df_plot, x=date_col, y=price_col,
                    title=f"{ticker} — Giá đóng cửa (250 phiên gần nhất)",
                    template="plotly_dark",
                    color_discrete_sequence=["#3a7bd5"],
                )
                fig.update_layout(
                    height=340,
                    margin=dict(l=10, r=10, t=40, b=10),
                    plot_bgcolor="#0e1117",
                    paper_bgcolor="#0e1117",
                    showlegend=False,
                    xaxis=dict(showgrid=False),
                    yaxis=dict(gridcolor="#1e2530"),
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            else:
                st.warning("Cột giá/ngày không khớp. Kiểm tra lại tên cột DataFrame.")

        else:  # OHLC — chỉ render khi user chọn
            go = perf.lazy_import("plotly.graph_objects")
            date_col = next((c for c in ["time","date","tradingDate"] if c in df_plot.columns), None)
            try:
                fig = go.Figure(data=[go.Candlestick(
                    x=df_plot[date_col],
                    open=df_plot.get("open", df_plot.iloc[:,1]),
                    high=df_plot.get("high", df_plot.iloc[:,2]),
                    low=df_plot.get("low",  df_plot.iloc[:,3]),
                    close=df_plot.get("close", df_plot.iloc[:,4]),
                    increasing_line_color="#00B050",
                    decreasing_line_color="#FF4444",
                )])
                fig.update_layout(
                    height=380, template="plotly_dark",
                    margin=dict(l=10, r=10, t=30, b=10),
                    paper_bgcolor="#0e1117",
                    plot_bgcolor="#0e1117",
                    xaxis_rangeslider_visible=False,  # Tắt rangeslider → nhẹ hơn
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            except Exception:
                st.warning("Không vẽ được nến OHLC với cấu trúc dữ liệu hiện tại.")

    except ImportError:
        st.line_chart(df_plot.set_index(df_plot.columns[0])[df_plot.columns[1]])


# ─────────────────────────────────────────────
# BCTC TABLE — compact, paginated
# ─────────────────────────────────────────────

def render_financial_table(df: pd.DataFrame, title: str = "Báo cáo tài chính"):
    """Hiển thị BCTC gọn gàng, có phân trang nếu nhiều dòng."""
    if df is None or df.empty:
        st.info("Không có dữ liệu báo cáo tài chính.")
        return

    st.subheader(title)

    # Downcast để tiết kiệm bộ nhớ khi hiển thị
    df_show = perf._slim_df(df.copy())

    if len(df_show) > 15:
        df_show = perf.paginate_dataframe(df_show, page_size=15, key=f"fin_{title}")

    st.dataframe(
        df_show,
        use_container_width=True,
        hide_index=True,
    )


# ─────────────────────────────────────────────
# MAIN PAGE LAYOUT — entry point
# ─────────────────────────────────────────────

def render_main(ticker: str, tab: str):
    """
    Render nội dung chính theo tab được chọn.
    Dùng st.tabs để Streamlit lazy-render — chỉ vẽ tab đang xem.
    """
    if not ticker:
        _render_empty_state()
        return

    ticker_badge(ticker)
    st.title(f"Phân tích {ticker}")

    tabs = st.tabs(["📈 Giá & KL", "📋 BCTC", "💰 Định giá", "📰 Tin tức"])

    # ── Tab 0: Giá ──────────────────────────────
    with tabs[0]:
        placeholder = perf.skeleton_placeholder(5, f"Đang tải giá {ticker}...")
        df_price = perf.cached_price(ticker)
        placeholder.empty()

        if df_price is not None and not df_price.empty:
            last_row = df_price.iloc[-1]
            close_col = next((c for c in ["close","Close","closePrice"] if c in df_price.columns), df_price.columns[-1])
            price_val  = last_row[close_col]
            prev_val   = df_price.iloc[-2][close_col] if len(df_price) > 1 else price_val
            pct_change = (price_val - prev_val) / prev_val * 100 if prev_val else 0

            c1, c2, c3 = st.columns(3)
            with c1:
                metric_card("Giá đóng cửa", f"{price_val:,.0f}", pct_change, "VNĐ")
            with c2:
                vol_col = next((c for c in ["volume","Volume","dealVolume"] if c in last_row.index), None)
                if vol_col:
                    metric_card("Khối lượng", f"{last_row[vol_col]:,.0f}")
            with c3:
                metric_card("Số phiên", str(len(df_price)))

            render_price_chart(df_price, ticker)
        else:
            st.warning(f"Không lấy được dữ liệu giá cho {ticker}.")
        
        perf.release_memory()

    # ── Tab 1: BCTC ──────────────────────────────
    with tabs[1]:
        report_type = st.selectbox(
            "Loại báo cáo",
            ["IncomeStatement", "BalanceSheet", "CashFlow"],
            key="report_type"
        )
        with st.spinner("Đang tải BCTC..."):
            df_fin = perf.cached_financial(ticker, report_type)
        render_financial_table(df_fin, report_type)
        perf.release_memory()

    # ── Tab 2: Định giá ──────────────────────────
    with tabs[2]:
        st.info("💡 Module định giá (PE/PB/DCF) — tích hợp từ valuation.py")
        with st.expander("📐 Công thức định giá Graham", expanded=False):
            st.latex(r"V = \sqrt{22.5 \times EPS \times BVPS}")
            st.caption("Graham Number — định giá bảo thủ")

    # ── Tab 3: Tin tức ───────────────────────────
    with tabs[3]:
        st.info("📰 Module tin tức — tích hợp từ News-stock pipeline")
        with st.expander("Xem tin gần nhất", expanded=True):
            st.caption("(Kết nối vào news aggregator pipeline của bạn)")


def _render_empty_state():
    """Màn hình trống khi chưa chọn ticker."""
    st.markdown("""
    <div style="text-align:center; padding: 60px 20px; color: #8892a4;">
        <div style="font-size:48px; margin-bottom:16px">📊</div>
        <h2 style="color:#ffffff; margin-bottom:8px">Chọn mã cổ phiếu</h2>
        <p>Nhập mã CK vào thanh tìm kiếm bên trái để bắt đầu phân tích</p>
    </div>
    """, unsafe_allow_html=True)

    # Gợi ý nhanh ở màn hình chính
    st.subheader("⚡ Xem nhanh thị trường")
    df_market = perf.cached_market_overview()
    if df_market is not None:
        st.dataframe(df_market, use_container_width=True, hide_index=True)
