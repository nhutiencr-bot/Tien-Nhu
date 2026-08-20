import streamlit as st
import pandas as pd
from vnstock.api.listing import Listing

# Fallback tĩnh ~1398 mã, dùng khi API live bị block
try:
    from vn_symbols_data import VN_SYMBOLS
    _FALLBACK_DF = pd.DataFrame(VN_SYMBOLS, columns=["symbol", "organ_name", "exchange"])
except ImportError:
    _FALLBACK_DF = pd.DataFrame(columns=["symbol", "organ_name", "exchange"])

LISTING_CACHE_TTL = 24 * 60 * 60  # 24h

EXCHANGE_NORMALIZE_MAP = {
    "HSX": "HOSE", "HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM",
}

def _normalize_exchange_label(value):
    if pd.isna(value):
        return "KHÁC"
    v = str(value).strip().upper()
    return EXCHANGE_NORMALIZE_MAP.get(v, v)

@st.cache_data(ttl=LISTING_CACHE_TTL)
def load_all_symbols():
    last_error = None
    for source in ["VCI", "KBS", "DNSE"]:
        try:
            lst = Listing(source=source)
            df = lst.symbols_by_exchange()

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
                if exchange_col in df.columns else "KHÁC"
            )
            out["organ_name"] = (
                df[name_col].astype(str).str.strip()
                if name_col and name_col in df.columns else ""
            )

            out = out[out["exchange"].isin(["HOSE", "HNX", "UPCOM"])]
            out = out[out["symbol"].str.fullmatch(r"[A-Z]{3}")]
            out = out[~out["organ_name"].isin(["", "nan", "None", "NaN"])]

            if out.empty:
                raise ValueError(f"Nguồn {source} trả về danh sách rỗng")

            out = out.drop_duplicates(subset="symbol")
            out = out.sort_values(["exchange", "symbol"]).reset_index(drop=True)
            out.attrs["source_used"] = source
            return out

        except Exception as e:
            last_error = e
            print(f"[SYMBOLS] Fallback từ nguồn {source}: {e}")
            continue

    # ── Tất cả nguồn live lỗi → dùng static fallback ──
    if not _FALLBACK_DF.empty:
        df = _FALLBACK_DF.copy()
        df = df.sort_values(["exchange", "symbol"]).reset_index(drop=True)
        df.attrs["source_used"] = "static_fallback"
        return df

    st.warning(
        f"Không tải được danh sách mã từ VCI/KBS/DNSE (lỗi cuối: {last_error}). "
        "Bạn vẫn có thể gõ tay mã cổ phiếu."
    )
    return pd.DataFrame(columns=["symbol", "exchange", "organ_name"])


def build_display_options(df_symbols: pd.DataFrame):
    """Vectorized pandas — nhanh hơn ~50x so với iterrows()."""
    if df_symbols.empty:
        return [], {}

    df = df_symbols.copy()
    name_clean = df["organ_name"].astype(str).str.strip()
    has_name = name_clean.ne("") & ~name_clean.isin(["nan", "None", "NaN"])

    df["_label"] = df["symbol"] + " — " + name_clean + " (" + df["exchange"] + ")"
    df.loc[~has_name, "_label"] = (
        df.loc[~has_name, "symbol"] + " (" + df.loc[~has_name, "exchange"] + ")"
    )

    display_list = df["_label"].tolist()
    display_to_symbol = dict(zip(df["_label"], df["symbol"]))
    return display_list, display_to_symbol
