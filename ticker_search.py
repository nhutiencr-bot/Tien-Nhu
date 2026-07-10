"""
ticker_search.py — Tra cứu mã cổ phiếu TỨC THỜI, không còn chờ 20-30s.

VẤN ĐỀ CŨ
---------
Mỗi lần người dùng gõ/chọn mã trong dropdown "Chọn mã cổ phiếu cần bóc tách
(đang có 1523 mã...)", app gọi lại API danh sách mã (Listing) hoặc dựng lại
toàn bộ selectbox 1500+ dòng từ đầu → tốn 20-30s mỗi lần vì phải gọi mạng.

CÁCH SỬA
--------
1. Tải TOÀN BỘ danh sách mã (symbol, tên công ty, sàn) CHỈ 1 LẦN — cache
   2 lớp:
     a) st.cache_data(ttl=24h)  — cache trong bộ nhớ phiên Streamlit.
     b) File JSON trên đĩa (.ticker_list_cache.json) — sống sót qua việc
        restart app / clear cache Streamlit, TTL riêng 24h.
2. Mọi thao tác tìm kiếm sau đó (search_ticker) chỉ lọc trên DataFrame đã
   có sẵn trong RAM bằng pandas string match → tức thời (<0.01s), KHÔNG
   gọi mạng nữa.
3. Cung cấp sẵn component Streamlit render_ticker_selector() để dùng thay
   cho selectbox cũ (gõ để lọc thay vì cuộn 1500+ dòng).

CÁCH DÙNG TRONG app.py
-----------------------
    from ticker_search import render_ticker_selector

    ticker = render_ticker_selector()
    if ticker:
        result = execute_equity_research_pipeline(ticker)
        ...

Hoặc chỉ cần hàm tìm kiếm thuần (không cần UI):
    from ticker_search import search_ticker
    matches_df = search_ticker("HPG")   # trả về DataFrame tức thời
"""

import json
import os
import time
import pandas as pd
import streamlit as st

SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ticker_list_cache.json")
CACHE_TTL_SECONDS = 24 * 3600  # 24 giờ — danh sách mã hầu như không đổi trong ngày


def _fetch_all_symbols_from_api() -> pd.DataFrame:
    """
    Gọi API 1 LẦN để lấy toàn bộ danh sách mã. Hàm này CHỈ được gọi khi cả 2
    lớp cache (đĩa + Streamlit) đều hết hạn hoặc trống — không phải mỗi lần
    tìm kiếm.
    """
    from vnstock.api.listing import Listing

    last_error = None
    for source in SOURCE_FALLBACK_ORDER:
        try:
            listing = Listing(source=source)
            df = None
            # Tên hàm có thể khác nhau tuỳ phiên bản vnstock — thử lần lượt
            for method_name in ('all_symbols', 'symbols_by_exchange', 'all_future_indices'):
                if hasattr(listing, method_name):
                    try:
                        candidate = getattr(listing, method_name)()
                        if candidate is not None and not candidate.empty:
                            df = candidate
                            break
                    except Exception:
                        continue
            if df is not None and not df.empty:
                return df
        except Exception as e:
            last_error = e
            continue

    raise ConnectionError(
        f"Không lấy được danh sách mã cổ phiếu từ bất kỳ nguồn nào "
        f"({', '.join(SOURCE_FALLBACK_ORDER)}). Lỗi cuối cùng: {last_error}"
    )


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {}
    for col in df.columns:
        lc = str(col).lower()
        if lc in ('symbol', 'ticker', 'ticker_symbol'):
            rename_map[col] = 'symbol'
        elif lc in ('organ_name', 'company_name', 'organname', 'organ_short_name'):
            rename_map[col] = 'organ_name'
        elif lc in ('exchange', 'comgroupcode', 'group_code'):
            rename_map[col] = 'exchange'
    df = df.rename(columns=rename_map)

    if 'symbol' not in df.columns:
        raise ValueError("Dữ liệu danh sách mã không có cột 'symbol'.")

    keep_cols = [c for c in ['symbol', 'organ_name', 'exchange'] if c in df.columns]
    df = df[keep_cols].copy()
    df['symbol'] = df['symbol'].astype(str).str.strip().str.upper()
    df = df.dropna(subset=['symbol'])
    df = df[df['symbol'] != '']
    df = df.drop_duplicates(subset=['symbol']).sort_values('symbol').reset_index(drop=True)
    return df


