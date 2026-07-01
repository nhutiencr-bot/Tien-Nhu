import pandas as pd
import re

# ════════════════════════════════════════════════════════════════
# PATCH A — Sử dụng registry động thay cho các set hardcode cũ
# ════════════════════════════════════════════════════════════════
from ticker_registry import get_sector
from ticker_registry import (
    BANK_TICKERS, FINANCIAL_TICKERS, INSURANCE_TICKERS,
    RETAIL_TICKERS, REAL_ESTATE_TICKERS,
)

def _find_revenue_for_bank(df_income, period='year'):
    """Hàm bổ trợ tìm doanh thu ngân hàng (được khôi phục cú pháp chuẩn)"""
    if 'bank_revenue_keywords' in globals() or 'bank_revenue_keywords' in locals():
        for keywords, excludes in bank_revenue_keywords: 
            s = find_row_series(df_income, keywords, exclude_keywords=excludes if excludes else None, period=period)
            if not s.empty:
                return s 
    return pd.Series(dtype=float)

def _find_revenue_for_realestate(df_income, period='year'):
    return find_row_series(df_income, ['doanh thu', 'revenue'], period=period)

def _find_revenue_for_retail(df_income, period='year'):
    return find_row_series(df_income, ['doanh thu', 'revenue'], period=period)

# ════════════════════════════════════════════════════════════════
# PATCH B — Hàm build_financial_table xử lý phân loại ngành tự động
# ════════════════════════════════════════════════════════════════
def build_financial_table(df_income, df_balance, df_ratio=None, ticker=None, period='year'):
    """
    Tổng hợp các chỉ tiêu BCTC.
    ticker: dùng để detect ngân hàng/tài chính và chọn logic revenue phù hợp.
    period: 'year' hoặc 'quarter'.
    """
    data = {}
    
    # ── Phân loại ngành TỰ ĐỘNG từ registry 1500+ mã ──
    if ticker:
        _s = get_sector(ticker)        # gọi 1 lần, O(1) lookup
        is_bank       = _s == "bank"
        is_financial  = _s in ("insurance", "securities")
        is_retail     = _s == "retail"
        is_realestate = _s == "realestate"
    else:
        is_bank = is_financial = is_retail = is_realestate = False

    # ── Revenue theo ngành ──────────────────────────────────────
    if is_bank or is_financial:
        data["revenue"] = _find_revenue_for_bank(df_income, period=period)
    elif is_realestate:
        data["revenue"] = _find_revenue_for_realestate(df_income, period=period)
    elif is_retail:
        data["revenue"] = _find_revenue_for_retail(df_income, period=period)
    else:
        data["revenue"] = find_row_series(df_income, ['doanh thu thuần', 'net revenue', 'doanh thu hoạt động kinh doanh'], period=period)
        if data["revenue"].empty:
            # Nếu vẫn không có (mã không xác định được ngành) -> thử bank keywords làm fallback
            data["revenue"] = _find_revenue_for_bank(df_income, period=period)

    # --- Net profit ---
    data['net_profit'] = find_row_series(
        df_income,
        ['lợi nhuận sau thuế', 'net profit', 'profit after tax', 'net income', 'lợi nhuận thuần', 'lãi sau thuế'],
        exclude_keywords=['trước thuế', 'before tax', 'thiểu số', 'minority'],
        item_ids=['net_profit', 'net_profit_after_tax', 'profit_after_tax'], 
        period=period
    )

    # --- Ratio ---
    if df_ratio is not None and not df_ratio.empty:
        data['eps']    = find_row_series(df_ratio, ['eps', 'earning per share', 'earnings per share'], period=period)
        data['bvps']   = find_row_series(df_ratio, ['book value per share', 'bvps'], period=period)
        data['roe']    = find_row_series(df_ratio, ['roe'], period=period)
        data['roa']    = find_row_series(df_ratio, ['roa'], period=period)   
        data['pe']     = find_row_series(df_ratio, ['p/e', 'pe ratio', ' pe '], period=period)
        data['pb']     = find_row_series(df_ratio, ['p/b', 'pb ratio', ' pb '], period=period)
        data['market_cap'] = find_row_series(df_ratio, ['market cap', 'vốn hóa'], item_ids=['market_cap'], period=period)
        
        data['outstanding_shares'] = find_row_series(
            df_ratio, 
            ['outstanding shares', 'số cổ phiếu lưu hành', 'số lượng cổ phiếu'],
            item_ids=['outstanding_shares', 'issue_share'], 
            period=period
        )
        
        data['ev_ebitda']      = find_row_series(df_ratio, ['ev/ebitda', 'ev to ebitda'], period=period)
        data['p_cf']           = find_row_series(df_ratio, ['price to cash flow', 'p/cf'], period=period)
        data['ps']             = find_row_series(df_ratio, ['p/s', 'price to sales', 'ps ratio'], period=period)
        data['net_margin']     = find_row_series(df_ratio, ['net margin', 'after tax profit margin', 'biên lợi nhuận sau thuế'], period=period)
        data['asset_turnover'] = find_row_series(df_ratio, ['asset turnover', 'vòng quay tài sản', 'vòng quay tổng tài sản'], period=period)
        data['dps']            = find_row_series(df_ratio, ['dividend per share', 'cổ tức trên mỗi cổ phiếu', 'cổ tức tiền mặt', 'dps'], period=period)
    else:
        for k in ['eps', 'bvps', 'roe', 'roa', 'pe', 'pb', 'market_cap',
                  'outstanding_shares', 'ev_ebitda', 'p_cf', 'ps', 'net_margin',
                  'asset_turnover', 'dps']:
            data[k] = pd.Series(dtype=float)

    if data['eps'].empty and 'eps_income_stmt' in data and not data['eps_income_stmt'].empty:
        data['eps'] = data['eps_income_stmt']
        
    if data['bvps'].empty and 'equity' in data and not data['equity'].empty and 'outstanding_shares' in data and not data['outstanding_shares'].empty:
        common_years = data['equity'].index.intersection(data['outstanding_shares'].index)
        if len(common_years) > 0:
            data['bvps'] = (data['equity'].loc[common_years] / data['outstanding_shares'].loc[common_years])
            
    return data

# ════════════════════════════════════════════════════════════════
# PATCH C — Hàm tương thích ngược truyền ticker chuẩn chỉ
# ════════════════════════════════════════════════════════════════
def build_5y_financial_table(df_income, df_balance, df_ratio=None, ticker=None):
    """Giữ tương thích ngược: bảng theo năm (hành vi cũ, không đổi)."""
    return build_financial_table(df_income, df_balance, df_ratio, ticker=ticker, period='year')

def cagr(series: pd.Series, n_years=None):
    if series is None or len(series.dropna()) < 2:
        return None
    s = series.dropna()
    start_val, end_val = float(s.iloc[0]), float(s.iloc[-1])
    if start_val <= 0:
        return None
    periods = n_years if n_years else (len(s) - 1)
    if periods <= 0:
        return None
    try:
        return (end_val / start_val) ** (1 / periods) - 1
    except Exception:
        return None

# Đẩy hàm định giá ra hẳn rìa ngoài cùng cấp file để không phá vỡ cấu trúc hàm khác
def nine_methods_valuation(eps_latest, bvps_latest, pe_series: pd.Series, pb_series: pd.Series, current_price):
    # Bạn chỉ cần điền phần xử lý tính toán định giá cũ của bạn vào đây
    pass
