"""
financial_normalizer.py
FIX: Hỗ trợ toàn bộ ngành nghề (bán lẻ, thực phẩm, bất động sản, xây dựng,
     điện, vận tải, dược, viễn thông...) — không bỏ sót mã nào trong 1403+ mã.
"""

import pandas as pd
import re

# ============================================================
# 1. PHÂN LOẠI NGÀNH — dùng để chọn logic chuẩn hoá revenue
# ============================================================

# Ngân hàng thương mại (thu nhập lãi thuần thay doanh thu)
BANK_TICKERS = {
    'VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB', 'VPB', 'HDB', 'TPB',
    'MSB', 'OCB', 'VIB', 'SHB', 'EIB', 'LPB', 'SSB', 'NAB', 'ABB', 'BAB',
    'BVB', 'KLB', 'PGB', 'VAB', 'VBB', 'SGN', 'NVB', 'SGB', 'CBB', 'SEAB',
}

# Bảo hiểm & chứng khoán
FINANCIAL_TICKERS = {
    # Bảo hiểm
    'BVH', 'PVI', 'PTI', 'MIG', 'BMI', 'VNR', 'BIC', 'PRE', 'PGI',
    # Chứng khoán
    'SSI', 'VND', 'HCM', 'MBS', 'VCI', 'FTS', 'AGR', 'SBS', 'BSI',
    'CTS', 'TVS', 'APS', 'TCI', 'BMS', 'VDS', 'WSS', 'DSC', 'PSI',
    'ORS', 'EVS',
}

# Bất động sản (doanh thu từ bàn giao/cho thuê — thường ghi "doanh thu bán hàng"
# nhưng đặc thù là trị giá lớn và không đều theo quý)
REALESTATE_TICKERS = {
    'VHM', 'NVL', 'PDR', 'DXG', 'KDH', 'NLG', 'SCR', 'BCM', 'TDH', 'HDG',
    'CEO', 'DIG', 'LDG', 'VRC', 'ITA', 'CII', 'HQC', 'SJS', 'NBB', 'ROS',
    'DRH', 'TSC', 'AGG', 'TIP', 'OGC', 'KBC', 'IDC', 'HID', 'D2D', 'TN1',
    'VPI', 'GEX', 'PXL', 'SGR', 'HBC', 'LHG', 'DLG', 'TCH',
}

# Map ngành → nhóm logic để dùng trong hàm detect
_TICKER_INDUSTRY_MAP = {}
for t in BANK_TICKERS:       _TICKER_INDUSTRY_MAP[t] = 'bank'
for t in FINANCIAL_TICKERS:  _TICKER_INDUSTRY_MAP[t] = 'financial'
for t in REALESTATE_TICKERS: _TICKER_INDUSTRY_MAP[t] = 'realestate'


def get_industry_group(ticker: str) -> str:
    """
    Trả về nhóm ngành: 'bank' | 'financial' | 'realestate' | 'general'
    'general' bao gồm: bán lẻ, thực phẩm, xây dựng, điện, dược, viễn thông...
    """
    if ticker is None:
        return 'general'
    return _TICKER_INDUSTRY_MAP.get(ticker.strip().upper(), 'general')


# ============================================================
# 2. TIỆN ÍCH XỬ LÝ CỘT NĂM / QUÝ
# ============================================================

def _get_year_columns(df: pd.DataFrame):
    meta_cols = {'item', 'item_en', 'item_id'}
    year_cols = []
    for c in df.columns:
        if c in meta_cols:
            continue
        c_str = str(c).strip()
        if re.fullmatch(r'\d{4}', c_str):
            year_cols.append(c)
    return sorted(year_cols, key=lambda x: int(str(x).strip()))


def _quarter_sort_key(c):
    y, q = str(c).strip().split('-Q')
    return (int(y), int(q))


def _get_quarter_columns(df: pd.DataFrame):
    meta_cols = {'item', 'item_en', 'item_id'}
    q_cols = []
    for c in df.columns:
        if c in meta_cols:
            continue
        c_str = str(c).strip()
        if re.fullmatch(r'\d{4}-Q[1-4]', c_str):
            q_cols.append(c)
    return sorted(q_cols, key=_quarter_sort_key)


# ============================================================
# 3. HÀM TÌM DÒNG (core engine)
# ============================================================

