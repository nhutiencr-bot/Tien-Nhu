"""
financial_normalizer.py
------------------------
vnstock (cả VCI và KBS) trả income_statement()/balance_sheet()/ratio() theo
format: mỗi DÒNG là 1 chỉ tiêu (cột 'item'/'item_en'/'item_id'), mỗi CỘT
còn lại là 1 NĂM (ví dụ '2021', '2022', ..., '2025').

Module này dò đúng dòng theo từ khoá, rồi trả ra 1 pandas Series có index
là năm (int, tăng dần) và value là số liệu -- dùng chung cho mọi chỗ cần
dữ liệu 5 năm (KQKD, DuPont, 9PP định giá, DCF...).
"""

import pandas as pd
import re


def _get_year_columns(df: pd.DataFrame):
    """Lấy danh sách cột là năm (vd '2021', '2022'...), bỏ cột metadata."""
    meta_cols = {'item', 'item_en', 'item_id'}
    year_cols = []
    for c in df.columns:
        if c in meta_cols:
            continue
        # Cột năm thường là string/int dạng '2021', đôi khi 'Q1/2021' (quý)
        c_str = str(c).strip()
        if re.fullmatch(r'\d{4}', c_str):
            year_cols.append(c)
    # Sắp xếp theo năm tăng dần
    year_cols = sorted(year_cols, key=lambda x: int(str(x).strip()))
    return year_cols


def find_row_series(df: pd.DataFrame, keywords, exclude_keywords=None):
    """
    Dò 1 dòng trong df (format item-theo-dòng) khớp với bất kỳ từ khoá nào
    trong `keywords` (tìm trong cả item, item_en, item_id - không phân biệt
    hoa thường, không phân biệt dấu tiếng Việt cơ bản).

    Trả về pandas Series index=năm (int), value=số liệu (float), đã sort
    theo năm tăng dần. Trả Series rỗng nếu không tìm được.
    """
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

    # Lấy dòng khớp đầu tiên (ưu tiên dòng có ít NaN nhất nếu có nhiều khớp)
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
    """
    Tổng hợp các chỉ tiêu cần cho KQKD 5 năm + phân tích cơ bản, dò từ
    income_statement / balance_sheet / ratio (nếu có).

    Trả về dict gồm các pandas Series (index = năm):
        revenue, net_profit, equity, total_assets, eps, bvps, roe, roa,
        pe, pb, market_cap, outstanding_shares, ev_ebitda, p_cf,
        operating_cash_flow
    Series rỗng nếu không dò được -- nơi gọi cần tự xử lý fallback.
    """
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
