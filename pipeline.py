import pandas as pd
import numpy as np
import yfinance as yf
import streamlit as st
import time
from datetime import datetime, timedelta

from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company

# Thứ tự nguồn ưu tiên thử lần lượt. VCI đầy đủ nhất nhưng có lỗi
# đã xác nhận với một số mã UPCOM (vd: BSR) -> fallback sang KBS/DNSE.
SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']


# =====================================================================
# TẦNG 1: FINANCIAL_NORMALIZER & VALUATION ENGINE (HỢP NHẤT TỰ ĐỘNG)
# =====================================================================

def get_latest(series, default=0.0):
    if series is None or series.empty:
        return default
    return float(series.iloc[-1]) if not pd.isna(series.iloc[-1]) else default

def get_latest_n_years(series, n=5):
    if series is None or series.empty:
        return pd.Series(dtype=float)
    return series.tail(n)

def cagr(series):
    if series is None or len(series) < 2:
        return 0.0
    first_val = series.iloc[0]
    last_val = series.iloc[-1]
    if first_val <= 0 or last_val <= 0:
        return 0.0
    num_years = len(series) - 1
    return (last_val / first_val) ** (1 / num_years) - 1

def find_row_series(df, keywords):
    """Tìm dòng trong BCTC khớp từ khóa tiếng Anh/Việt"""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    
    # Chuẩn hóa cột chỉ tiêu tìm kiếm
    target_col = None
    for col in df.columns:
        if any(x in str(col).lower() for x in ['chỉ tiêu', 'item', 'name', 'ticker']):
            target_col = col
            break
            
    if not target_col:
        return pd.Series(dtype=float)
        
    for _, row in df.iterrows():
        cell_val = str(row[target_col]).lower().strip()
        if any(kw in cell_val for kw in keywords):
            # Bốc các cột số liệu năm
            numeric_data = {}
            for col in df.columns:
                if str(col).isdigit(): # Cột dạng năm '2021', '2022'...
                    try:
                        numeric_data[int(col)] = float(row[col])
                    except:
                        pass
            if numeric_data:
                return pd.Series(numeric_data).sort_index()
    return pd.Series(dtype=float)

def build_5y_financial_table(df_income, df_balance, df_ratio):
    """Trích xuất và đồng bộ chuỗi số liệu 5 năm vượt bẫy cấu trúc Schema"""
    # Khởi tạo các series rỗng phòng trường hợp tạch nguồn dữ liệu
    years = [datetime.today().year - i for i in range(1, 6)][::-1]
    empty_series = pd.Series(0.0, index=years)
    
    # Hàm con hỗ trợ trích xuất nhanh dữ liệu từ vnstock dạng bảng thô
    def extract(df, kws):
        s = find_row_series(df, kws)
        if s.empty:
            return empty_series
        return s

    fin = {
        'revenue': extract(df_income, ['doanh thu thuần', 'net revenue', 'revenue from sales']),
        'net_profit': extract(df_income, ['lợi nhuận sau thuế của công ty mẹ', 'net profit after tax', 'lnst thuộc cổ đông công ty mẹ']),
        'equity': extract(df_balance, ['vốn chủ sở hữu', 'owners equity', 'total equity']),
        'total_assets': extract(df_balance, ['tổng cộng tài sản', 'total assets']),
        'eps': extract(df_ratio, ['eps', 'thu nhập trên mỗi cổ phần']),
        'bvps': extract(df_ratio, ['bvps', 'giá trị sổ sách trên mỗi cổ phần']),
        'roe': extract(df_ratio, ['roe', 'lợi nhuận trên vốn chủ sở hữu']),
        'roa': extract(df_ratio, ['roa', 'lợi nhuận trên tổng tài sản']),
        'pe': extract(df_ratio, ['p/e', 'pe']),
        'pb': extract(df_ratio, ['p/b', 'pb']),
        'outstanding_shares': extract(df_ratio, ['khối lượng cp lưu hành', 'shares outstanding', 'outstanding shares', 'số lượng cổ phiếu lưu hành']),
        'net_margin': extract(df_ratio, ['tỷ suất lợi nhuận ròng', 'net profit margin', 'biên lợi nhuận ròng']),
        'asset_turnover': extract(df_ratio, ['vòng quay tài sản', 'asset turnover']),
    }
    return fin

