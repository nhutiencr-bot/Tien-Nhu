"""
financial_normalizer.py
"""

import pandas as pd
import re

# Danh sách mã ngân hàng VN (mở rộng đầy đủ)
BANK_TICKERS = {
    'VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB', 'VPB', 'HDB', 'TPB',
    'MSB', 'OCB', 'VIB', 'SHB', 'EIB', 'LPB', 'SSB', 'NAB', 'ABB', 'BAB',
    'BVB', 'KLB', 'PGB', 'VAB', 'VBB', 'SGN', 'NVB', 'SGB', 'CBB', 'SEAB',
}

# Danh sách mã bảo hiểm/chứng khoán (cũng dùng thu nhập thay doanh thu)
FINANCIAL_TICKERS = {
    'BVH', 'PVI', 'PTI', 'MIG', 'BMI', 'VNR', 'BIC', 'PRE', 'PGI',
    'SSI', 'VND', 'HCM', 'MBS', 'VCI', 'FTS', 'AGR', 'SBS', 'BSI',
}


def _get_year_columns(df: pd.DataFrame):
    meta_cols = {'item', 'item_en', 'item_id'}
    year_cols = []
    for c in df.columns:
        if c in meta_cols:
            continue
        c_str = str(c).strip()
        if re.fullmatch(r'\d{4}', c_str):
            year_cols.append(c)
    year_cols = sorted(year_cols, key=lambda x: int(str(x).strip()))
    return year_cols


def find_row_series(df: pd.DataFrame, keywords, exclude_keywords=None,
                    item_ids=None, prefer_top_level=True):
    if df is None or df.empty:
        return pd.Series(dtype=float)

    year_cols = _get_year_columns(df)
    if not year_cols:
        return pd.Series(dtype=float)

    search_cols = [c for c in ['item', 'item_en', 'item_id'] if c in df.columns]
    if not search_cols:
        return pd.Series(dtype=float)

    matched = pd.DataFrame()

    # Bước 1: thử khớp chính xác theo item_id
    if item_ids and 'item_id' in df.columns:
        item_id_lower = df['item_id'].astype(str).str.lower().str.strip()
        target_ids = [i.lower().strip() for i in item_ids]
        mask_exact = item_id_lower.isin(target_ids)
        if mask_exact.any():
            matched = df[mask_exact]

    # Bước 2: fallback dò từ khoá
    if matched.empty:
        combined_text = df[search_cols].astype(str).agg(' '.join, axis=1).str.lower()
        mask = pd.Series(False, index=df.index)
        for kw in keywords:
            mask = mask | combined_text.str.contains(kw.lower(), na=False, regex=False)
        if exclude_keywords:
            for kw in exclude_keywords:
                mask = mask & ~combined_text.str.contains(kw.lower(), na=False, regex=False)
        matched = df[mask]

    if matched.empty:
        return pd.Series(dtype=float)

    row = matched.iloc[0]
    if len(matched) > 1:
        if prefer_top_level and 'levels' in matched.columns:
            levels_numeric = pd.to_numeric(matched['levels'], errors='coerce')
            if levels_numeric.notna().any():
                min_level = levels_numeric.min()
                top_level_rows = matched[levels_numeric == min_level]
                if len(top_level_rows) == 1:
                    row = top_level_rows.iloc[0]
                else:
                    non_na_counts = top_level_rows[year_cols].notna().sum(axis=1)
                    row = top_level_rows.loc[non_na_counts.idxmax()]
            else:
                non_na_counts = matched[year_cols].notna().sum(axis=1)
                row = matched.loc[non_na_counts.idxmax()]
        else:
            non_na_counts = matched[year_cols].notna().sum(axis=1)
            row = matched.loc[non_na_counts.idxmax()]

    result = {}
    for yc in year_cols:
        val = pd.to_numeric(pd.Series([row[yc]]), errors='coerce').iloc[0]
        if pd.notna(val):
            result[int(str(yc).strip())] = float(val)

    return pd.Series(result).sort_index()


def _find_revenue_for_bank(df_income):
    """
    Ngân hàng/bảo hiểm/chứng khoán không có 'doanh thu thuần'.
    Thử lần lượt các chỉ tiêu thu nhập đặc thù theo thứ tự ưu tiên.
    """
    # Thứ tự ưu tiên cho ngân hàng
    bank_revenue_keywords = [
        # Tổng thu nhập hoạt động (phổ biến nhất)
        (['tổng thu nhập hoạt động', 'total operating income', 'net operating income'], ['chi phí', 'expense']),
        # Thu nhập lãi thuần
        (['thu nhập lãi thuần', 'net interest income', 'lãi thuần'], ['chi phí lãi']),
        # Thu nhập thuần
        (['thu nhập thuần', 'net income from', 'total net income'], ['lợi nhuận', 'profit']),
        # Tổng doanh thu
        (['tổng doanh thu', 'total revenue', 'gross revenue'], []),
        # Doanh thu hoạt động
        (['doanh thu hoạt động', 'operating revenue'], []),
        # Thu nhập từ lãi
        (['thu nhập từ lãi', 'interest income', 'interest and similar income'], ['chi phí']),
    ]

    for keywords, excludes in bank_revenue_keywords:
        s = find_row_series(df_income, keywords,
                           exclude_keywords=excludes if excludes else None)
        if not s.empty:
            return s

    return pd.Series(dtype=float)