def find_row_series(df: pd.DataFrame, keywords, exclude_keywords=None,
                    item_ids=None, prefer_top_level=True, period='year'):
    """
    Tìm series số liệu trong DataFrame BCTC.

    period='year'    -> cột dạng 'YYYY', key là int năm.
    period='quarter' -> cột dạng 'YYYY-Qn', key là str 'YYYY-Qn'.
    """
    if df is None or df.empty:
        return pd.Series(dtype=float)

    year_cols = _get_quarter_columns(df) if period == 'quarter' else _get_year_columns(df)
    if not year_cols:
        return pd.Series(dtype=float)

    search_cols = [c for c in ['item', 'item_en', 'item_id'] if c in df.columns]
    if not search_cols:
        return pd.Series(dtype=float)

    matched = pd.DataFrame()

    # Bước 1: khớp chính xác theo item_id
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
            if period == 'quarter':
                result[str(yc).strip()] = float(val)
            else:
                result[int(str(yc).strip())] = float(val)

    if period == 'quarter':
        ordered_keys = sorted(result.keys(), key=_quarter_sort_key)
        return pd.Series({k: result[k] for k in ordered_keys})
    return pd.Series(result).sort_index()


# ============================================================
# 4. LOGIC REVENUE THEO NGÀNH
# ============================================================

def _find_revenue_for_bank(df_income, period='year'):
    """Ngân hàng/bảo hiểm/chứng khoán: ưu tiên thu nhập hoạt động."""
    bank_revenue_keywords = [
        (['tổng thu nhập hoạt động', 'total operating income', 'net operating income'],
         ['chi phí', 'expense']),
        (['thu nhập lãi thuần', 'net interest income', 'lãi thuần'], ['chi phí lãi']),
        (['thu nhập thuần', 'net income from', 'total net income'], ['lợi nhuận', 'profit']),
        (['tổng doanh thu', 'total revenue', 'gross revenue'], []),
        (['doanh thu hoạt động', 'operating revenue'], []),
        (['thu nhập từ lãi', 'interest income', 'interest and similar income'], ['chi phí']),
    ]
    for keywords, excludes in bank_revenue_keywords:
        s = find_row_series(df_income, keywords,
                            exclude_keywords=excludes if excludes else None,
                            period=period)
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def _find_revenue_general(df_income, period='year'):
    """
    DN thông thường (bán lẻ, thực phẩm, xây dựng, điện, dược, viễn thông...):
    tìm doanh thu thuần / net revenue với nhiều biến thể nhãn khác nhau.
    Nếu không tìm được, fallback về tổng doanh thu hoặc doanh thu bán hàng.
    """
    # Ưu tiên 1: Doanh thu thuần (loại trừ các dòng con)
    candidates = [
        (
            ['doanh thu thuần', 'net revenue', 'net sales',
             'doanh thu bán hàng và cung cấp dịch vụ',
             'revenue from sale of goods and rendering of services',
             'tổng doanh thu', 'total revenue', 'gross revenue',
             'doanh thu hoạt động', 'operating revenue',
             'revenues', 'revenue'],
            ['giá vốn', 'cost of', 'chi phí lãi', 'interest expense',
             'giảm trừ', 'deduction', 'chiết khấu'],
            ['revenue', 'net_revenue', 'net_sales',
             'revenue_net', 'sales_revenue', 'total_revenue'],
        ),
        # Ưu tiên 2: nếu không có, thử lấy doanh thu bán hàng (trước giảm trừ)
        (
            ['doanh thu bán hàng', 'sales revenue', 'gross sales',
             'doanh thu từ hoạt động kinh doanh'],
            ['giá vốn', 'cost of'],
            ['gross_revenue', 'sales'],
        ),
    ]
    for kws, excludes, item_ids in candidates:
        s = find_row_series(df_income, kws,
                            exclude_keywords=excludes,
                            item_ids=item_ids,
                            period=period)
        if not s.empty:
            return s

    # Fallback cuối: bất kỳ dòng nào chứa "doanh thu" hoặc "revenue"
    s = find_row_series(df_income,
                        ['doanh thu', 'revenue', 'sales'],
                        exclude_keywords=['giá vốn', 'cost of goods', 'chi phí lãi'],
                        period=period)
    return s


