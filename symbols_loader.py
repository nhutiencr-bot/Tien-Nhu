import streamlit as st
import pandas as pd

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

def _try_load_vnstock4(source):
    """vnstock >= 4.0 dùng Vnstock().stock().listing"""
    from vnstock import Vnstock
    obj = Vnstock(source=source, show_log=False)
    # vnstock 4.x: listing thông qua .stock(symbol="").listing
    listing = obj.stock(symbol="ACB").listing
    df = listing.symbols_by_exchange()
    return df

def _try_load_legacy(source):
    """vnstock 3.x / fallback"""
    from vnstock.api.listing import Listing
    lst = Listing(source=source)
    df = lst.symbols_by_exchange()
    return df

@st.cache_data(ttl=LISTING_CACHE_TTL)
def load_all_symbols():
    last_error = None
    for source in ["VCI", "KBS", "DNSE"]:
        for loader in [_try_load_vnstock4, _try_load_legacy]:
            try:
                df = loader(source)
                if df is None or df.empty:
                    continue

                cols_lower = {c.lower(): c for c in df.columns}
                symbol_col = cols_lower.get("symbol", None)
                if symbol_col is None:
                    # fallback: cột đầu tiên
                    symbol_col = df.columns[0]

                exchange_col = (
                    cols_lower.get("exchange")
                    or cols_lower.get("board")
                    or cols_lower.get("comgroupcode")
                    or None
                )
                name_col = (
                    cols_lower.get("organ_name")
                    or cols_lower.get("organ_short_name")
                    or cols_lower.get("companyname")
                    or None
                )

                out = pd.DataFrame()
                out["symbol"] = df[symbol_col].astype(str).str.strip().str.upper()
                out["exchange"] = (
                    df[exchange_col].apply(_normalize_exchange_label)
                    if exchange_col and exchange_col in df.columns
                    else "KHÁC"
                )
                out["organ_name"] = (
                    df[name_col].astype(str).str.strip()
                    if name_col and name_col in df.columns
                    else ""
                )

                out = out[out["exchange"].isin(["HOSE", "HNX", "UPCOM"])]
                # Nới lỏng regex: chấp nhận 2-5 ký tự
                out = out[out["symbol"].str.fullmatch(r"[A-Z0-9]{2,5}")]
                out["organ_name"] = out["organ_name"].replace(["", "nan", "None", "NaN"], pd.NA)

                if out.empty:
                    continue

                out = out.drop_duplicates(subset="symbol")
                out = out.sort_values(["exchange", "symbol"]).reset_index(drop=True)
                out.attrs["source_used"] = source
                print(f"[SYMBOLS] ✅ Loaded {len(out)} symbols from {source} via {loader.__name__}")
                return out

            except Exception as e:
                last_error = e
                print(f"[SYMBOLS] ⚠️ {loader.__name__}({source}): {e}")
                continue

    st.warning(
        f"Không tải được danh sách mã từ VCI/KBS/DNSE (lỗi cuối: {last_error}). "
        "Bạn vẫn có thể gõ tay mã cổ phiếu."
    )
    return pd.DataFrame(columns=["symbol", "exchange", "organ_name"])


def build_display_options(df_symbols: pd.DataFrame):
    if df_symbols.empty:
        return [], {}
    display_list = []
    display_to_symbol = {}
    for _, row in df_symbols.iterrows():
        name = row["organ_name"] if row["organ_name"] and row["organ_name"] not in ["nan", "None", ""] else ""
        if name:
            label = f"{row['symbol']} — {name} ({row['exchange']})"
        else:
            label = f"{row['symbol']} ({row['exchange']})"
        display_list.append(label)
        display_to_symbol[label] = row["symbol"]
    return display_list, display_to_symbol
