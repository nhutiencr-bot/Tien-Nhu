"""
financial_normalizer.py
------------------------
Các sửa đổi chính so với bản trước:
  1. FIX CRITICAL: _get_year_columns() có dead code — return thứ 2 không bao giờ chạy
     vì return thứ 1 đã exit function. Đã gộp lại thành 1 return duy nhất.
  2. Thêm RETAIL_TICKERS vào nhóm detect — keyword revenue bán lẻ đặc thù
  3. REAL_ESTATE_TICKERS: dùng keyword "doanh thu cho thuê"
  4. build_5y_financial_table() nhận ticker và truyền xuống build_financial_table()
  5. find_row_series() ưu tiên dòng có nhiều data nhất (không bỏ sót năm 2021)
  6. _find_revenue_for_retail(): hàm riêng cho bán lẻ/phân phối
  7. Phân loại ngành (bank/financial/retail/real_estate) để chọn đúng field
     Doanh thu — khắc phục lỗi ngân hàng bị khớp nhầm dòng "Doanh thu thuần"
     tổng quát ra số vô lý (thay vì "Tổng thu nhập hoạt động"/"Thu nhập lãi
     thuần" đúng bản chất kinh doanh ngân hàng).
"""

import pandas as pd
import re

BANK_TICKERS = {
    'VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB', 'VPB', 'HDB', 'TPB',
    'MSB', 'OCB', 'VIB', 'SHB', 'EIB', 'LPB', 'SSB', 'NAB', 'ABB', 'BAB',
    'BVB', 'KLB', 'PGB', 'VAB', 'VBB', 'SGN', 'NVB', 'SGB', 'CBB', 'SEAB',
}

FINANCIAL_TICKERS = {
    'BVH', 'PVI', 'PTI', 'MIG', 'BMI', 'VNR', 'BIC', 'PRE', 'PGI',
    'SSI', 'VND', 'HCM', 'MBS', 'VCI', 'FTS', 'AGR', 'SBS', 'BSI', 'VPX', 'VCK', 'TCX', 'SHS',
}

SECURITIES_TICKERS = {
    'SSI', 'VND', 'HCM', 'MBS', 'VCI', 'FTS', 'AGR', 'SBS', 'BSI', 'VPX', 'VCK', 'TCX', 'SHS',
}

RETAIL_TICKERS = {
    'MWG', 'FRT', 'DGW', 'PNJ', 'HAX', 'SVC', 'MCH', 'PET',
    'PSD', 'HHS', 'HUT', 'AST', 'PTC',
}

REAL_ESTATE_TICKERS = {'VRE', 'NLG', 'DXG', 'KDH', 'PDR', 'CEO', 'BCM'}

TARGET_YEARS = list(range(2021, 2026))


def _get_year_columns(df: pd.DataFrame):
    """
    Trả về list các cột năm trong df (dạng int hoặc string '2021', '2022'...).
    FIX: Bản cũ có 2 return statements — cái thứ 2 với _year_sort_key không bao giờ chạy.
    Đã gộp lại: sort theo int(str(col)[:4]) để xử lý cả int lẫn string year columns.
    """
    meta_cols = {'item', 'item_en', 'item_id'}
    year_cols = []
    for c in df.columns:
        if c in meta_cols:
            continue
        c_str = str(c).strip()
        # Nhận cả cột int (2021, 2022...) lẫn string '2021'
        if re.fullmatch(r'\d{4}', c_str):
            year_cols.append(c)
    return sorted(year_cols, key=lambda col: int(str(col).strip()[:4]))


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


