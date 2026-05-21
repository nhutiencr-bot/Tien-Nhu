import os
import json
import gspread
import pandas as pd
import yfinance as yf
from google.oauth2.service_account import Credentials

# 1. XÁC THỰC GOOGLE SHEETS
print("Đang kết nối với Google Sheets...")
creds_json = os.environ.get('GCP_CREDENTIALS')

if not creds_json:
    raise ValueError("Không tìm thấy biến môi trường GCP_CREDENTIALS. Hãy kiểm tra lại phần Secrets trên GitHub!")

creds_dict = json.loads(creds_json)

scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
client = gspread.authorize(creds)

# THAY TÊN SHEET CỦA BẠN VÀO ĐÂY
sheet = client.open('Tên_Sheet_Của_Bạn').sheet1 

# 2. LẤY DỮ LIỆU CHỨNG KHOÁN TỪ YFINANCE
symbols = ['FPT', 'VCB', 'CTR', 'GEX', 'BSR', 'PC1', 'DPM', 'LPB', 'BVH', 'REE']
data_rows = []

print("Đang tải dữ liệu từ yfinance...")
for sym in symbols:
    try:
        ticker = yf.Ticker(f"{sym}.VN")
        hist = ticker.history(period="1mo")
        if hist.empty:
            continue
            
        close_price = hist['Close'].iloc[-1] / 1000
        avg_vol_20 = hist['Volume'].tail(20).mean()
        
        info = ticker.info
        market_cap = info.get('marketCap', 0) / 1e9 
        pe = info.get('trailingPE', 0)
        pb = info.get('priceToBook', 0)
        
        ma20 = hist['Close'].tail(20).mean()
        if hist['Close'].iloc[-1] > ma20:
            trend = "KHẢ QUAN"
            tech_score = 5 
        else:
            trend = "TRUNG TÍNH"
            tech_score = 2
            
        data_rows.append([
            sym,                                    
            round(close_price, 2),                  
            int(avg_vol_20),                        
            tech_score,                             
            trend,                                  
            round(market_cap, 0) if market_cap else "N/A", 
            round(pe, 1) if pe else "N/A",          
            round(pb, 1) if pb else "N/A"           
        ])
        print(f"Đã lấy thành công dữ liệu: {sym}")
        
    except Exception as e:
        print(f"Lỗi khi lấy mã {sym}: {e}")

# 3. CHUẨN BỊ DỮ LIỆU VÀ ĐẨY LÊN SHEET
columns = [
    'Mã (đơn vị)', 'Đóng cửa (kvnd)', 'KLTB 20N', 
    'Điểm kỹ thuật (*)', 'Xu hướng SMG ngắn hạn', 
    'Vốn hóa (tỷ đồng)', 'P/E (lần)', 'P/BV (lần)'
]

df = pd.DataFrame(data_rows, columns=columns)
df = df.sort_values(by=['Điểm kỹ thuật (*)'], ascending=False)

sheet.clear()
sheet.update([df.columns.values.tolist()] + df.values.tolist())

print("✅ ĐÃ CẬP NHẬT GOOGLE SHEET THÀNH CÔNG!")
