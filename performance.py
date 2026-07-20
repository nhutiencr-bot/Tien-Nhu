"""
performance.py — Tối ưu hiệu suất cho Tien-Nhu Streamlit App
=============================================================
Tác giả: Claude (hỗ trợ Tien-Nhu)
Mục đích: Giảm RAM, tăng tốc load, tránh re-run không cần thiết
Cách dùng: import performance as perf ở đầu app.py và các module khác
"""

import streamlit as st
import pandas as pd
import time
import gc
import hashlib
import json
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional, Callable, Any


# ─────────────────────────────────────────────
# 1. CACHE LAYER — dữ liệu tài chính
# ─────────────────────────────────────────────

# TTL mặc định (giây) — điều chỉnh theo nhu cầu
_TTL_PRICE    = 5 * 60        # Giá realtime: 5 phút
_TTL_FINANCIAL= 24 * 3600     # BCTC: 24 giờ (ít thay đổi)
_TTL_INDEX    = 3 * 60        # VN-Index: 3 phút
_TTL_NEWS     = 15 * 60       # Tin tức: 15 phút


@st.cache_data(ttl=_TTL_PRICE, show_spinner=False)
def cached_price(ticker: str) -> Optional[pd.DataFrame]:
    """
    Lấy giá cổ phiếu với cache 5 phút.
    Tránh gọi vnstock liên tục mỗi lần user tương tác.
    """
    try:
        from vnstock import stock_historical_data
        df = stock_historical_data(ticker, "2020-01-01", _today(), "1D", "stock")
        return _slim_df(df)
    except Exception as e:
        st.warning(f"⚠️ Không lấy được giá {ticker}: {e}")
        return None


@st.cache_data(ttl=_TTL_FINANCIAL, show_spinner=False)
def cached_financial(ticker: str, report_type: str = "IncomeStatement") -> Optional[pd.DataFrame]:
    """
    Lấy BCTC với cache 24h — dữ liệu không thay đổi trong ngày.
    report_type: 'IncomeStatement' | 'BalanceSheet' | 'CashFlow'
    """
    try:
        from vnstock import financial_report
        df = financial_report(ticker, report_type, "yearly")
        return df
    except Exception as e:
        return None


@st.cache_data(ttl=_TTL_INDEX, show_spinner=False)
def cached_market_overview() -> Optional[pd.DataFrame]:
    """VN-Index, HNX-Index overview — cache 3 phút."""
    try:
        from vnstock import market_top_mover
        return market_top_mover("VN30")
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def get_ticker_list() -> list:
    """
    Danh sách ~1500 ticker — cache_resource (không bao giờ reload,
    tồn tại suốt vòng đời app). Gọi 1 lần duy nhất khi khởi động.
    """
    try:
        from vnstock import listing_companies
        df = listing_companies()
        return sorted(df["ticker"].dropna().tolist())
    except Exception:
        # Fallback: danh sách cứng một số ticker phổ biến
        return ["VCB","BID","CTG","TCB","MBB","VPB","HPG","VHM","VIC","GAS",
                "SAB","MSN","FPT","MWG","VJC","PLX","POW","REE","SSI","VND"]


# ─────────────────────────────────────────────
# 2. LAZY IMPORT — tránh load thư viện nặng khi chưa cần
# ─────────────────────────────────────────────

_import_cache = {}

def lazy_import(module_name: str):
    """
    Chỉ import khi thực sự cần. Giảm cold start time đáng kể.
    Ví dụ: plotly = lazy_import('plotly.express')
    """
    if module_name not in _import_cache:
        import importlib
        _import_cache[module_name] = importlib.import_module(module_name)
    return _import_cache[module_name]


# ─────────────────────────────────────────────
# 3. SESSION STATE MANAGER — tránh re-run không cần
# ─────────────────────────────────────────────

def init_session_defaults(defaults: dict):
    """
    Khởi tạo session state một lần duy nhất.
    Gọi ở đầu app.py: perf.init_session_defaults({...})
    """
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def get_state(key: str, default=None):
    return st.session_state.get(key, default)


def set_state(key: str, value):
    st.session_state[key] = value


def state_changed(key: str, new_value) -> bool:
    """Trả về True nếu giá trị thực sự thay đổi (tránh re-render vô ích)."""
    old = st.session_state.get(f"_prev_{key}")
    if old != new_value:
        st.session_state[f"_prev_{key}"] = new_value
        return True
    return False


# ─────────────────────────────────────────────
# 4. PAGINATION — tránh render 1500 dòng cùng lúc
# ─────────────────────────────────────────────

def paginate_dataframe(df: pd.DataFrame, page_size: int = 20, key: str = "page") -> pd.DataFrame:
    """
    Hiển thị DataFrame theo trang. Gọi thay vì st.dataframe(df) trực tiếp.
    
    Cách dùng:
        page_df = perf.paginate_dataframe(big_df, page_size=25, key="watchlist")
        st.dataframe(page_df)
    """
    total = len(df)
    n_pages = max(1, (total - 1) // page_size + 1)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        page = st.number_input(
            f"Trang (/{n_pages})", min_value=1, max_value=n_pages,
            value=st.session_state.get(key, 1), key=key
        )

    start = (page - 1) * page_size
    end   = min(start + page_size, total)
    st.caption(f"Hiển thị {start+1}–{end} / {total} dòng")
    return df.iloc[start:end]


