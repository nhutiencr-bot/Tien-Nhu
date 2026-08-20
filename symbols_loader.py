"""
symbols_loader.py
-----------------
Load danh sách mã cổ phiếu — static first, API sau.
"""
import pandas as pd
import streamlit as st
from vn_symbols_data import VN_SYMBOLS

MIN_LIVE_SYMBOLS = 500

def _static_symbols_df() -> pd.DataFrame:
    return pd.DataFrame(VN_SYMBOLS, columns=["symbol", "organ_name", "exchange"])

@st.cache_data(ttl=24 * 60 * 60)
def load_all_symbols() -> pd.DataFrame:
    """Load static ngay lập tức, không chờ API."""
    return _static_symbols_df()

def build_display_options(df: pd.DataFrame):
    if df is None or df.empty:
        return [], {}

    sym_col  = next((c for c in df.columns if c.lower() in ["symbol", "ticker"]), None)
    name_col = next((c for c in df.columns if "name" in c.lower() or "organ" in c.lower()), None)
    exch_col = next((c for c in df.columns if "exchange" in c.lower() or "comgroup" in c.lower()), None)

    if sym_col is None:
        return [], {}

    import numpy as np
    df = df.copy()
    sym  = df[sym_col].astype(str).str.strip().str.upper()
    name = df[name_col].astype(str).str.strip() if name_col else pd.Series("", index=df.index)
    exch = df[exch_col].astype(str).str.strip() if exch_col else pd.Series("", index=df.index)

    has_name = name.ne("") & ~name.isin(["nan", "None", "NaN"])
    has_exch = exch.ne("") & ~exch.str.upper().isin(["NAN", "NONE", ""])

    label = np.select(
        [has_name & has_exch, has_name & ~has_exch, ~has_name & has_exch],
        [sym + " — " + name + " (" + exch + ")", sym + " — " + name, sym + " (" + exch + ")"],
        default=sym
    )

    return label.tolist(), dict(zip(label, sym))
