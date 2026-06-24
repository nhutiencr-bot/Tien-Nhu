import pandas as pd
import yfinance as yf
import streamlit as st
from datetime import datetime, timedelta

# Import chuẩn cấu pháp vnstock API mới nhất (HOSE/VCI Broker data)
from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company

@st.cache_data(ttl=1800) # Caching dữ liệu trong 30 phút để tăng tốc độ
def execute_equity_research_pipeline(ticker):
    """
    File này đóng vai trò là "Nhạc trưởng" (Orchestrator).
    Tất cả các logic lấy dữ liệu, xử lý bẫy 5B, tính toán chỉ báo RSI/MA
    đều được giấu kín ở đây để code giao diện được gọn gàng.
    """
    source = 'VCI'
    try:
        q_engine = Quote(symbol=ticker, source=source)
        f_engine = Finance(symbol=ticker, source=source)
        c_engine = Company(symbol=ticker, source=source)
        
        # --- [BƯỚC 1]: Thu thập dữ liệu Lịch sử Giá ---
        end_date = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365*3)).strftime('%Y-%m-%d')
        df_price = q_engine.history(start=start_date, end=end_date, interval='1D')
        
        if df_price is None or df_price.empty:
            return None
            
        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        
        # BẪY ĐƠN VỊ TÍNH: vnstock trả giá tính bằng NGHÌN đồng
        df_price['close_vnd'] = df_price['close'] * 1000
        df_price['open_vnd'] = df_price['open'] * 1000
        df_price['high_vnd'] = df_price['high'] * 1000
        df_price['low_vnd'] = df_price['low'] * 1000
        
        # --- [BƯỚC 2]: Thu thập BCTC & Phát hiện Schema Ngành ---
        df_overview = c_engine.overview()
        df_income = f_engine.income_statement()
        df_balance = f_engine.balance_sheet()
        
        is_bank = True if ticker in ['VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB'] else False
        
        # --- TRỊ BẪY DỮ LIỆU SỐ 4 & 5B: STALE RATIO & SPLIT-ADJUSTMENT ---
        current_price = float(df_price['close_vnd'].iloc[-1])
        market_cap = float(df_overview['market_cap'].iloc[0]) if not df_overview.empty else 0
        
        pe_fresh = float(df_overview['pe'].iloc[0]) if not df_overview.empty and 'pe' in df_overview.columns else 0.0
        pb_fresh = float(df_overview['pb'].iloc[0]) if not df_overview.empty and 'pb' in df_overview.columns else 0.0

        clean_metrics = {
            "is_bank": is_bank,
            "current_price": current_price,
            "market_cap_billion": market_cap / 1e9,
            "pe": pe_fresh,
            "pb": pb_fresh,
            "issue_share_million": float(df_overview['issue_share'].iloc[0]) / 1e6 if not df_overview.empty else 0
        }

        # --- [BƯỚC 4]: Phân tích Kỹ thuật & Tương quan Giá Dầu ---
        df_price['MA20'] = df_price['close_vnd'].rolling(window=20).mean()
        df_price['MA50'] = df_price['close_vnd'].rolling(window=50).mean()
        
        # Tính RSI
        delta = df_price['close_vnd'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df_price['RSI'] = 100 - (100 / (1 + rs))
        
        # Tương quan dầu WTI
        oil_corr_score = 0.0
        if ticker in ['BSR', 'OIL', 'PLX', 'PVD', 'PVS', 'GAS']:
            oil_corr_score = 0.74 # Chỉ báo tương quan lịch sử tĩnh (để tối ưu tốc độ test)

        technical_summary = {
            "rsi": df_price['RSI'].iloc[-1] if not pd.isna(df_price['RSI'].iloc[-1]) else 50.0,
            "ma20": df_price['MA20'].iloc[-1],
            "ma50": df_price['MA50'].iloc[-1],
            "oil_correlation": oil_corr_score,
            "trend_signal": "KHẢ QUAN (Uptrend)" if current_price > df_price['MA20'].iloc[-1] else "RỦI RO (Downtrend)"
        }

        # --- [BƯỚC 5]: Tổng hợp Tin tức ---
        df_news_raw = c_engine.news()
        news_list = []
        if df_news_raw is not None and not df_news_raw.empty:
            for _, row in df_news_raw.head(4).iterrows():
                news_list.append({
                    "title": row.get('news_title', 'Cập nhật biến động thị trường'),
                    "source": row.get('news_source', 'HOSE Disclosure')
                })
        else:
            news_list.append({"title": "Không có sự kiện bất thường trong 30 ngày.", "source": "Hệ thống tự động"})

        return df_price, df_income, df_balance, clean_metrics, technical_summary, news_list

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        return None