def build_5y_financial_table(df_income, df_balance, df_ratio=None, ticker=None):
    """
    Tổng hợp các chỉ tiêu BCTC 5 năm.
    ticker: dùng để detect ngân hàng/tài chính và chọn logic revenue phù hợp.
    """
    data = {}
    is_bank = ticker in BANK_TICKERS if ticker else False
    is_financial = ticker in FINANCIAL_TICKERS if ticker else False

    # --- Revenue: xử lý riêng cho ngân hàng/tài chính ---
    if is_bank or is_financial:
        data['revenue'] = _find_revenue_for_bank(df_income)
    else:
        # Doanh nghiệp thông thường
        data['revenue'] = find_row_series(
            df_income,
            [
                'doanh thu thuần', 'net revenue', 'net sales', 'revenue',
                'doanh thu bán hàng', 'tổng doanh thu', 'total revenue',
            ],
            exclude_keywords=['giá vốn', 'cost of', 'chi phí lãi'],
            item_ids=['revenue', 'net_revenue', 'net_sales'])

        # Nếu vẫn không có (mã không xác định được ngành) -> thử bank keywords
        if data['revenue'].empty:
            data['revenue'] = _find_revenue_for_bank(df_income)

    # --- Net profit ---
    data['net_profit'] = find_row_series(
        df_income,
        ['lợi nhuận sau thuế', 'net profit', 'profit after tax', 'net income',
         'lợi nhuận thuần', 'lãi sau thuế'],
        exclude_keywords=['trước thuế', 'before tax', 'thiểu số', 'minority'],
        item_ids=['net_profit', 'net_profit_after_tax', 'profit_after_tax'])

    data['eps_income_stmt'] = find_row_series(
        df_income,
        ['lãi cơ bản trên cổ phiếu', 'earnings per share', 'eps'],
        item_ids=['eps'])

    # --- Balance sheet ---
    data['equity'] = find_row_series(
        df_balance,
        ['vốn chủ sở hữu', "owner's equity", 'owners equity', 'total equity',
         'equity', 'vcsh'],
        exclude_keywords=['vốn điều lệ', 'charter', 'cổ phần ưu đãi'])

    data['total_assets'] = find_row_series(
        df_balance,
        ['tổng cộng tài sản', 'total assets', 'tổng tài sản'])

    # --- Ratio ---
    if df_ratio is not None and not df_ratio.empty:
        data['eps']    = find_row_series(df_ratio, ['eps', 'earning per share', 'earnings per share'])
        data['bvps']   = find_row_series(df_ratio, ['book value per share', 'bvps'])
        data['roe']    = find_row_series(df_ratio, ['roe'])
        data['roa']    = find_row_series(df_ratio, ['roa'])
        data['pe']     = find_row_series(df_ratio, ['p/e', 'pe ratio', ' pe '])
        data['pb']     = find_row_series(df_ratio, ['p/b', 'pb ratio', ' pb '])
        data['market_cap'] = find_row_series(df_ratio, ['market cap', 'vốn hóa'],
                                             item_ids=['market_cap'])
        data['outstanding_shares'] = find_row_series(
            df_ratio,
            ['outstanding shares', 'số cổ phiếu lưu hành', 'số lượng cổ phiếu'],
            item_ids=['outstanding_shares', 'issue_share'])
        data['ev_ebitda']      = find_row_series(df_ratio, ['ev/ebitda', 'ev to ebitda'])
        data['p_cf']           = find_row_series(df_ratio, ['price to cash flow', 'p/cf'])
        data['net_margin']     = find_row_series(df_ratio, ['net margin', 'after tax profit margin', 'biên lợi nhuận sau thuế'])
        data['asset_turnover'] = find_row_series(df_ratio, ['asset turnover', 'vòng quay tài sản', 'vòng quay tổng tài sản'])
    else:
        for k in ['eps', 'bvps', 'roe', 'roa', 'pe', 'pb', 'market_cap',
                  'outstanding_shares', 'ev_ebitda', 'p_cf', 'net_margin', 'asset_turnover']:
            data[k] = pd.Series(dtype=float)

    if data['eps'].empty and not data['eps_income_stmt'].empty:
        data['eps'] = data['eps_income_stmt']

    if data['bvps'].empty and not data['equity'].empty and not data['outstanding_shares'].empty:
        common_years = data['equity'].index.intersection(data['outstanding_shares'].index)
        if len(common_years) > 0:
            data['bvps'] = (data['equity'].loc[common_years] / data['outstanding_shares'].loc[common_years])

    return data


def normalize_to_billion_vnd(series: pd.Series, label=""):
    if series is None or series.empty:
        return series
    median_abs = series.abs().median()
    if median_abs > 10_000_000:
        return series / 1e9
    return series


def get_latest(series: pd.Series, default=0.0):
    if series is None or series.empty:
        return default
    return float(series.iloc[-1])


def get_latest_n_years(series: pd.Series, n=5):
    if series is None or series.empty:
        return series
    return series.iloc[-n:]


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