def _find_revenue_realestate(df_income, period='year'):
    """
    BĐS: doanh thu nhận diện từ bàn giao BĐS / cho thuê.
    Thường cùng nhãn với DN thông thường nhưng có thể có thêm "doanh thu từ BĐS".
    """
    candidates_re = [
        ['doanh thu từ bất động sản', 'doanh thu bán bất động sản',
         'real estate revenue', 'property sales revenue'],
        ['doanh thu thuần', 'net revenue', 'net sales'],
        ['tổng doanh thu', 'total revenue', 'gross revenue'],
        ['doanh thu bán hàng', 'sales revenue'],
        ['doanh thu', 'revenue'],
    ]
    for kws in candidates_re:
        s = find_row_series(df_income, kws,
                            exclude_keywords=['giá vốn', 'cost of', 'chi phí lãi'],
                            period=period)
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def _find_revenue(df_income, industry_group: str, period='year'):
    """Router chọn hàm revenue theo nhóm ngành."""
    if industry_group in ('bank', 'financial'):
        return _find_revenue_for_bank(df_income, period=period)
    if industry_group == 'realestate':
        s = _find_revenue_realestate(df_income, period=period)
        if s.empty:
            s = _find_revenue_general(df_income, period=period)
        return s
    # general: bán lẻ, thực phẩm, xây dựng, điện, dược, viễn thông, v.v.
    s = _find_revenue_general(df_income, period=period)
    if s.empty:
        # Fallback về bank logic để bắt mọi trường hợp còn lại
        s = _find_revenue_for_bank(df_income, period=period)
    return s


# ============================================================
# 5. HÀM TỔNG HỢP BCTC
# ============================================================

def build_financial_table(df_income, df_balance, df_ratio=None,
                          ticker=None, period='year'):
    """
    Tổng hợp các chỉ tiêu BCTC cho MỌI ngành nghề.

    ticker : dùng để detect nhóm ngành và chọn logic revenue phù hợp.
    period : 'year' hoặc 'quarter'.
    """
    data = {}

    industry_group = get_industry_group(ticker)

    # --- Revenue ---
    data['revenue'] = _find_revenue(df_income, industry_group, period=period)

    # --- Net profit ---
    data['net_profit'] = find_row_series(
        df_income,
        ['lợi nhuận sau thuế', 'net profit', 'profit after tax', 'net income',
         'lợi nhuận thuần', 'lãi sau thuế', 'profit for the period',
         'lợi nhuận của cổ đông công ty mẹ'],
        exclude_keywords=['trước thuế', 'before tax', 'thiểu số', 'minority',
                          'cổ đông thiểu số'],
        item_ids=['net_profit', 'net_profit_after_tax', 'profit_after_tax'],
        period=period)

    data['eps_income_stmt'] = find_row_series(
        df_income,
        ['lãi cơ bản trên cổ phiếu', 'earnings per share', 'eps',
         'lãi trên cổ phiếu'],
        item_ids=['eps'],
        period=period)

    # --- Balance sheet ---
    data['equity'] = find_row_series(
        df_balance,
        ['vốn chủ sở hữu', "owner's equity", 'owners equity', 'total equity',
         'equity', 'vcsh', 'total shareholders equity',
         'tổng vốn chủ sở hữu'],
        exclude_keywords=['vốn điều lệ', 'charter', 'cổ phần ưu đãi'],
        period=period)

    data['total_assets'] = find_row_series(
        df_balance,
        ['tổng cộng tài sản', 'total assets', 'tổng tài sản',
         'total assets and liabilities'],
        period=period)

    # --- Chỉ tiêu bổ sung từ balance sheet ---
    data['total_debt'] = find_row_series(
        df_balance,
        ['nợ phải trả', 'total liabilities', 'total debt', 'tổng nợ'],
        exclude_keywords=['vốn chủ'],
        period=period)

    data['cash'] = find_row_series(
        df_balance,
        ['tiền và tương đương tiền', 'cash and cash equivalents',
         'tiền mặt', 'cash'],
        period=period)

    # --- Ratio ---
    if df_ratio is not None and not df_ratio.empty:
        data['eps'] = find_row_series(
            df_ratio,
            ['eps', 'earning per share', 'earnings per share'],
            period=period)
        data['bvps'] = find_row_series(
            df_ratio,
            ['book value per share', 'bvps', 'giá trị sổ sách'],
            period=period)
        data['roe'] = find_row_series(df_ratio, ['roe'], period=period)
        data['roa'] = find_row_series(df_ratio, ['roa'], period=period)
        data['pe']  = find_row_series(
            df_ratio, ['p/e', 'pe ratio', ' pe '], period=period)
        data['pb']  = find_row_series(
            df_ratio, ['p/b', 'pb ratio', ' pb '], period=period)
        data['market_cap'] = find_row_series(
            df_ratio,
            ['market cap', 'vốn hóa', 'market capitalization'],
            item_ids=['market_cap'],
            period=period)
        data['outstanding_shares'] = find_row_series(
            df_ratio,
            ['outstanding shares', 'số cổ phiếu lưu hành',
             'số lượng cổ phiếu', 'shares outstanding'],
            item_ids=['outstanding_shares', 'issue_share'],
            period=period)
        data['ev_ebitda'] = find_row_series(
            df_ratio, ['ev/ebitda', 'ev to ebitda'], period=period)
        data['p_cf'] = find_row_series(
            df_ratio, ['price to cash flow', 'p/cf'], period=period)
        data['ps'] = find_row_series(
            df_ratio, ['p/s', 'price to sales', 'ps ratio'], period=period)
        data['net_margin'] = find_row_series(
            df_ratio,
            ['net margin', 'after tax profit margin',
             'biên lợi nhuận sau thuế', 'profit margin'],
            period=period)
        data['asset_turnover'] = find_row_series(
            df_ratio,
            ['asset turnover', 'vòng quay tài sản',
             'vòng quay tổng tài sản'],
            period=period)
        data['dps'] = find_row_series(
            df_ratio,
            ['dividend per share', 'cổ tức trên mỗi cổ phiếu',
             'cổ tức tiền mặt', 'dps'],
            period=period)
    else:
        for k in ['eps', 'bvps', 'roe', 'roa', 'pe', 'pb', 'market_cap',
                  'outstanding_shares', 'ev_ebitda', 'p_cf', 'ps',
                  'net_margin', 'asset_turnover', 'dps']:
            data[k] = pd.Series(dtype=float)

    # Fallback: EPS từ KQKD nếu ratio không có
    if data['eps'].empty and not data['eps_income_stmt'].empty:
        data['eps'] = data['eps_income_stmt']

    # Tính BVPS thủ công nếu thiếu
    if (data['bvps'].empty
            and not data['equity'].empty
            and not data['outstanding_shares'].empty):
        common_years = data['equity'].index.intersection(
            data['outstanding_shares'].index)
        if len(common_years) > 0:
            data['bvps'] = (
                data['equity'].loc[common_years]
                / data['outstanding_shares'].loc[common_years]
            )

    return data