def find_row_series(df: pd.DataFrame, keywords, exclude_keywords=None,
                    item_ids=None, prefer_top_level=True, period='year'):
    """
    Tìm dòng phù hợp trong DataFrame BCTC vnstock.

    FIX: Khi có nhiều dòng khớp, chọn dòng có nhiều năm data nhất
         (ưu tiên dòng phủ đủ 2021-2025).
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

    if item_ids and 'item_id' in df.columns:
        item_id_lower = df['item_id'].astype(str).str.lower().str.strip()
        target_ids = [i.lower().strip() for i in item_ids]
        mask_exact = item_id_lower.isin(target_ids)
        if mask_exact.any():
            matched = df[mask_exact]

    if matched.empty:
        combined_text = df[search_cols].apply(
            lambda row: ' '.join(str(v) if v is not None else '' for v in row.values),
            axis=1
        ).str.lower()
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
            if period == 'quarter':
                result[str(yc).strip()] = float(val)
            else:
                yr = int(str(yc).strip()[:4])
                result[yr] = float(val)

    if period == 'quarter':
        ordered_keys = sorted(result.keys(), key=_quarter_sort_key)
        return pd.Series({k: result[k] for k in ordered_keys})
    return pd.Series(result).sort_index()


def _find_revenue_for_bank(df_income, period='year'):
    """Ngân hàng/bảo hiểm/chứng khoán — dùng thu nhập thay doanh thu."""
    bank_revenue_keywords = [
        (['doanh thu hoạt động', 'operating revenue'], []),
        (['tổng doanh thu hoạt động'], ['chi phí']),
        (['doanh thu từ hoạt động môi giới', 'brokerage revenue'], []),
        (['phí và hoa hồng', 'fee and commission', 'net fee', 'net commission'], []),
        (['doanh thu thuần từ hoạt động', 'net revenue from operations'], []),
        (['tổng thu nhập hoạt động', 'total operating income', 'net operating income'], ['chi phí', 'expense']),
        (['thu nhập lãi thuần', 'net interest income', 'lãi thuần'], ['chi phí lãi']),
        (['thu nhập thuần', 'net income from', 'total net income'], ['lợi nhuận', 'profit']),
        (['tổng doanh thu', 'total revenue', 'gross revenue'], []),
        (['thu nhập từ lãi', 'interest income'], ['chi phí']),
    ]
    for keywords, excludes in bank_revenue_keywords:
        s = find_row_series(df_income, keywords,
                            exclude_keywords=excludes if excludes else None,
                            period=period)
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def _find_revenue_for_retail(df_income, period='year'):
    """Bán lẻ / phân phối — keyword rộng hơn, không exclude "dịch vụ"."""
    retail_keywords_list = [
        (['doanh thu thuần về bán hàng và cung cấp dịch vụ'], ['giá vốn']),
        (['doanh thu bán hàng và cung cấp dịch vụ'], ['giá vốn']),
        (['doanh thu thuần', 'net revenue'], ['giá vốn', 'chi phí lãi']),
        (['doanh thu bán hàng', 'sales revenue'], ['giá vốn']),
        (['tổng doanh thu', 'total revenue'], ['giá vốn']),
        (['revenue', 'net sales'], ['cost']),
    ]
    for keywords, excludes in retail_keywords_list:
        s = find_row_series(df_income, keywords,
                            exclude_keywords=excludes if excludes else None,
                            period=period)
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def _find_revenue_for_realestate(df_income, period='year'):
    """BĐS cho thuê (VRE, NLG...)."""
    re_keywords_list = [
        (['doanh thu cho thuê', 'rental revenue', 'rental income'], []),
        (['doanh thu bất động sản'], []),
        (['doanh thu thuần', 'net revenue'], ['giá vốn']),
        (['tổng doanh thu', 'total revenue'], []),
    ]
    for keywords, excludes in re_keywords_list:
        s = find_row_series(df_income, keywords,
                            exclude_keywords=excludes if excludes else None,
                            period=period)
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def build_financial_table(df_income, df_balance, df_ratio=None,
                          ticker=None, period='year'):
    """
    Tổng hợp chỉ tiêu BCTC. ticker bắt buộc phải truyền vào để detect ngành.
    """
    data = {}

    is_bank = ticker in BANK_TICKERS if ticker else False
    is_financial = ticker in FINANCIAL_TICKERS if ticker else False
    is_retail = ticker in RETAIL_TICKERS if ticker else False
    is_realestate = ticker in REAL_ESTATE_TICKERS if ticker else False

    if is_bank or is_financial:
        data['revenue'] = _find_revenue_for_bank(df_income, period=period)
    elif is_realestate:
        data['revenue'] = _find_revenue_for_realestate(df_income, period=period)
    elif is_retail:
        data['revenue'] = _find_revenue_for_retail(df_income, period=period)
    else:
        data['revenue'] = find_row_series(
            df_income,
            ['doanh thu thuần', 'net revenue', 'net sales', 'revenue',
             'doanh thu bán hàng', 'tổng doanh thu', 'total revenue'],
            exclude_keywords=['giá vốn', 'cost of', 'chi phí lãi'],
            item_ids=['revenue', 'net_revenue', 'net_sales'],
            period=period
        )
        if data['revenue'].empty:
            data['revenue'] = _find_revenue_for_retail(df_income, period=period)

    data['net_profit'] = find_row_series(
        df_income,
        ['lợi nhuận sau thuế', 'net profit', 'profit after tax', 'net income',
         'lợi nhuận thuần', 'lãi sau thuế'],
        exclude_keywords=['trước thuế', 'before tax', 'thiểu số', 'minority'],
        item_ids=['net_profit', 'net_profit_after_tax', 'profit_after_tax'],
        period=period
    )

    data['eps_income_stmt'] = find_row_series(
        df_income,
        ['lãi cơ bản trên cổ phiếu', 'earnings per share', 'eps'],
        item_ids=['eps'], period=period
    )

    data['equity'] = find_row_series(
        df_balance,
        ['vốn chủ sở hữu', "owner's equity", 'owners equity', 'total equity',
         'equity', 'vcsh'],
        exclude_keywords=['vốn điều lệ', 'charter', 'cổ phần ưu đãi'],
        period=period
    )

    data['total_assets'] = find_row_series(
        df_balance,
        ['tổng cộng tài sản', 'total assets', 'tổng tài sản'],
        period=period
    )

    ratio_fields = [
        ('eps', ['eps', 'earning per share', 'earnings per share']),
        ('bvps', ['book value per share', 'bvps']),
        ('roe', ['roe']),
        ('roa', ['roa']),
        ('pe', ['p/e', 'pe ratio', ' pe ']),
        ('pb', ['p/b', 'pb ratio', ' pb ']),
        ('market_cap', ['market cap', 'vốn hóa']),
        ('outstanding_shares', ['outstanding shares', 'số cổ phiếu lưu hành']),
        ('ev_ebitda', ['ev/ebitda', 'ev to ebitda']),
        ('p_cf', ['price to cash flow', 'p/cf']),
        ('ps', ['p/s', 'price to sales', 'ps ratio']),
        ('net_margin', ['net margin', 'after tax profit margin', 'biên lợi nhuận sau thuế']),
        ('asset_turnover', ['asset turnover', 'vòng quay tài sản']),
        ('dps', ['dividend per share', 'cổ tức', 'dps']),
    ]

    if df_ratio is not None and not df_ratio.empty:
        for field_name, keywords in ratio_fields:
            data[field_name] = find_row_series(df_ratio, keywords, period=period)
    else:
        for field_name, _ in ratio_fields:
            data[field_name] = pd.Series(dtype=float)

    if data.get('eps', pd.Series(dtype=float)).empty and not data['eps_income_stmt'].empty:
        data['eps'] = data['eps_income_stmt']

    if (data.get('bvps', pd.Series(dtype=float)).empty
            and not data['equity'].empty
            and not data.get('outstanding_shares', pd.Series(dtype=float)).empty):
        eq = data['equity']
        sh = data['outstanding_shares']
        common_years = eq.index.intersection(sh.index)
        if len(common_years) > 0:
            data['bvps'] = (eq.loc[common_years] / sh.loc[common_years])

    return data


def build_5y_financial_table(df_income, df_balance, df_ratio=None, ticker=None):
    """
    FIX: ticker giờ được truyền vào đúng — đây là bug fix quan trọng nhất
    (trước đây build_5y_financial_table không nhận/truyền ticker xuống,
    khiến build_financial_table() không detect được ngành => luôn dùng
    keyword doanh thu chung chung, sai cho ngân hàng/bán lẻ/BĐS).
    """
    return build_financial_table(
        df_income, df_balance, df_ratio,
        ticker=ticker,
        period='year'
    )


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


def ddm_gordon(dps, required_return=0.11, g=0.04):
    if dps is None or dps <= 0 or required_return <= g:
        return None
    return (dps * (1 + g)) / (required_return - g)


def graham_number(eps, bvps):
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return (22.5 * eps * bvps) ** 0.5


def advanced_multiples_valuation(eps_latest, eps_5y_ago, pe_current,
                                  ebitda_latest, cfo_latest, revenue_latest, net_debt_latest,
                                  shares_outstanding,
                                  ev_ebitda_median_5y, pcf_median_5y, ps_median_5y):
    methods = {}
    shares_billion = shares_outstanding / 1e9 if shares_outstanding else 0
    if shares_billion <= 0:
        return methods

    if ebitda_latest and ebitda_latest > 0 and ev_ebitda_median_5y:
        fair_ev = ebitda_latest * ev_ebitda_median_5y
        fair_market_cap = fair_ev - net_debt_latest
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


def nine_methods_valuation(eps_latest, bvps_latest, pe_series: pd.Series,
                            pb_series: pd.Series, current_price):
    return {}