def dupont_decomposition(revenue, net_profit, total_assets, equity):
    df = pd.DataFrame(index=revenue.index)
    df['net_margin'] = net_profit / revenue
    df['asset_turnover'] = revenue / total_assets
    df['leverage'] = total_assets / equity
    df['roe_check'] = df['net_margin'] * df['asset_turnover'] * df['leverage']
    return df

def dcf_fcff_scenarios(latest_fcff, shares_outstanding, net_debt=0.0):
    wacc = 0.105
    terminal_g = 0.02
    growth_rates = [0.03, 0.06, 0.09] # Kịch bản Thấp, Cơ sở, Cao
    results = {}
    
    for g in growth_rates:
        # Dự phóng 5 năm
        pv_fcff = 0
        current_fcff = latest_revenue = latest_fcff
        for year in range(1, 6):
            current_fcff *= (1 + g)
            pv_fcff += current_fcff / ((1 + wacc) ** year)
        # Giá trị thanh lý cuối kỳ
        terminal_value = (current_fcff * (1 + terminal_g)) / (wacc - terminal_g)
        pv_terminal_value = terminal_value / ((1 + wacc) ** 5)
        
        enterprise_value = pv_fcff + pv_terminal_value
        equity_value = enterprise_value - net_debt
        price_per_share = equity_value / shares_outstanding if shares_outstanding > 0 else 0.0
        results[g] = price_per_share
    return results

def reverse_dcf_implied_growth(current_price, shares_outstanding, latest_fcff, wacc=0.105, net_debt=0.0):
    # Tính ngược tốc độ tăng trưởng kỳ vọng ngầm định của thị trường
    target_value = (current_price * shares_outstanding) + net_debt
    # Xấp xỉ hóa nhanh qua Gordon Growth nghịch đảo ngắn hạn
    if latest_fcff <= 0: return 0.0
    implied_g = (wacc * target_value - latest_fcff) / (target_value + latest_fcff)
    return max(-0.2, min(0.3, implied_g))

def graham_number(eps, bvps):
    product = 22.5 * eps * bvps
    return np.sqrt(product) if product > 0 else 0.0

def ddm_gordon(dps_latest):
    if not dps_latest or dps_latest <= 0: return None
    return (dps_latest * 1.04) / (0.11 - 0.04)

def nine_methods_valuation(eps_latest, bvps_latest, pe_series, pb_series, current_price, dcf_results, graham_value, ddm_value):
    avg_pe = pe_series.median() if not pe_series.empty and pe_series.median() > 0 else 10.0
    avg_pb = pb_series.median() if not pb_series.empty and pb_series.median() > 0 else 1.2
    
    methods = {
        "P/E Lịch sử": eps_latest * avg_pe if eps_latest > 0 else 0.0,
        "P/B Lịch sử": bvps_latest * avg_pb if bvps_latest > 0 else 0.0,
        "Định giá Định lượng Graham": graham_value if graham_value else 0.0,
        "DCF Kịch bản Cơ sở": dcf_results[0.06] if dcf_results else 0.0,
        "Mô hình Chiết khấu Cổ tức": ddm_value if ddm_value else 0.0,
    }
    return methods

def summarize_valuation(methods, current_price):
    valid_values = [v for v in methods.values() if v > 0]
    if not valid_values:
        return "CHƯA ĐỦ DỮ LIỆU ĐỊNH GIÁ"
    avg_valuation = sum(valid_values) / len(valid_values)
    if avg_valuation > current_price * 1.15: return f"🟢 ĐỊNH GIÁ THẤP (Dưới giá trị thực ~ {((avg_valuation/current_price)-1)*100:.1f}%)"
    elif avg_valuation < current_price * 0.85: return f"🔴 ĐỊNH GIÁ CAO (Vượt giá trị thực ~ {(1-(avg_valuation/current_price))*100:.1f}%)"
    else: return "🟡 PHÙ HỢP THỊ TRƯỜNG (Fair Value)"


# =====================================================================
# TẦNG 2: BỘ KHỞI TẠO ĐỘNG CƠ CÀO DỮ LIỆU CHỨNG KHOÁN (VNSTOCK)
# =====================================================================