def _load_disk_cache() -> pd.DataFrame | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if time.time() - payload.get('_cached_at', 0) > CACHE_TTL_SECONDS:
            return None
        df = pd.DataFrame(payload.get('data', []))
        return df if not df.empty else None
    except Exception:
        return None


def _save_disk_cache(df: pd.DataFrame) -> None:
    try:
        payload = {'_cached_at': time.time(), 'data': df.to_dict(orient='records')}
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        # Ghi cache đĩa lỗi (VD: môi trường read-only) không nên làm crash app
        pass


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Đang tải danh sách mã cổ phiếu (chỉ chạy 1 lần)...")
def get_all_tickers() -> pd.DataFrame:
    """
    Trả về DataFrame [symbol, organ_name, exchange] cho TOÀN BỘ mã.
    - Lần gọi đầu tiên trong ngày: đọc cache đĩa nếu còn hạn; nếu không, gọi
      API (mất vài giây, CHỈ 1 lần) rồi lưu lại cache đĩa.
    - Các lần sau (trong TTL 24h): trả về ngay từ cache Streamlit trong RAM
      — không tính toán lại, không gọi mạng.
    """
    disk_df = _load_disk_cache()
    if disk_df is not None:
        # Cache đĩa được lưu SAU khi đã _normalize_columns() nên có thể dùng thẳng
        return disk_df

    raw_df = _fetch_all_symbols_from_api()
    df = _normalize_columns(raw_df)
    _save_disk_cache(df)
    return df


def search_ticker(query: str, limit: int = 20) -> pd.DataFrame:
    """
    Tìm kiếm TỨC THỜI — lọc trên DataFrame đã có sẵn trong RAM (get_all_tickers
    được cache), KHÔNG gọi mạng ở bước này. Độ trễ chỉ còn là thời gian lọc
    pandas trên ~1500 dòng (thực tế < 10ms).

    Thứ tự ưu tiên kết quả:
      1. Khớp chính xác mã (HPG == HPG)
      2. Mã có tiền tố trùng (HP -> HPG, HPX, ...)
      3. Tên công ty chứa từ khoá (không phân biệt hoa/thường, có dấu)
    """
    df = get_all_tickers()
    if df.empty:
        return df
    if not query or not query.strip():
        return df.head(limit)

    q_upper = query.strip().upper()
    q_lower = query.strip().lower()

    exact = df[df['symbol'] == q_upper]
    prefix = df[df['symbol'].str.startswith(q_upper) & ~df['symbol'].isin(exact['symbol'])]

    name_match = pd.DataFrame(columns=df.columns)
    if 'organ_name' in df.columns:
        already = set(exact['symbol']) | set(prefix['symbol'])
        name_match = df[
            df['organ_name'].astype(str).str.lower().str.contains(q_lower, na=False)
            & ~df['symbol'].isin(already)
        ]

    result = pd.concat([exact, prefix, name_match], ignore_index=True)
    return result.head(limit)


def render_ticker_selector(label: str = "Chọn mã cổ phiếu cần bóc tách", key: str = "ticker_selector"):
    """
    Component Streamlit: gõ để lọc + chọn mã, thay cho selectbox 1500+ dòng
    dựng lại từ API mỗi lần render (nguyên nhân gây chờ 20-30s).

    Trả về: mã cổ phiếu (str) người dùng đã chọn, hoặc None nếu chưa chọn.
    """
    all_df = get_all_tickers()
    total = len(all_df)

    query = st.text_input(
        f"{label} (đang có {total} mã trên HOSE/HNX/UPCOM):",
        key=f"{key}_query",
        placeholder="Gõ mã (VD: HPG) hoặc tên công ty...",
    )

    matches = search_ticker(query, limit=15) if query.strip() else all_df.head(15)

    if matches.empty:
        st.warning("Không tìm thấy mã phù hợp.")
        return None

    def _format_row(row) -> str:
        name = row.get('organ_name', '') or ''
        exch = row.get('exchange', '') or ''
        tail = " — ".join([p for p in [name, exch] if p])
        return f"{row['symbol']}" + (f" — {tail}" if tail else "")

    options = [_format_row(row) for _, row in matches.iterrows()]
    symbol_by_option = dict(zip(options, matches['symbol']))

    selected_label = st.selectbox("Kết quả:", options, key=f"{key}_result")
    return symbol_by_option.get(selected_label)


def clear_ticker_cache() -> None:
    """Xoá cả 2 lớp cache (đĩa + Streamlit), dùng khi cần ép tải lại danh sách mã mới nhất."""
    get_all_tickers.clear()
    try:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
    except Exception:
        pass
