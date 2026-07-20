"""
app_optimized_example.py — Cách tích hợp vào app.py hiện tại
=============================================================
Đây là MẪUTHAM KHẢO. Copy những phần phù hợp vào app.py của bạn.
Không cần thay toàn bộ — chỉ thêm từng phần một.
"""

import streamlit as st

# ── BƯỚC 1: Config trang — PHẢI là lệnh Streamlit đầu tiên ──────────────────
st.set_page_config(
    page_title="Tien-Nhu Research",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
    # "wide" giúp bố cục thoáng hơn, ít scroll hơn
)

# ── BƯỚC 2: Import module tối ưu ─────────────────────────────────────────────
import performance as perf
import ui_optimized as ui

# ── BƯỚC 3: Khởi tạo session state 1 lần ────────────────────────────────────
perf.init_session_defaults({
    "ticker":       "",
    "nav_tab":      "📈 Giá & KL",
    "chart_mode":   "Đường",
    "page":         1,
    "_last_refresh": "",
})

# ── BƯỚC 4: Inject CSS (1 lần) ───────────────────────────────────────────────
ui.inject_css()

# ── BƯỚC 5: Render sidebar → nhận ticker & tab user chọn ────────────────────
ticker, tab = ui.render_sidebar()

# ── BƯỚC 6: Render nội dung chính ────────────────────────────────────────────
ui.render_main(ticker, tab)

# ── BƯỚC 7: Giải phóng bộ nhớ cuối mỗi run ──────────────────────────────────
perf.release_memory()