# ─────────────────────────────────────────────
# 5. MEMORY MANAGEMENT — giảm RAM usage
# ─────────────────────────────────────────────

def _slim_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tự động downcast kiểu dữ liệu để tiết kiệm RAM.
    float64 → float32, int64 → int32 khi có thể.
    """
    if df is None or df.empty:
        return df
    for col in df.select_dtypes(include=["float64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="float")
    for col in df.select_dtypes(include=["int64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="integer")
    return df


def release_memory():
    """Gọi sau khi xử lý xong dữ liệu nặng để giải phóng RAM."""
    gc.collect()


def clear_old_cache():
    """
    Xóa cache Streamlit nếu app đang dùng quá nhiều RAM.
    Có thể gắn vào nút trong sidebar.
    """
    st.cache_data.clear()
    release_memory()
    st.success("✅ Đã xóa cache — app sẽ tải lại dữ liệu mới.")


# ─────────────────────────────────────────────
# 6. LOADING STATES — UX khi đang tải
# ─────────────────────────────────────────────

def with_spinner(label: str = "Đang tải dữ liệu..."):
    """
    Decorator: bọc hàm nặng trong spinner.
    @perf.with_spinner("Đang tải BCTC...")
    def load_data(): ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            with st.spinner(label):
                return fn(*args, **kwargs)
        return wrapper
    return decorator


def skeleton_placeholder(n_rows: int = 5, label: str = ""):
    """
    Hiển thị placeholder khi đang load (giả lập skeleton).
    Gọi trước khi bắt đầu fetch dữ liệu.
    """
    if label:
        st.caption(f"⏳ {label}")
    placeholder = st.empty()
    fake = pd.DataFrame({
        "Ticker": ["---"] * n_rows,
        "Giá":    ["..."] * n_rows,
        "KL":     ["..."] * n_rows,
    })
    placeholder.dataframe(fake, use_container_width=True)
    return placeholder   # caller gọi placeholder.empty() sau khi có data


# ─────────────────────────────────────────────
# 7. BATCH LOADER — load nhiều ticker mà không treo app
# ─────────────────────────────────────────────

def batch_load_tickers(
    tickers: list,
    load_fn: Callable,
    batch_size: int = 10,
    delay_ms: int = 200,
    progress_label: str = "Đang tải dữ liệu..."
) -> dict:
    """
    Load danh sách ticker theo batch nhỏ, có progress bar.
    Tránh gọi API 1500 lần cùng lúc → crash RAM / timeout.

    Cách dùng:
        results = perf.batch_load_tickers(
            tickers=my_list,
            load_fn=lambda t: cached_price(t),
            batch_size=10
        )
    """
    results = {}
    progress = st.progress(0, text=progress_label)
    total = len(tickers)

    for i, ticker in enumerate(tickers):
        try:
            results[ticker] = load_fn(ticker)
        except Exception:
            results[ticker] = None
        
        if i % batch_size == 0:
            time.sleep(delay_ms / 1000)
            release_memory()
        
        progress.progress((i + 1) / total, text=f"{progress_label} {i+1}/{total}")

    progress.empty()
    return results


# ─────────────────────────────────────────────
# 8. HELPERS
# ─────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def hash_params(*args) -> str:
    """Tạo cache key từ nhiều tham số."""
    raw = json.dumps(args, default=str, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()[:8]


def format_billion(value: float) -> str:
    """Định dạng số theo tỷ VNĐ cho dễ đọc."""
    if abs(value) >= 1_000:
        return f"{value/1_000:.1f} nghìn tỷ"
    return f"{value:.1f} tỷ"


def color_value(val: float, positive_color="#00B050", negative_color="#FF0000") -> str:
    """Trả về HTML span màu xanh/đỏ cho giá trị tài chính."""
    color = positive_color if val >= 0 else negative_color
    sign  = "+" if val > 0 else ""
    return f'<span style="color:{color};font-weight:600">{sign}{val:,.1f}</span>'


# ─────────────────────────────────────────────
# 9. SIDEBAR CONTROLS (gọi 1 lần ở app.py)
# ─────────────────────────────────────────────

def render_performance_sidebar():
    """
    Thêm vào sidebar: nút clear cache, thông tin bộ nhớ.
    Gọi trong app.py: perf.render_performance_sidebar()
    """
    with st.sidebar:
        st.divider()
        st.caption("⚙️ Hiệu suất")
        if st.button("🗑️ Xóa cache", help="Tải lại dữ liệu mới nhất từ nguồn"):
            clear_old_cache()
        
        # Hiển thị thời gian cập nhật cuối
        last_refresh = st.session_state.get("_last_refresh", "Chưa xác định")
        st.caption(f"Cập nhật lúc: {last_refresh}")
        
        # Cập nhật timestamp
        st.session_state["_last_refresh"] = datetime.now().strftime("%H:%M:%S")
