from supabase import create_client
import streamlit as st
import pandas as pd

@st.cache_resource
def get_supabase_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def load_from_cache(ticker: str) -> dict | None:
    """Đọc dữ liệu đã cào trước đó, KHÔNG gọi vnstock API."""
    sb = get_supabase_client()
    res = sb.table("financial_reports").select("*").eq("ticker", ticker).execute()
    if not res.data:
        return None

    df = pd.DataFrame(res.data).set_index("year").sort_index()
    fields = ["revenue", "net_profit", "equity", "total_assets", "eps", "bvps", "roe", "roa"]
    return {f: df[f].dropna() for f in fields if f in df.columns}

def save_to_cache(ticker: str, data: dict, source: str):
    """Ghi kết quả cào được xuống Supabase để lần sau không cào lại."""
    sb = get_supabase_client()
    fields = ["revenue", "net_profit", "equity", "total_assets", "eps", "bvps", "roe", "roa"]
    years = set()
    for f in fields:
        s = data.get(f)
        if s is not None and not s.empty:
            years |= set(s.index)

    rows = []
    for y in years:
        row = {"ticker": ticker, "year": int(y), "source": source}
        for f in fields:
            s = data.get(f)
            if s is not None and y in s.index:
                row[f] = float(s[y])
        rows.append(row)

    if rows:
        sb.table("financial_reports").upsert(rows, on_conflict="ticker,year").execute()
