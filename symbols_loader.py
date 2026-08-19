import streamlit as st
import pandas as pd
import os

LISTING_CACHE_TTL = 12 * 60 * 60  # 12 tiếng

@st.cache_data(ttl=LISTING_CACHE_TTL)
def load_all_symbols():
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("Thiếu SUPABASE_URL hoặc SUPABASE_KEY")
        
        supabase = create_client(url, key)
        res = supabase.table('symbols').select('symbol,organ_name,exchange').execute()
        df = pd.DataFrame(res.data)
        
        if df.empty:
            raise ValueError("Supabase trả về rỗng")
        
        df['symbol'] = df['symbol'].astype(str).str.strip().str.upper()
        df['organ_name'] = df['organ_name'].fillna('').astype(str).str.strip()
        df['exchange'] = df['exchange'].fillna('KHÁC').astype(str).str.strip()
        df = df.drop_duplicates('symbol').sort_values(['exchange','symbol']).reset_index(drop=True)
        
        print(f"[SYMBOLS] ✅ Loaded {len(df)} symbols from Supabase")
        return df

    except Exception as e:
        print(f"[SYMBOLS] ❌ {e}")
        st.warning("Không tải được danh sách mã. Bạn vẫn có thể gõ tay mã cổ phiếu.")
        return pd.DataFrame(columns=['symbol', 'exchange', 'organ_name'])


def build_display_options(df_symbols: pd.DataFrame):
    if df_symbols.empty:
        return [], {}
    display_list = []
    display_to_symbol = {}
    for _, row in df_symbols.iterrows():
        name = row['organ_name']
        if name and name not in ('', 'nan', 'None'):
            label = f"{row['symbol']} — {name} ({row['exchange']})"
        else:
            label = f"{row['symbol']} ({row['exchange']})"
        display_list.append(label)
        display_to_symbol[label] = row['symbol']
    return display_list, display_to_symbol
