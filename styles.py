import streamlit as st

def apply_premium_fintech_theme():
    """
    CSS "độ" giao diện Streamlit thành Fintech Dark
    (Glassmorphism + Neon gradient tím-hồng).

    FIX: Thanh đen trên cùng (Share/☆/✏/GitHub icons) —
    selector cũ chỉ cover stHeader, bỏ sót stToolbar + stToolbarActions
    và iframe wrapper. Thêm đủ các selector bên dưới để toàn bộ top bar
    đồng màu với nền #0a0a14 của dashboard.
    """
    st.markdown("""
<style>

/* ── Nền Dark + Radial Gradient (phủ full màn hình) ─────────────────── */
.stApp {
    background-color: #0a0a14 !important;
    background-image: radial-gradient(ellipse 140% 55% at 50% 0%, #3d0f6e 0%, #1a0933 40%, #0a0a14 72%) !important;
    color: #f1f1f6;
    font-family: 'Inter', sans-serif;
}

/* ── Ẩn sidebar hoàn toàn — layout full-width ────────────────────────── */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] {
    display: none !important;
}

/* ── Content full-width ──────────────────────────────────────────────── */
.main .block-container {
    max-width: 1600px !important;
    padding-left: 3rem !important;
    padding-right: 3rem !important;
    padding-top: 1.5rem !important;
}

/* ── Glassmorphism KPI cards ─────────────────────────────────────────── */
div[data-testid="metric-container"] {
    background: rgba(255, 255, 255, 0.02);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    padding: 20px;
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4);
    transition: transform 0.2s ease, border 0.2s ease;
}
div[data-testid="metric-container"]:hover {
    transform: translateY(-3px);
    border: 1px solid rgba(168, 85, 247, 0.4);
}

/* ── Neon gradient tiêu đề ───────────────────────────────────────────── */
h1, h2, h3 {
    background: linear-gradient(90deg, #a855f7 0%, #ec4899 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

/* ── Tabs ────────────────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 10px;
    background-color: transparent;
}
.stTabs [data-baseweb="tab"] {
    background-color: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.05);
    padding: 10px 20px;
    border-radius: 8px;
    color: #8b5cf6;
}
.stTabs [aria-selected="true"] {
    background: linear-gradient(90deg, #a855f7, #ec4899) !important;
    color: white !important;
    font-weight: bold;
}

/* ── DataFrame border ────────────────────────────────────────────────── */
.stDataFrame {
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 10px;
}

/* ══════════════════════════════════════════════════════════════════════
   FIX: THANH ĐEN TRÊN CÙNG (Share / ☆ / ✏ / GitHub icons)
   ══════════════════════════════════════════════════════════════════════ */

[data-testid="stHeader"],
header[data-testid="stHeader"] {
    background-color: #0a0a14 !important;
    background-image: none !important;
    border-bottom: none !important;
    box-shadow: none !important;
}

[data-testid="stToolbar"],
[data-testid="stHeader"] > div,
[data-testid="stHeader"] > div > div {
    background-color: #0a0a14 !important;
    background-image: none !important;
}

[data-testid="stToolbarActions"],
[data-testid="stToolbarActionButtonContainer"] {
    background-color: transparent !important;
}

[data-testid="stDecoration"] {
    display: none !important;
}

.appview-container > section:first-child {
    background-color: #0a0a14 !important;
}

.main > div:first-child {
    background-color: #0a0a14 !important;
}

:root {
    --background-color: #0a0a14;
}

</style>
""", unsafe_allow_html=True)
