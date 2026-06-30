import streamlit as st
import pandas as pd
from vnstock.api.listing import Listing

# Danh sách niêm yết gần như không đổi trong ngày -> cache dài (12 giờ)
# để giảm số lần gọi API và tránh bị nguồn dữ liệu giới hạn (rate limit).
LISTING_CACHE_TTL = 12 * 60 * 60  # 12 giờ

# Một số dataset gắn nhãn sàn khác chuẩn (vd "HSX" thay vì "HOSE",
# "UPCOM"/"UPCoM" viết hoa khác nhau) -> chuẩn hoá về 3 nhãn cố định.
EXCHANGE_NORMALIZE_MAP = {
    "HSX": "HOSE",
    "HOSE": "HOSE",
    "HNX": "HNX",
    "UPCOM": "UPCOM",
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

    Nếu nguồn VCI lỗi (rate limit/IP bị chặn), thử lần lượt KBS rồi DNSE
    -- tương tự cơ chế fallback đã áp dụng cho pipeline lấy giá/BCTC.
    """
    last_error = None
    for source in ["VCI", "KBS", "DNSE"]:
        try:
            lst = Listing(source=source)
            df = lst.symbols_by_exchange()  # có đủ symbol + exchange + organ_name

            # Chuẩn hoá tên cột phòng trường hợp nguồn khác đặt tên khác
            cols_lower = {c.lower(): c for c in df.columns}
            symbol_col = cols_lower.get("symbol", "symbol")
            exchange_col = cols_lower.get("exchange", cols_lower.get("board", "exchange"))
            name_col = cols_lower.get("organ_name", cols_lower.get("organ_short_name", None))

            if symbol_col not in df.columns:
                raise ValueError(f"Nguồn {source} thiếu cột symbol, không dùng được.")

            out = pd.DataFrame()
            out["symbol"] = df[symbol_col].astype(str).str.strip().str.upper()
            out["exchange"] = (
                df[exchange_col].apply(_normalize_exchange_label)
                if exchange_col in df.columns
                else "KHÁC"
            )
            out["organ_name"] = df[name_col] if name_col and name_col in df.columns else ""

            # Chỉ giữ cổ phiếu thường trên 3 sàn chính, loại trùng mã
            out = out[out["exchange"].isin(["HOSE", "HNX", "UPCOM"])]

            # Loại trái phiếu / chứng quyền / phái sinh: mã cổ phiếu thường VN
            # luôn là 3 KÝ TỰ CHỮ CÁI (vd FPT, HPG, VCB). Trái phiếu thường có
            # mã dài 8-10 ký tự lẫn số (vd BAB123032), chứng quyền cũng dài hơn
            # và có số trong mã -> lọc bỏ theo đúng định dạng mã cổ phiếu.
            out = out[out["symbol"].str.fullmatch(r"[A-Z]{3}")]

            # Loại các dòng không có tên công ty (thường là do dữ liệu rác/trái phiếu sót lại)
            out["organ_name"] = out["organ_name"].astype(str).str.strip()
            out = out[~out["organ_name"].isin(["", "nan", "None", "NaN"])]

            out = out.drop_duplicates(subset="symbol").sort_values(["exchange", "symbol"]).reset_index(drop=True)

            if out.empty:
                raise ValueError(f"Nguồn {source} trả về danh sách rỗng sau khi lọc.")

            out.attrs["source_used"] = source
            return out

        except Exception as e:
            last_error = e
            continue

    # Tất cả nguồn đều lỗi -> trả DataFrame rỗng kèm cảnh báo,
    # để app.py có thể fallback về ô nhập tay thay vì crash.
    st.warning(
        f"Không tải được danh sách mã từ VCI/KBS/DNSE (lỗi cuối: {last_error}). "
        f"Bạn vẫn có thể gõ tay mã cổ phiếu."
    )
    return pd.DataFrame(columns=["symbol", "exchange", "organ_name"])


def build_display_options(df_symbols: pd.DataFrame):
    """
    Tạo list string hiển thị dạng 'MÃ — Tên công ty (SÀN)' để dùng trong
    selectbox, cùng dict map ngược display -> symbol gốc.
    """
    if df_symbols.empty:
        return [], {}

    display_list = []
    display_to_symbol = {}
    for _, row in df_symbols.iterrows():
        name = row["organ_name"] if row["organ_name"] else ""
        label = f"{row['symbol']} — {name} ({row['exchange']})" if name else f"{row['symbol']} ({row['exchange']})"
        display_list.append(label)
        display_to_symbol[label] = row["symbol"]

    return display_list, display_to_symbol
