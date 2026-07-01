import pandas as pd
import numpy as np

def find_row_series(df: pd.DataFrame, keywords: list, exclude_keywords: list = None):
    """
    Tìm trong DataFrame báo cáo tài chính (index là tên chỉ tiêu, cột là kỳ)
    dòng đầu tiên có index chứa ít nhất một từ khóa trong `keywords`,
    đồng thời không chứa bất kỳ từ khóa nào trong `exclude_keywords` (nếu có).

    Trả về Series với index là các kỳ (năm hoặc quý), giá trị là số liệu.
    Nếu không tìm thấy, trả về Series rỗng.
    """
    if df is None or df.empty:
        return pd.Series(dtype=float)
    if exclude_keywords is None:
        exclude_keywords = []

    for idx in df.index:
        idx_str = str(idx).lower()
        # Kiểm tra có chứa ít nhất 1 từ khóa chính
        if not any(k.lower() in idx_str for k in keywords):
            continue
        # Kiểm tra loại trừ
        if any(k.lower() in idx_str for k in exclude_keywords):
            continue
        # Trả về dòng đầu tiên khớp
        series = df.loc[idx]
        # Chuyển sang Series float, cố gắng ép kiểu số
        numeric = pd.to_numeric(series, errors='coerce')
        return numeric.dropna()
    return pd.Series(dtype=float)


def build_5y_financial_table(df_income, df_balance, df_ratio, ticker=None):
    """
    Từ các DataFrame báo cáo tài chính (năm), trích xuất các chỉ tiêu cơ bản.
    Trả về dictionary gồm các Series đã được lọc theo kỳ năm.
    """
    result = {}

    # --- Từ Báo cáo KQKD (income statement) ---
    result['revenue'] = find_row_series(df_income, [
        'doanh thu thuần', 'doanh thu thuần về bán hàng và cung cấp dịch vụ',
        'revenue', 'total revenue', 'net revenue'
    ])
    result['net_profit'] = find_row_series(df_income, [
        'lợi nhuận sau thuế', 'lợi nhuận sau thuế thu nhập doanh nghiệp',
        'lợi nhuận sau thuế của cổ đông công ty mẹ',
        'net profit', 'profit after tax', 'net income'
    ])

    # --- Từ Bảng cân đối kế toán (balance sheet) ---
    result['equity'] = find_row_series(df_balance, [
        'vốn chủ sở hữu', 'vốn chủ sở hữu tổng cộng', 'equity',
        'total equity', 'shareholders\' equity'
    ])
    result['total_assets'] = find_row_series(df_balance, [
        'tổng tài sản', 'tổng cộng tài sản', 'total assets'
    ])
    # Số cổ phiếu lưu hành (đôi khi có trong balance sheet hoặc ratio)
    outstanding = find_row_series(df_balance, [
        'số cổ phiếu lưu hành', 'khối lượng cổ phiếu đang lưu hành',
        'outstanding shares'
    ])
    if outstanding.empty:
        outstanding = find_row_series(df_ratio, [
            'số cổ phiếu lưu hành', 'outstanding shares', 'khối lượng lưu hành'
        ])
    result['outstanding_shares'] = outstanding

    # --- Từ Bảng chỉ số tài chính (ratio) ---
    ratio_indicators = {
        'eps': ['eps', 'earning per share', 'lợi nhuận trên mỗi cổ phiếu'],
        'bvps': ['bvps', 'book value per share', 'giá trị sổ sách trên mỗi cổ phiếu'],
        'roe': ['roe', 'return on equity', 'tỷ suất sinh lời trên vốn chủ sở hữu'],
        'roa': ['roa', 'return on assets', 'tỷ suất sinh lời trên tài sản'],
        'pe': ['p/e', 'pe', 'price to earning'],
        'pb': ['p/b', 'pb', 'price to book'],
        'net_margin': ['biên lợi nhuận ròng', 'net margin'],
        'asset_turnover': ['vòng quay tài sản', 'asset turnover'],
        'ev_ebitda': ['ev/ebitda', 'enterprise value/ebitda'],
        'p_cf': ['p/cf', 'price to cash flow'],
        'ps': ['p/s', 'price to sales'],
        'dps': ['dps', 'dividend per share', 'cổ tức trên mỗi cổ phiếu'],
        'market_cap': ['market cap', 'vốn hóa', 'market capitalization']
    }
    for key, kw_list in ratio_indicators.items():
        series = find_row_series(df_ratio, kw_list)
        result[key] = series

    return result


def build_financial_table(df_income, df_balance, df_ratio, ticker=None, period='quarter'):
    """
    Tương tự build_5y_financial_table nhưng dùng cho dữ liệu theo quý.
    """
    # Gọi lại hàm xây dựng cho năm (cấu trúc giống hệt, chỉ khác period dữ liệu đầu vào)
    return build_5y_financial_table(df_income, df_balance, df_ratio, ticker)


def get_latest(series, default=0.0):
    """Trả về giá trị của kỳ gần nhất trong Series (index đã sắp xếp)."""
    if series is None or series.empty:
        return default
    # Index có thể là năm (int) hoặc quarter (str), cần sort được
    try:
        sorted_series = series.sort_index()
        return float(sorted_series.iloc[-1])
    except Exception:
        return default


def get_latest_n_years(series, n=5):
    """Lấy tối đa n năm gần nhất từ Series (giả sử index là năm)."""
    if series is None or series.empty:
        return series
    # Lọc các index có dạng số
    years = [idx for idx in series.index if isinstance(idx, (int, np.integer)) or
             (isinstance(idx, str) and idx.isdigit())]
    years = sorted([int(y) for y in years], reverse=True)[:n]
    return series.loc[[y for y in series.index if int(y) in years]].sort_index()


def cagr(series):
    """
    Tính CAGR (tăng trưởng kép) từ năm đầu đến năm cuối của Series.
    Trả về float (0.xx) hoặc None nếu không đủ dữ liệu.
    """
    if series is None or series.empty or len(series) < 2:
        return None
    s = series.sort_index()
    first_val = s.iloc[0]
    last_val = s.iloc[-1]
    if pd.isna(first_val) or pd.isna(last_val) or first_val == 0:
        return None
    years = len(s) - 1
    if years <= 0:
        return None
    if last_val / first_val < 0:
        # Không áp dụng cho chuỗi chuyển từ âm sang dương
        return None
    return (last_val / first_val) ** (1.0 / years) - 1.0