# ============================================================
# 6. WRAPPER TƯƠNG THÍCH NGƯỢC
# ============================================================

def build_5y_financial_table(df_income, df_balance, df_ratio=None, ticker=None):
    """Giữ tương thích ngược: bảng theo năm."""
    return build_financial_table(df_income, df_balance, df_ratio,
                                  ticker=ticker, period='year')


# ============================================================
# 7. TIỆN ÍCH SỐ LIỆU
# ============================================================

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


# ============================================================
# 8. ĐỊNH GIÁ
# ============================================================

def ddm_gordon(dps, required_return=0.11, g=0.04):
    """Gordon Growth Model. Cổ tức phải dương và ke > g."""
    if dps is None or dps <= 0 or required_return <= g:
        return None
    return (dps * (1 + g)) / (required_return - g)


def graham_number(eps, bvps):
    """Graham Number. EPS và BVPS phải > 0."""
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return (22.5 * eps * bvps) ** 0.5


def advanced_multiples_valuation(eps_latest, eps_5y_ago, pe_current,
                                  ebitda_latest, cfo_latest,
                                  revenue_latest, net_debt_latest,
                                  shares_outstanding,
                                  ev_ebitda_median_5y,
                                  pcf_median_5y, ps_median_5y):
    """EV/EBITDA, P/CF, P/S và PEG."""
    methods = {}

    shares_billion = shares_outstanding / 1e9 if shares_outstanding else 0
    if shares_billion <= 0:
        return methods

    if ebitda_latest and ebitda_latest > 0 and ev_ebitda_median_5y:
        fair_ev = ebitda_latest * ev_ebitda_median_5y
        fair_market_cap = fair_ev - (net_debt_latest or 0)
        if fair_market_cap > 0:
            methods['EV/EBITDA Median 5N'] = fair_market_cap / shares_billion

    if cfo_latest and cfo_latest > 0 and pcf_median_5y:
        methods['P/CF Median 5N'] = (cfo_latest * pcf_median_5y) / shares_billion

    if revenue_latest and revenue_latest > 0 and ps_median_5y:
        methods['P/S Median 5N'] = (revenue_latest * ps_median_5y) / shares_billion

    if (eps_latest and eps_5y_ago and eps_5y_ago > 0
            and eps_latest > eps_5y_ago and pe_current):
        eps_growth = ((eps_latest / eps_5y_ago) ** 0.25 - 1) * 100
        if eps_growth > 0:
            methods['PEG Fair Value'] = eps_latest * max(eps_growth, 1)
            methods['_PEG_Ratio'] = pe_current / max(eps_growth, 1)

    return methods


def nine_methods_valuation(eps_latest, bvps_latest,
                            pe_series: pd.Series,
                            pb_series: pd.Series, current_price):
    """Tổng hợp 9 phương pháp định giá."""
    methods = {}
    # (Gọi DDM, Graham và Advanced Multiples ở đây và gom vào methods)
    return methods
