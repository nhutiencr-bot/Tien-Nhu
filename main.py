import os
import json
import time
from datetime import datetime, timedelta
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from vnstock import stock_historical_data, ticker_overview

# ==========================================
# 1. XÁC THỰC GOOGLE SHEETS
# ==========================================
print("Đang kết nối với Google Sheets...")
creds_json = os.environ.get('GCP_CREDENTIALS')

if not creds_json:
    raise ValueError("Không tìm thấy biến môi trường GCP_CREDENTIALS!")

creds_dict = json.loads(creds_json)
scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
client = gspread.authorize(creds)

# ĐIỀN TÊN FILE GOOGLE SHEET CỦA BẠN VÀO ĐÂY
sheet = client.open('Tên_Sheet_Của_Bạn').sheet1 

# ==========================================
# 2. XỬ LÝ DỮ LIỆU TỪ VNSTOCK
# ==========================================
print("Đang tải dữ liệu từ vnstock...")

# Lấy mốc thời gian 40 ngày trước để đảm bảo luôn có đủ 20 phiên giao dịch (trừ T7, CN, Lễ)
end_date = datetime.now().strftime('%Y-%m-%d')
start_date = (datetime.now() - timedelta(days=40)).strftime('%Y-%m-%d')

# Danh sách quét (Các mã thanh khoản cao, phổ biến trên HOSE/HNX/UPCOM)
symbols = [
    'SSI','BCM','VHM','VIC','VRE','BVH','POW','GAS','ACB','BID',
    'CTG','HDB','MBB','SHB','STB','TCB','TPB','VCB','VIB','VPB','HPG',
    'GVR','MSN','VNM','SAB','MWG','FPT','GEX','REE','PNJ','VJC','HVN','NVL',
    'PDR','DIG','DXG','NLG','KDH','KBC','VND','VCI','HCM','VIX','FTS','BSI',
    'CTS','MBS','SHS','DGC','DPM','DCM','CSV','HSG','NKG','VGC','IDC','SZC',
    'PC1','HDG','BCG','TCH','HAG','SBT','PAN','ASM','GEG','LCG','HHV',
    'VCG','FCN','CTD','VHC','ANV','IDI','HAH','GMD','PVT','PVS','PVD','BSR',
    'OIL','ACV','VEA','MCH','CTR','FOX','ViettelPost', 'NAF', 'LPB', 'EVF', 'MSR', 'KDC', 'PHR', 'DCL'
]

data_rows = []

for sym in symbols:
    try:
        # Lấy lịch sử giá để tính Trung bình
        df_hist = stock_historical_data(symbol=sym, start_date=start_date, end_date=end_date, resolution='1D', type='stock')
        if df_hist is None or df_hist.empty or len(df_hist) < 20:
            continue

        # Lấy đúng 20 phiên gần nhất
        df_hist = df_hist.sort_values('time').tail(20)
        
        # Đóng cửa (vnđ) chuyển sang (kvnđ)
        close_price_vnd = df_hist['close'].iloc[-1]
        close_kvnd = close_price_vnd / 1000
        
        # KLTB 20N
        avg_vol_20 = df_hist['volume'].mean()
        
        # Tính Giá trị giao dịch = (Giá đóng cửa * KLTB) / 1 Tỷ
        gtgd = (close_price_vnd * avg_vol_20) / 1000000000
        
        # ĐIỀU KIỆN LỌC: CHỈ LẤY MÃ CÓ GTGD > 20 TỶ
        if gtgd <= 20:
            continue

        # Lấy Vốn hóa và P/E
        overview = ticker_overview(sym)
        if not overview.empty:
            market_cap = overview['marketcap'].iloc[0] # Đơn vị đã là tỷ đồng
            pe = overview['pe'].iloc[0]
        else:
            market_cap = 0
            pe = 0
        
        # Logic tính Điểm kỹ thuật & Xu hướng (Giả định Giá > MA20 là Khả quan)
        ma20 = df_hist['close'].mean()
        if close_price_vnd > ma20:
            trend = "KHẢ QUAN"
            tech_score = 5
        else:
            trend = "TRUNG TÍNH"
            tech_score = 2
            
        data_rows.append([
            sym,
            round(close_kvnd, 2),
            int(avg_vol_20),
            tech_score,
            trend,
            round(market_cap, 0) if pd.notnull(market_cap) else "N/A",
            round(pe, 1) if pd.notnull(pe) else "N/A",
            round(gtgd, 1)
        ])
        print(f"✅ Hợp lệ: {sym} | GTGD: {round(gtgd, 1)} tỷ")
        
        # Nghỉ 0.5s giữa các mã để máy chủ vnstock không chặn IP do gọi API quá nhanh
        time.sleep(0.5)

    except Exception as e:
        print(f"⚠️ Bỏ qua {sym} do lỗi dữ liệu")

# ==========================================
# 3. GHI DỮ LIỆU LÊN GOOGLE SHEETS
# ==========================================
columns = [
    'Mã (đơn vị)', 'Đóng cửa (kvnd)', 'KLTB 20N', 
    'Điểm kỹ thuật (*)', 'Xu hướng SMG ngắn hạn', 
    'Vốn hóa (tỷ đồng)', 'P/E (lần)', 'GTGD (tỷ đồng)'
]

df = pd.DataFrame(data_rows, columns=columns)

# Sắp xếp theo GTGD giảm dần để lấy TOP thanh khoản cao nhất
df = df.sort_values(by=['GTGD (tỷ đồng)'], ascending=False)

sheet.clear()
sheet.update([df.columns.values.tolist()] + df.values.tolist())

print(f"🎉 ĐÃ ĐẨY THÀNH CÔNG {len(df)} MÃ LÊN SHEET!")
