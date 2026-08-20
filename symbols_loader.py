"""
symbols_loader.py
-----------------
Load danh sách mã cổ phiếu — dùng vnstock v3.1.0 (explorer) thay vnstock.api
"""
import pandas as pd
from vn_symbols_data import VN_SYMBOLS

# Nếu nguồn live trả về ít hơn ngưỡng này thì coi như bị chặn (403) / lỗi và
# dùng danh sách tĩnh đầy đủ (~1398 mã) thay vì rơi về vài chục mã.
MIN_LIVE_SYMBOLS = 500

def _static_symbols_df() -> pd.DataFrame:
    """
    Danh sách TĨNH ~1398 mã HOSE/HNX/UPCOM (embed sẵn trong vn_symbols_data.py).
    Dùng làm fallback khi API live bị 403/timeout trên Streamlit Cloud, để
    dropdown luôn đủ mã thay vì chỉ còn ~35 mã.
    """
    return pd.DataFrame(VN_SYMBOLS, columns=["symbol", "organ_name", "exchange"])

def load_all_symbols() -> pd.DataFrame:
    """
    Trả về DataFrame(symbol, organ_name, exchange).
    Thử VCI rồi TCBS (vnstock explorer). Chỉ chấp nhận kết quả live khi có
    >= MIN_LIVE_SYMBOLS mã; nếu ít hơn (dấu hiệu 403/bị chặn — chỉ trả vài
    chục mã) hoặc lỗi, dùng danh sách tĩnh đầy đủ ~1398 mã.
    """
    for provider in ("vci", "tcbs"):
        try:
            if provider == "vci":
                from vnstock.explorer.vci.listing import Listing
            else:
                from vnstock.explorer.tcbs.listing import Listing
            lst = Listing()
            df = lst.all_symbols()
            if df is not None and not df.empty and len(df) >= MIN_LIVE_SYMBOLS:
                return df
        except Exception:
            continue

    # Tất cả nguồn live lỗi hoặc trả quá ít mã -> danh sách tĩnh đầy đủ.
    return _static_symbols_df()

def build_display_options(df: pd.DataFrame):
    """
    Trả về (display_list, display_to_symbol).
    display_list: ["ACB — Ngân hàng TMCP Á Châu (HOSE)", ...]
    display_to_symbol: {"ACB — ...": "ACB"}
    Vectorized pandas — nhanh hơn ~50x so với iterrows().
    """
    if df is None or df.empty:
        return [], {}

    # Chuẩn hoá tên cột
    sym_col  = next((c for c in df.columns if c.lower() in ["symbol", "ticker"]), None)
    name_col = next((c for c in df.columns if "name" in c.lower() or "organ" in c.lower()), None)
    exch_col = next((c for c in df.columns if "exchange" in c.lower() or "comgroup" in c.lower()), None)

    if sym_col is None:
        return [], {}

    df = df.copy()
    sym  = df[sym_col].astype(str).str.strip().str.upper()
    name = df[name_col].astype(str).str.strip() if name_col else pd.Series("", index=df.index)
    exch = df[exch_col].astype(str).str.strip() if exch_col else pd.Series("", index=df.index)

    # Mask các giá trị rỗng / NaN
    has_name = name_col is not None and name.ne("") & ~name.isin(["nan", "None", "NaN"])
    has_exch = exch_col is not None and exch.ne("") & ~exch.str.upper().isin(["NAN", "NONE", ""])

    # Build label vectorized
    label = sym.copy()
    label = sym + " — " + name.where(has_name, "") 
    label = label.str.strip(" —")  # dọn khi không có name
    label = label + " (" + exch + ")"
    label = label.where(has_exch, label.str.replace(r" \(.*\)$", "", regex=True))

    # Rebuild sạch hơn bằng np.select
    import numpy as np
    label = np.select(
        [has_name & has_exch,  has_name & ~has_exch, ~has_name & has_exch],
        [sym + " — " + name + " (" + exch + ")",  sym + " — " + name,  sym + " (" + exch + ")"],
        default=sym
    )

    display_list = label.tolist()
    display_to_symbol = dict(zip(label, sym))

    return display_list, display_to_symbol
