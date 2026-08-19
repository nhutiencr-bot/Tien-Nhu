import os
from supabase import create_client
from vnstock.api.listing import Listing
import pandas as pd

SUPABASE_URL = "https://xxxx.supabase.co"   # thay bằng URL thật
SUPABASE_KEY = "eyJhbGci..."                 # thay bằng anon key thật

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Lấy danh sách từ VCI (chạy local không bị block)
lst = Listing(source='VCI')
df = lst.symbols_by_exchange()
df = df[df['exchange'].isin(['HOSE', 'HNX', 'UPCOM'])]
df = df[df['symbol'].str.fullmatch(r'[A-Z]{3}')]
df = df[['symbol', 'organ_name', 'exchange']].drop_duplicates('symbol')
df['organ_name'] = df['organ_name'].fillna('').astype(str).str.strip()
df = df.sort_values(['exchange', 'symbol']).reset_index(drop=True)

print(f"Loaded {len(df)} symbols, uploading to Supabase...")

# Upsert theo batch 500
records = df.to_dict('records')
batch_size = 500
for i in range(0, len(records), batch_size):
    batch = records[i:i+batch_size]
    supabase.table('symbols').upsert(batch).execute()
    print(f"  Uploaded {min(i+batch_size, len(records))}/{len(records)}")

print("Done!")
