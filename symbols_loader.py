import streamlit as st
import pandas as pd
from vnstock.api.listing import Listing

# Cache danh sách mã 12 giờ
LISTING_CACHE_TTL = 12 * 60 * 60

EXCHANGE_NORMALIZE_MAP = {
    "HSX": "HOSE",
    "HOSE": "HOSE",
    "HNX": "HNX",
    "UPCOM": "UPCOM",
}


def _normalize_exchange_label(value):
    if pd.isna(value):
        return "KHÁC"
    v = str(value).strip().upper()
    return EXCHANGE_NORMALIZE_MAP.get(v, v)


@st.cache_data(ttl=LISTING_CACHE_TTL)
def load_all_symbols():
    """
    Lấy toàn bộ mã cổ phiếu trên 3 sàn HOSE/HNX/UPCOM kèm tên công ty và sàn.
    Trả về DataFrame với 3 cột chuẩn hoá: symbol, organ_name, exchange.
    Fallback lần lượt qua VCI -> KBS -> DNSE.
    """
    last_error = None

    for source in ["VCI", "KBS", "DNSE"]:
        try:
            lst = Listing(source=source)
            df = lst.symbols_by_exchange()

            # Chuẩn hóa tên cột
            cols_lower = {c.lower(): c for c in df.columns}
            symbol_col = cols_lower.get("symbol", "symbol")
            exchange_col = cols_lower.get("exchange", cols_lower.get("board", "exchange"))
            name_col = cols_lower.get("organ_name", cols_lower.get("organ_short_name", None))

            if symbol_col not in df.columns:
                raise ValueError(f"Nguồn {source} thiếu cột symbol")

            out = pd.DataFrame()
            out["symbol"] = df[symbol_col].astype(str).str.strip().str.upper()
            out["exchange"] = (
                df[exchange_col].apply(_normalize_exchange_label)
                if exchange_col in df.columns
                else "KHÁC"
            )
            out["organ_name"] = (
                df[name_col] if name_col and name_col in df.columns else ""
            )

            # Giữ lại 3 sàn chính
            out = out[out["exchange"].isin(["HOSE", "HNX", "UPCOM"])]
            # Chỉ lấy mã cổ phiếu thường (3 chữ cái)
            out = out[out["symbol"].str.fullmatch(r"[A-Z]{3}")]
            # Bỏ dòng không có tên công ty
            out["organ_name"] = out["organ_name"].astype(str).str.strip()
            out = out[~out["organ_name"].isin(["", "nan", "None", "NaN"])]

            # Kiểm tra rỗng trước khi sort để tránh lỗi
            if out.empty:
                raise ValueError(f"Nguồn {source} trả về danh sách rỗng sau khi lọc")

            out = out.drop_duplicates(subset="symbol")
            out = out.sort_values(["exchange", "symbol"]).reset_index(drop=True)

            out.attrs["source_used"] = source
            return out

        except Exception as e:
            last_error = e
            # Log ra console để debug nếu cần (có thể hiện warning nhẹ)
            print(f"[SYMBOLS] Fallback từ nguồn {source}: {e}")
            continue

    # Tất cả nguồn lỗi -> fallback DataFrame rỗng + cảnh báo
    st.warning(
        f"Không tải được danh sách mã từ VCI/KBS/DNSE (lỗi cuối: {last_error}). "
        "Bạn vẫn có thể gõ tay mã cổ phiếu."
    )
    return pd.DataFrame(columns=["symbol", "exchange", "organ_name"])


def build_display_options(df_symbols: pd.DataFrame):
    """
    Tạo list string hiển thị dạng 'MÃ — Tên công ty (SÀN)' và dict map ngược.
    """
    if df_symbols.empty:
        return [], {}

    display_list = []
    display_to_symbol = {}

    for _, row in df_symbols.iterrows():
        name = row["organ_name"] if row["organ_name"] else ""
        if name:
            label = f"{row['symbol']} — {name} ({row['exchange']})"
        else:
            label = f"{row['symbol']} ({row['exchange']})"
        display_list.append(label)
        display_to_symbol[label] = row["symbol"]

    return display_list, display_to_symbol