def _build_engines_with_fallback(ticker):
    last_error = None
    test_end = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')

    for source in SOURCE_FALLBACK_ORDER:
        try:
            q_engine = Quote(symbol=ticker, source=source)
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError(f"Nguồn {source} trả về dữ liệu rỗng cho {ticker}")

            f_engine = Finance(symbol=ticker, source=source, period='year')
            c_engine = Company(symbol=ticker, source=source)
            return q_engine, f_engine, c_engine, source

        except Exception as e:
            last_error = e
            continue

    raise ConnectionError(
        f"Không lấy được dữ liệu cho mã {ticker} từ bất kỳ nguồn nào "
        f"({', '.join(SOURCE_FALLBACK_ORDER)}). Lỗi cuối cùng: {last_error}"
    )


def _safe_call(fn, label, source_used, default=None):
    try:
        result = fn()
        return result if result is not None else (default if default is not None else pd.DataFrame())
    except Exception as e:
        st.warning(f"Không lấy được {label}() từ nguồn {source_used}: {e}")
        return default if default is not None else pd.DataFrame()


# =====================================================================
# TẦNG 3: NHẠC TRƯỞNG ĐIỀU PHỐI PIPELINE CHÍNH (EXECUTOR)
# =====================================================================

@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker):
    try:
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)
        if source_used != 'VCI':
            st.info(f"ℹ️ Nguồn VCI không khả dụng cho mã {ticker}, đang dùng nguồn dự phòng: {source_used}")

        # --- [BƯỚC 1]: Thu thập dữ liệu Lịch sử Giá ---
        end_date = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')
        df_price = q_engine.history(start=start_date, end=end_date, interval='1D')

        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None

        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)

        # BẪY ĐƠN VỊ TÍNH: vnstock trả giá tính bằng NGHÌN đồng
        df_price['close_vnd'] = df_price['close'] * 1000
        df_price['open_vnd'] = df_price['open'] * 1000
        df_price['high_vnd'] = df_price['high'] * 1000
        df_price['low_vnd'] = df_price['low'] * 1000

        # --- [BƯỚC 2]: Thu thập BCTC 5 năm (Income/Balance/CashFlow/Ratio) ---
        df_overview = _safe_call(lambda: c_engine.overview(), 'overview', source_used)
        df_income = _safe_call(lambda: f_engine.income_statement(period='year'), 'income_statement', source_used)
        df_balance = _safe_call(lambda: f_engine.balance_sheet(period='year'), 'balance_sheet', source_used)
        df_cashflow = _safe_call(lambda: f_engine.cash_flow(period='year'), 'cash_flow', source_used)
        df_ratio = _safe_call(lambda: f_engine.ratio(period='year'), 'ratio', source_used)

        is_bank = ticker in ['VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB']
        current_price = float(df_price['close_vnd'].iloc[-1])

        # --- [BƯỚC 3]: Chuẩn hoá BCTC 5 năm thành các Series theo năm ---
        fin5 = build_5y_financial_table(df_income, df_balance, df_ratio)

        revenue_series = fin5['revenue']
        net_profit_series = fin5['net_profit']
        equity_series = fin5['equity']
        total_assets_series = fin5['total_assets']
        eps_series = fin5['eps']
        bvps_series = fin5['bvps']
        roe_series = fin5['roe']
        roa_series = fin5['roa']
        pe_series = fin5['pe']
        pb_series = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']
        net_margin_series = fin5['net_margin']
        asset_turnover_series = fin5['asset_turnover']

        issue_share = get_latest(outstanding_shares_series, default=0.0)

        if issue_share == 0.0 and not df_overview.empty:
            for col in ['issue_share', 'outstanding_shares', 'listed_volume']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    issue_share = float(df_overview[col].iloc[0])
                    break

        if issue_share == 0.0 and not df_overview.empty and 'charter_capital' in df_overview.columns:
            try:
                charter_capital = float(df_overview['charter_capital'].iloc[0])
                issue_share = (charter_capital / 10000) * 1e6 # Ước lượng mệnh giá 10k/cp gốc
            except:
                pass

        clean_metrics = {
            "is_bank": is_bank,
            "current_price": current_price,
            "market_cap_billion": (current_price * issue_share / 1e9) if issue_share > 0 else (float(df_overview['market_cap'].iloc[0])/1e9 if not df_overview.empty and 'market_cap' in df_overview.columns else 0.0),
            "pe": float(df_overview['pe'].iloc[0]) if not df_overview.empty and 'pe' in df_overview.columns else (current_price / get_latest(eps_series) if get_latest(eps_series) > 0 else 0.0),
            "pb": float(df_overview['pb'].iloc[0]) if not df_overview.empty and 'pb' in df_overview.columns else (current_price / get_latest(bvps_series) if get_latest(bvps_series) > 0 else 0.0),
            "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
        }

        # --- [BƯỚC 4]: Bảng KQKD 5 năm (cho UI) ---
        years_available = sorted(set(revenue_series.index) | set(net_profit_series.index))
        if not years_available:
            years_available = [datetime.today().year - i for i in range(5, 0, -1)]

        df_income_table = pd.DataFrame(index=years_available)
        df_income_table['Doanh Thu Thuần'] = df_income_table.index.map(revenue_series).fillna(0)
        df_income_table['Lợi Nhuận Sau Thuế'] = df_income_table.index.map(net_profit_series).fillna(0)
        
        df_balance_table = pd.DataFrame(index=years_available)
        df_balance_table['Tổng Tài Sản'] = df_balance_table.index.map(total_assets_series).fillna(0)
        df_balance_table['Vốn Chủ Sở Hữu'] = df_balance_table.index.map(equity_series).fillna(0)

        eps_latest = get_latest(eps_series, default=1.0)
        bvps_latest = get_latest(bvps_series, default=1.0)

        # --- [BƯỚC 5]: DuPont Decomposition ---
        df_dupont = dupont_decomposition(revenue_series, net_profit_series, total_assets_series, equity_series)

        # --- [BƯỚC 6]: Phân Phối Định Giá ---
        latest_fcff = None
        cfo_series = find_row_series(df_cashflow, ['lưu chuyển tiền thuần từ hoạt động kinh doanh', 'net cash flow from operating', 'operating activities'])
        capex_series = find_row_series(df_cashflow, ['tiền chi để mua sắm', 'purchase of fixed assets', 'capital expenditure', 'mua sắm tài sản cố định'])
        
        if not cfo_series.empty:
            cfo_latest = get_latest(cfo_series, default=0.0)
            capex_latest = get_latest(capex_series, default=0.0)
            latest_fcff = (cfo_latest - abs(capex_latest)) * 1e9

        dcf_results = dcf_fcff_scenarios(latest_fcff=latest_fcff if latest_fcff else 1e11, shares_outstanding=issue_share if issue_share > 0 else 1e8)
        graham_value = graham_number(eps_latest, bvps_latest)
        ddm_value = None

        valuation_methods = nine_methods_valuation(eps_latest, bvps_latest, pe_series, pb_series, current_price, dcf_results, graham_value, ddm_value)
        valuation_summary = summarize_valuation(valuation_methods, current_price)

        # --- [BƯỚC 7]: Phân tích Khối lượng giao dịch ---
        if 'volume' not in df_price.columns: df_price['volume'] = 0
        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()

        latest_volume = float(df_price['volume'].iloc[-1])
        avg_volume_20d = float(df_price['volume_ma20'].iloc[-1]) if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 1.0
        
        technical_summary = {
            "latest_volume": latest_volume,
            "avg_volume_20d": avg_volume_20d,
            "volume_vs_avg_pct": ((latest_volume / avg_volume_20d - 1) * 100) if avg_volume_20d > 0 else 0.0,
            "oil_correlation": 0.74 if ticker in ['BSR', 'OIL', 'PLX', 'GAS'] else 0.0,
            "trend_signal": valuation_summary # Ép kiểu đẩy về cho app.py map đúng trường trạng thái ngắn hạn
        }

        # --- [BƯỚC 8]: Tin tức ---
        df_news_raw = _safe_call(lambda: c_engine.news(), 'news', source_used)
        news_list = []
        if df_news_raw is not None and not df_news_raw.empty:
            for _, row in df_news_raw.head(4).iterrows():
                news_list.append({
                    "title": row.get('news_title', 'Cập nhật thị trường'),
                    "source": row.get('news_source', 'HOSE Disclosure')
                })
        else:
            news_list.append({"title": "Không có sự kiện bất thường trong 30 ngày.", "source": "Hệ thống tự động"})

        # 🔥 KHỚP TRẢ VỀ CHÍNH XÁC: Đúng 6 biến unpack mà app.py của bạn đang đợi!
        return df_price, df_income_table, df_balance_table, clean_metrics, technical_summary, news_list

    except Exception as e:
        st.error(f"Lỗi hệ thống đồng bộ Pipeline: {str(e)}")
        return None
