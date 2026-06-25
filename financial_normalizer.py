"""
financial_normalizer.py
"""

import pandas as pd
import re


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


def find_row_series(df: pd.DataFrame, keywords, exclude_keywords=None):
    if df is None or df.empty:
        return pd.Series(dtype=float)

    year_cols = _get_year_columns(df)
    if not year_cols:
        return pd.Series(dtype=float)

    search_cols = [c for c in ['item', 'item_en', 'item_id'] if c in df.columns]
    if not search_cols:
        return pd.Series(dtype=float)

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
        non_na_counts = matched[year_cols].notna().sum(axis=1)
        row = matched.loc[non_na_counts.idxmax()]

    result = {}
    for yc in year_cols:
        val = pd.to_numeric(pd.Series([row[yc]]), errors='coerce').iloc[0]
        if pd.notna(val):
            result[int(str(yc).strip())] = float(val)

    return pd.Series(result).sort_index()


def build_5y_financial_table(df_income, df_balance, df_ratio=None):
    data = {}

    # --- Từ income_statement ---
    data['revenue'] = find_row_series(
        df_income, ['doanh thu thuần', 'net revenue', 'net sales', 'revenue'],
        exclude_keywords=['giá vốn', 'cost of'])
    data['net_profit'] = find_row_series(
        df_income,
        ['lợi nhuận sau thuế', 'net profit', 'profit after tax', 'net income'],
        exclude_keywords=['trước thuế', 'before tax', 'thiểu số', 'minority'])
    data['eps_income_stmt'] = find_row_series(
        df_income, ['lãi cơ bản trên cổ phiếu', 'earnings per share', 'eps'])

    # --- Từ balance_sheet ---
    data['equity'] = find_row_series(
        df_balance,
        [
            'vốn chủ sở hữu', "owner's equity", 'owners equity', 'total equity',
            'equity', 'vốn csh', 'vcsh', 'shareholders equity',
            'stockholders equity', 'net assets', 'book value',
            'total stockholders', 'total shareholders'
        ],
        exclude_keywords=['vốn điều lệ', 'charter', 'minority', 'thiểu số'])

    data['total_assets'] = find_row_series(
        df_balance,
        [
            'tổng cộng tài sản', 'total assets', 'tổng tài sản',
            'assets', 'tổng cộng nguồn vốn', 'total nguồn vốn',
            'tổng nguồn vốn', 'total liabilities and equity',
            'total liabilities and stockholders'
        ])

    # --- Từ ratio() ---
    if df_ratio is not None and not df_ratio.empty:
        data['eps'] = find_row_series(df_ratio, ['eps', 'earning per share', 'earnings per share'])
        data['bvps'] = find_row_series(df_ratio, ['book value per share', 'bvps'])
        data['roe'] = find_row_series(df_ratio, ['roe'])
        data['roa'] = find_row_series(df_ratio, ['roa'])
        data['pe'] = find_row_series(df_ratio, ['p/e', 'pe ratio', ' pe '])
        data['pb'] = find_row_series(df_ratio, ['p/b', 'pb ratio', ' pb '])
        data['market_cap'] = find_row_series(df_ratio, ['market cap', 'vốn hóa'])
        data['outstanding_shares'] = find_row_series(df_ratio, ['outstanding shares', 'số cổ phiếu lưu hành', 'số lượng cổ phiếu'])
        data['ev_ebitda'] = find_row_series(df_ratio, ['ev/ebitda', 'ev to ebitda'])
        data['p_cf'] = find_row_series(df_ratio, ['price to cash flow', 'p/cf'])
        data['net_margin'] = find_row_series(df_ratio, ['net margin', 'after tax profit margin', 'biên lợi nhuận sau thuế'])
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
