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


def find_row_series(df: pd.DataFrame, keywords, exclude_keywords=None, item_ids=None,
                     prefer_top_level=True):
    """
    Dò 1 dòng trong df (format item-theo-dòng) khớp với dữ liệu cần lấy.

    ⚠️ BẪY ITEM_ID KHÔNG ĐOÁN ĐƯỢC: với KBS, item_id không phải lúc nào
    cũng là tên tiếng Anh chuẩn đẹp (vd 'equity') -- nhiều dòng quan trọng
    (như "B. VỐN CHỦ SỞ HỮU (400=410+420)") có item_id là slug tự sinh từ
    tiêu đề gốc (vd 'b_von_chu_so_huu_400410420'), không thể đoán trước.
    => `item_ids` giờ chỉ là gợi ý PHỤ (thử trước nếu khớp chính xác), còn
    cơ chế chính vẫn là dò từ khoá trong item/item_en.

    ⚠️ BẪY DÒNG CON TRÙNG TỪ KHOÁ: BCTC có cấu trúc phân cấp (vd "B. VỐN
    CHỦ SỞ HỮU" là dòng TỔNG cấp cao, nhưng "I. Vốn chủ sở hữu" lại là 1
    dòng CON bên trong, cùng chứa từ khoá "vốn chủ sở hữu"). Nếu nhiều
    dòng khớp từ khoá, ưu tiên dòng có cột 'levels' THẤP NHẤT (cấp càng
    cao/tổng quát thường có levels nhỏ hơn) -- đây đáng tin hơn heuristic
    "ít NaN nhất" vì dòng con đôi khi cũng đầy đủ số liệu không kém dòng tổng.

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

    matched = pd.DataFrame()

    # --- Bước 1: thử khớp chính xác theo item_id (gợi ý phụ, có thể miss) ---
    if item_ids and 'item_id' in df.columns:
        item_id_lower = df['item_id'].astype(str).str.lower().str.strip()
        target_ids = [i.lower().strip() for i in item_ids]
        mask_exact = item_id_lower.isin(target_ids)
        if mask_exact.any():
            matched = df[mask_exact]

    # --- Bước 2: fallback dò từ khoá trong text (item/item_en/item_id) ---
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

    # Chọn dòng đại diện khi có nhiều dòng khớp
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
                    # Vẫn nhiều dòng cùng level thấp nhất -> tie-break bằng
                    # số liệu non-NaN nhiều nhất trong nhóm đó
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
    # item_ids: ưu tiên khớp chính xác theo chuẩn hoá item_id của KBS
    # (_INCOME_STATEMENT_MAP) và tên tương đương dò được trên VCI.
    data['revenue'] = find_row_series(
        df_income,
        [
            'doanh thu thuần', 'net revenue', 'net sales', 'revenue',
            # Ngân hàng/bảo hiểm không có "doanh thu thuần" -> dùng thu nhập hoạt động
            'thu nhập lãi thuần', 'net interest income',
            'tổng thu nhập hoạt động', 'total operating income',
            'thu nhập từ hoạt động', 'operating revenue',
            'tổng doanh thu', 'total revenue',
        ],
        exclude_keywords=['giá vốn', 'cost of', 'chi phí lãi'])
        item_ids=['revenue', 'net_revenue', 'operating_income', 'net_sales'])
    data['net_profit'] = find_row_series(
        df_income,
        ['lợi nhuận sau thuế', 'net profit', 'profit after tax', 'net income'],
        exclude_keywords=['trước thuế', 'before tax', 'thiểu số', 'minority'],
        item_ids=['net_profit', 'net_profit_after_tax', 'profit_after_tax'])
    data['eps_income_stmt'] = find_row_series(
        df_income, ['lãi cơ bản trên cổ phiếu', 'earnings per share', 'eps'],
        item_ids=['eps'])

    # --- Từ balance_sheet ---
    # ⚠️ Không dùng item_ids cho equity/total_assets: item_id thật của KBS
    # là slug tự sinh từ tiêu đề gốc (vd 'b_von_chu_so_huu_400410420'),
    # không đoán trước được -> dò hoàn toàn theo từ khoá + ưu tiên cấp
    # 'levels' thấp nhất (dòng TỔNG, không phải dòng con) trong find_row_series.
    data['equity'] = find_row_series(
        df_balance,
        ['vốn chủ sở hữu', "owner's equity", 'owners equity', 'total equity'],
        exclude_keywords=['vốn điều lệ', 'charter', 'cổ phần ưu đãi'])
    data['total_assets'] = find_row_series(
        df_balance, ['tổng cộng tài sản', 'total assets', 'tổng tài sản'])

    # --- Từ ratio() nếu có (ưu tiên vì đã tính sẵn, ít lỗi hơn tự tính) ---
    if df_ratio is not None and not df_ratio.empty:
        data['eps'] = find_row_series(df_ratio, ['eps', 'earning per share', 'earnings per share'])
        data['bvps'] = find_row_series(df_ratio, ['book value per share', 'bvps'])
        data['roe'] = find_row_series(df_ratio, ['roe'])
        data['roa'] = find_row_series(df_ratio, ['roa'])
        data['pe'] = find_row_series(df_ratio, ['p/e', 'pe ratio', ' pe '])
        data['pb'] = find_row_series(df_ratio, ['p/b', 'pb ratio', ' pb '])
        data['market_cap'] = find_row_series(df_ratio, ['market cap', 'vốn hóa'], item_ids=['market_cap'])
        data['outstanding_shares'] = find_row_series(df_ratio, ['outstanding shares', 'số cổ phiếu lưu hành', 'số lượng cổ phiếu'], item_ids=['outstanding_shares', 'issue_share'])
        data['ev_ebitda'] = find_row_series(df_ratio, ['ev/ebitda', 'ev to ebitda'])
        data['p_cf'] = find_row_series(df_ratio, ['price to cash flow', 'p/cf'])
        data['net_margin'] = find_row_series(df_ratio, ['net margin', 'after tax profit margin', 'biên lợi nhuận sau thuế'])
        data['asset_turnover'] = find_row_series(df_ratio, ['asset turnover', 'vòng quay tài sản', 'vòng quay tổng tài sản'])
    else:
        for k in ['eps', 'bvps', 'roe', 'roa', 'pe', 'pb', 'market_cap',
                  'outstanding_shares', 'ev_ebitda', 'p_cf', 'net_margin', 'asset_turnover']:
            data[k] = pd.Series(dtype=float)

    # Nếu ratio() không có EPS/BVPS, fallback dùng EPS từ income_statement
    # và tự tính BVPS = equity / outstanding_shares (nếu có outstanding_shares)
    if data['eps'].empty and not data['eps_income_stmt'].empty:
        data['eps'] = data['eps_income_stmt']

    if data['bvps'].empty and not data['equity'].empty and not data['outstanding_shares'].empty:
        common_years = data['equity'].index.intersection(data['outstanding_shares'].index)
        if len(common_years) > 0:
            data['bvps'] = (data['equity'].loc[common_years] / data['outstanding_shares'].loc[common_years])

    return data


def normalize_to_billion_vnd(series: pd.Series, label=""):
    """
    ⚠️ BẪY ĐƠN VỊ ĐA NGUỒN: VCI trả income_statement/balance_sheet theo
    đơn vị TỶ ĐỒNG, nhưng KBS áp thêm unit_multiplier=1000 nội bộ khiến
    giá trị trả về ở đơn vị ĐỒNG TUYỆT ĐỐI (gấp ~1 tỷ lần tỷ đồng thật).

    Heuristic phát hiện: với 1 doanh nghiệp niêm yết thật, doanh thu/lợi
    nhuận tính bằng TỶ ĐỒNG hợp lý thường nằm trong khoảng vài chục đến
    vài trăm nghìn (tỷ). Nếu giá trị tuyệt đối trung vị > 10 triệu, gần
    như chắc chắn đang ở đơn vị ĐỒNG (chưa quy về tỷ) -> tự chia 1e9.

    Trả về Series đã chuẩn hoá về tỷ đồng (không sửa inplace).
    """
    if series is None or series.empty:
        return series
    median_abs = series.abs().median()
    if median_abs > 10_000_000:  # > 10 triệu -> chắc chắn đang là đồng tuyệt đối
        return series / 1e9
    return series



def get_latest(series: pd.Series, default=0.0):
    """Lấy giá trị năm gần nhất của 1 Series (đã sort theo năm), an toàn nếu rỗng."""
    if series is None or series.empty:
        return default
    return float(series.iloc[-1])


def get_latest_n_years(series: pd.Series, n=5):
    """Lấy n năm gần nhất của Series (đã sort theo năm tăng dần)."""
    if series is None or series.empty:
        return series
    return series.iloc[-n:]


def cagr(series: pd.Series, n_years=None):
    """
    Tính CAGR (Compound Annual Growth Rate) từ giá trị đầu đến giá trị
    cuối của Series. n_years: số năm giữa 2 mốc; nếu None, tự tính từ
    số lượng điểm dữ liệu - 1.
    Trả về None nếu không tính được (thiếu data, giá trị đầu <= 0...).
    """
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
