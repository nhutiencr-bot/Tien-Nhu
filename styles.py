import streamlit as st
def apply_premium_fintech_theme():
    """
    File này đóng vai trò như một thư viện CSS dùng để "độ" giao diện Streamlit
    thành phong cách Fintech hiện đại (Dark theme, Glassmorphism, Neon gradient)
    giống hệt bản gốc của tác giả.
    """
    st.markdown("""
    <style>
        /* Nền Dark Theme kết hợp Radial Gradient */
        .stApp {
            background-color: #0a0a14;
            background-image: radial-gradient(circle at 50% 0%, #1a0933 0%, #0a0a14 65%);
            color: #f1f1f6;
            font-family: 'Inter', sans-serif;
        }
        
        /* Hiệu ứng Kính mờ (Glassmorphism) cho thẻ KPI */
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
        
        /* Gradient Neon Tím-Hồng cho các Tiêu đề */
        h1, h2, h3 {
            background: linear-gradient(90deg, #a855f7 0%, #ec4899 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        /* Style lại các Tab để giống với nút bấm hiện đại */
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
        
        /* Chỉnh lại bảng dữ liệu DataFrame */
        .stDataFrame {
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
        }

        /* Đồng bộ màu thanh Header mặc định của Streamlit (chứa nút Stop/Share...)
           với nền tối của dashboard. Dùng màu ĐẶC (không gradient) vì thanh quá
           mỏng khiến radial-gradient bị bóp méo thành vệt loang màu xấu. */
        [data-testid="stHeader"] {
            background-color: #0a0a14 !important;
            background-image: none !important;
        }

        /* Ẩn hẳn vạch "đang chạy" mỏng trên cùng cho liền mạch với nền,
           thay vì cố tô màu (vạch quá mỏng nên tô gradient cũng không đẹp) */
        [data-testid="stDecoration"] {
            display: none !important;
        }

        /* Toolbar (Manage app, các icon) ở góc cũng đồng bộ nền tối */
        [data-testid="stToolbar"] {
            background-color: transparent !important;
        }
    </style>
    """, unsafe_allow_html=True)
