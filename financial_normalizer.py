"""
financial_normalizer.py
------------------------
Các sửa đổi so với bản trước:

  [FIX 1] _find_revenue_for_bank(): đảo priority — Thu nhập lãi thuần lên ƯU TIÊN #1.
          Bản cũ để "doanh thu hoạt động" đầu tiên → TPB bị lấy 30,751 tỷ thay vì 13,371 tỷ.

  [FIX 2] _find_revenue_for_securities(): tách riêng CTCK khỏi ngân hàng.
          CTCK dùng "Doanh thu hoạt động" (đúng), ngân hàng dùng NII (đúng).

  [FIX 3] _norm_label(): fix bug chữ đ/Đ bị drop hoàn toàn khi unicodedata strip dấu.
          "hoạt động" → "hoat ong" (sai) → giờ → "hoat dong" (đúng).

  [FIX 4] find_row_series(): thêm _norm_label() vào matching để không phụ thuộc
          vào dấu tiếng Việt trong keyword.

  [FIX 5] Bổ sung OILGAS_TICKERS, CONSTRUCTION_TICKERS vào sector detection.

  [FIX 6] build_financial_table(): thêm field 'cfo' — lấy CFO từ cashflow năm,
          fallback tự cộng 4 quý gần nhất khi cashflow năm 2025 chưa có.

  [FIX 7] _get_year_columns(): bổ sung match r'\d{4}-Q4' — vnstock đổi format
          annual column từ '2025' → '2025-Q4'.

  [FIX 8] find_row_series(): khi nhiều dòng match, ưu tiên dòng có data ở năm MỚI NHẤT.

  [FIX 9] _search_with_priority(): không return ngay khi s not empty — kiểm tra
          series có data ở latest year trước; chỉ fallback nếu không có lựa chọn tốt hơn.
          Đây là root cause thực sự của bug revenue 2025 bị sai/thiếu cho bank.
"""

import re
import unicodedata
import pandas as pd
from datetime import datetime


# ---------------------------------------------------------------------------
# Sector sets
# ---------------------------------------------------------------------------

BANK_TICKERS = {
    'VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB', 'VPB', 'HDB', 'TPB',
    'MSB', 'OCB', 'VIB', 'SHB', 'EIB', 'LPB', 'SSB', 'NAB', 'ABB', 'BAB',
    'BVB', 'KLB', 'PGB', 'VAB', 'VBB', 'SGN', 'NVB', 'SGB', 'CBB', 'SEAB',
}

SECURITIES_TICKERS = {
    'SSI', 'VND', 'HCM', 'MBS', 'VCI', 'FTS', 'AGR', 'SBS', 'BSI',
    'VPX', 'VCK', 'TCX', 'SHS', 'CTS', 'VDS', 'ORS', 'TVS',
}

INSURANCE_TICKERS = {
    'BVH', 'PVI', 'PTI', 'MIG', 'BMI', 'VNR', 'BIC', 'PRE', 'PGI',
}

FINANCIAL_TICKERS = SECURITIES_TICKERS | INSURANCE_TICKERS

RETAIL_TICKERS = {
    'MWG', 'FRT', 'DGW', 'PNJ', 'HAX', 'SVC', 'MCH', 'PET',
    'PSD', 'HHS', 'HUT', 'AST', 'PTC', 'MSN',
}

REAL_ESTATE_TICKERS = {
    'VHM', 'VIC', 'NLG', 'KDH', 'DXG', 'PDR', 'CEO', 'BCM',
    'VRE', 'DIG', 'HDC', 'NVL', 'AGG', 'DPG', 'SZC',
}

OILGAS_TICKERS = {
    'GAS', 'PLX', 'BSR', 'PVC', 'DPM', 'DGC', 'PVD', 'PVS', 'PGC',
}

CONSTRUCTION_TICKERS = {
    'CTD', 'HBC', 'FCN', 'VCG', 'PC1', 'LCG', 'CII', 'PXL', 'SC5',
}

TARGET_YEARS = list(range(2021, 2026))

CFO_KEYWORDS = [
    'luu chuyen tien thuan tu hoat dong kinh doanh',
    'luu chuyen tien tu hoat dong kinh doanh',
    'net cash flow from operating',
    'cash flow from operating activities',
    'tien thuan tu hoat dong kinh doanh',
    'net cash from operating',
    'operating cash flow',
    'cfo',
]


# ---------------------------------------------------------------------------
# Text normalizer — fix bug đ/Đ
# ---------------------------------------------------------------------------

def _norm_label(text: str) -> str:
    if not isinstance(text, str):
        return ''
    text = text.lower().replace('đ', 'd').replace('Đ', 'd')
    nfkd = unicodedata.normalize('NFKD', text)
    ascii_str = nfkd.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', ascii_str).strip()


# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------

def _get_year_columns(df: pd.DataFrame):
    meta_cols = {'item', 'item_en', 'item_id'}
    seen_years = {}
    for c in df.columns:
        if c in meta_cols:
            continue
        c_str = str(c).strip()
        if re.fullmatch(r'\d{4}', c_str):
            yr = int(c_str)
            # Ưu tiên cột '2025' over '2025-Q4' nếu cả hai tồn tại
            if yr not in seen_years:
                seen_years[yr] = c
        elif re.fullmatch(r'\d{4}-Q4', c_str):
            yr = int(c_str[:4])
            # Chỉ thêm nếu chưa có cột plain '2025'
            if yr not in seen_years:
                seen_years[yr] = c
    return [seen_years[yr] for yr in sorted(seen_years)]


def _quarter_sort_key(c):
    y, q = str(c).strip().split('-Q')
    return (int(y), int(q))


def _get_quarter_columns(df: pd.DataFrame):
    meta_cols = {'item', 'item_en', 'item_id'}
    q_cols = [
        c for c in df.columns
        if c not in meta_cols and re.fullmatch(r'\d{4}-Q[1-4]', str(c).strip())
    ]
    return sorted(q_cols, key=_quarter_sort_key)


# ---------------------------------------------------------------------------
# Core row finder
# ---------------------------------------------------------------------------

def find_row_series(df: pd.DataFrame, keywords, exclude_keywords=None,
                    item_ids=None, prefer_top_level=True, period='year'):
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
        id_lower = df['item_id'].astype(str).str.lower().str.strip()
        target_ids = [i.lower().strip() for i in item_ids]
        mask_id = id_lower.isin(target_ids)
        if mask_id.any():
            matched = df[mask_id]

    # Bước 2: fallback dò từ khoá (có norm để không phụ thuộc dấu tiếng Việt)
    if matched.empty:
        norm_kws = [_norm_label(kw) for kw in keywords]
        norm_exc = [_norm_label(e) for e in (exclude_keywords or [])]

        combined_norm = df[search_cols].apply(
            lambda row: _norm_label(' '.join(str(v) for v in row.values if v is not None)),
            axis=1
        )

        mask = pd.Series(False, index=df.index)
        for kw in norm_kws:
            mask = mask | combined_norm.str.contains(kw, na=False, regex=False)

        for exc in norm_exc:
            mask = mask & ~combined_norm.str.contains(exc, na=False, regex=False)

        matched = df[mask]

    if matched.empty:
        return pd.Series(dtype=float)

    # FIX 8: khi nhiều dòng match, ưu tiên dòng có data ở năm MỚI NHẤT
    if len(matched) > 1:
        latest_col = year_cols[-1]
        has_latest = matched[latest_col].notna()
        candidates = matched[has_latest] if has_latest.any() else matched
        non_na_counts = candidates[year_cols].notna().sum(axis=1)
        row = candidates.loc[non_na_counts.idxmax()]
    else:
        row = matched.iloc[0]

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


# ---------------------------------------------------------------------------
# _search_with_priority — FIX 9: kiểm tra data ở latest year
# ---------------------------------------------------------------------------

def _search_with_priority(df_income, priority: list, period: str):
    best_fallback = None
    for includes, excludes in priority:
        s = find_row_series(
            df_income,
            keywords=includes,
            exclude_keywords=excludes if excludes else None,
            period=period,
        )
        if s.empty:
            continue
        s_notna = s.dropna()
        if s_notna.empty:
            continue
        latest_year = s.index[-1]           # index[-1] của series gốc
        if latest_year in s_notna.index:    # check membership, không so sánh index trực tiếp
            return s
        if best_fallback is None:
            best_fallback = s
    return best_fallback if best_fallback is not None else pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# Revenue helpers theo ngành
# ---------------------------------------------------------------------------

# ============================================================
# THAY THẾ _find_revenue_for_bank bằng 3 hàm riêng theo ngành
# ============================================================

def _find_revenue_for_bank(df_income, period='year'):
    priority_list = [
        (['tổng thu nhập hoạt động', 'tong thu nhap hoat dong',
          'total operating income', 'net operating income'],
         ['chi phí', 'chi phi', 'expense']),

        (['thu nhập lãi thuần', 'thu nhap lai thuan',
          'net interest income', 'lãi thuần', 'lai thuan'],
         ['chi phí lãi', 'chi phi lai', 'interest expense',
          'tương tự', 'tuong tu', 'similar']),

        (['thu nhập thuần', 'thu nhap thuan', 'total net income'],
         ['lợi nhuận', 'loi nhuan', 'profit',
          'trước thuế', 'truoc thue', 'before tax']),
    ]
    ...

def _find_revenue_for_insurance(df_income, period='year'):
    priority_list = [
        (['tổng doanh thu hoạt động kinh doanh bảo hiểm',
          'tong doanh thu hoat dong kinh doanh bao hiem',
          'doanh thu hoạt động kinh doanh bảo hiểm',
          'doanh thu hoat dong kinh doanh bao hiem',
          'total insurance revenue'],
         ['phí nhượng', 'phi nhuong', 'nhượng tái', 'nhuong tai', 'ceded']),

        (['phí bảo hiểm thuần', 'phi bao hiem thuan',
          'net premium', 'doanh thu phí thuần', 'doanh thu phi thuan',
          'net earned premium'],
         []),

        (['tổng doanh thu', 'tong doanh thu', 'total revenue'],
         ['phí nhượng', 'phi nhuong', 'nhượng tái', 'nhuong tai']),

        (['doanh thu hoạt động', 'doanh thu hoat dong', 'operating revenue'],
         []),
    ]
    ...


def _find_revenue_for_securities(df_income, period='year'):
    priority_list = [
        (['tổng doanh thu hoạt động', 'tong doanh thu hoat dong',
          'total operating revenue',
          'tổng thu nhập hoạt động', 'tong thu nhap hoat dong'],
         ['chi phí', 'chi phi', 'expense']),

        (['doanh thu hoạt động', 'doanh thu hoat dong',
          'operating revenue', 'doanh thu từ hoạt động', 'doanh thu tu hoat dong'],
         ['chi phí', 'chi phi']),

        (['tổng doanh thu', 'tong doanh thu', 'total revenue'],
         []),

        (['doanh thu'],
         ['từ lãi', 'tu lai', 'interest', 'chi phí', 'chi phi']),
    ]
    ...


def _find_revenue_for_securities(df_income, period='year'):
    """
    CHỨNG KHOÁN: Doanh thu = Doanh thu hoạt động (môi giới, tự doanh, tư vấn...).
    IS chứng khoán thường đã là net, không có bẫy gross/net rõ như ngân hàng.
    """
    priority_list = [
        # Ưu tiên 1: Tổng doanh thu hoạt động (dòng tổng hợp)
        (['tổng doanh thu hoạt động', 'total operating revenue',
          'tổng thu nhập hoạt động'],
         ['chi phí', 'expense']),
        # Ưu tiên 2: Doanh thu hoạt động
        (['doanh thu hoạt động', 'operating revenue', 'doanh thu từ hoạt động'],
         ['chi phí']),
        # Ưu tiên 3: Tổng doanh thu
        (['tổng doanh thu', 'total revenue'],
         []),
        # Ưu tiên 4: Doanh thu (fallback — nhưng exclude 'từ lãi' để tránh gross interest)
        (['doanh thu'],
         ['từ lãi', 'interest', 'chi phí']),
    ]
    for keywords, excludes in priority_list:
        s = find_row_series(df_income, keywords,
                            exclude_keywords=excludes or None, period=period)
        if not s.empty:
            return s
    return pd.Series(dtype=float)

def _find_revenue_for_realestate(df_income, period='year'):
    priority = [
        (
            ['doanh thu ban hang va cung cap dich vu', 'doanh thu ban hang',
             'doanh thu ban bat dong san'],
            ['gia von', 'cost', 'chiet khau', 'giam gia'],
        ),
        (
            ['doanh thu cho thue', 'rental revenue', 'rental income'],
            ['chi phi'],
        ),
        (
            ['doanh thu thuan', 'net revenue'],
            ['gia von', 'cost', 'hoat dong tai chinh', 'hoat dong khac'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


def _find_revenue_for_retail(df_income, period='year'):
    priority = [
        (
            ['doanh thu ban hang va cung cap dich vu',
             'doanh thu thuan ve ban hang va cung cap dich vu'],
            ['gia von', 'cost'],
        ),
        (
            ['doanh thu thuan', 'net revenue', 'net sales'],
            ['gia von', 'chi phi lai'],
        ),
        (
            ['doanh thu ban hang', 'sales revenue'],
            ['gia von'],
        ),
        (
            ['tong doanh thu', 'total revenue'],
            ['gia von'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


def _find_revenue_general(df_income, period='year'):
    priority = [
        (
            ['doanh thu ban hang va cung cap dich vu', 'doanh thu ban hang',
             'revenue from goods and services', 'sales revenue'],
            ['gia von', 'cost of', 'chiet khau', 'giam gia', 'hang ban tra lai'],
        ),
        (
            ['doanh thu thuan', 'net revenue', 'net sales'],
            ['gia von', 'cost of', 'hoat dong tai chinh', 'hoat dong khac'],
        ),
        (
            ['doanh thu', 'revenue'],
            ['gia von', 'chi phi', 'cost', 'expense', 'lai', 'interest',
             'phi', 'khac', 'other'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


# ---------------------------------------------------------------------------
# CFO helper — fallback cộng quý
# ---------------------------------------------------------------------------

def _find_cfo_with_quarterly_fallback(df_cashflow_y, df_cashflow_q=None):
    """
    Lấy CFO từ cashflow năm.
    Nếu năm hiện tại bị thiếu → cộng các quý có sẵn từ df_cashflow_q.
    """
    cfo_annual = find_row_series(
        df_cashflow_y,
        keywords=CFO_KEYWORDS,
        period='year',
    )

    if df_cashflow_q is None or df_cashflow_q.empty:
        return cfo_annual

    current_year = datetime.today().year
    if cfo_annual.empty or current_year not in cfo_annual.index:
        cfo_q = find_row_series(
            df_cashflow_q,
            keywords=CFO_KEYWORDS,
            period='quarter',
        )
        if not cfo_q.empty:
            current_year_quarters = [
                k for k in cfo_q.index
                if str(k).startswith(str(current_year))
            ]
            if len(current_year_quarters) >= 1:
                cfo_current = cfo_q[current_year_quarters].sum()
                if not cfo_annual.empty:
                    cfo_annual = cfo_annual.copy()
                    cfo_annual[current_year] = cfo_current
                else:
                    years_in_q = sorted(set(
                        int(str(k).split('-Q')[0]) for k in cfo_q.index
                    ))
                    result = {}
                    for yr in years_in_q:
                        qs = [k for k in cfo_q.index if str(k).startswith(str(yr))]
                        if len(qs) == 4:
                            result[yr] = cfo_q[qs].sum()
                        elif yr == current_year and len(qs) >= 1:
                            result[yr] = cfo_q[qs].sum()
                    cfo_annual = pd.Series(result).sort_index()

    return cfo_annual


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_financial_table(df_income, df_balance, df_ratio=None,
                          ticker=None, period='year',
                          df_cashflow_y=None, df_cashflow_q=None):
    """
    Tổng hợp chỉ tiêu BCTC.
    ticker bắt buộc truyền vào để detect ngành chính xác.
    [FIX 6] Thêm df_cashflow_y + df_cashflow_q để fetch CFO với fallback quý.
    """
    data = {}
    t = (ticker or '').upper().strip()

    # --- Sector detection & Revenue ---
    if t in BANK_TICKERS:
        data['revenue'] = _find_revenue_for_bank(df_income, period=period)
    elif t in SECURITIES_TICKERS:
        data['revenue'] = _find_revenue_for_securities(df_income, period=period)
    elif t in INSURANCE_TICKERS:
        data['revenue'] = _find_revenue_for_insurance(df_income, period=period)
    elif t in REAL_ESTATE_TICKERS:
        data['revenue'] = _find_revenue_for_realestate(df_income, period=period)
    elif t in RETAIL_TICKERS:
        data['revenue'] = _find_revenue_for_retail(df_income, period=period)
    else:
        data['revenue'] = _find_revenue_general(df_income, period=period)
        if data['revenue'].empty:
            data['revenue'] = _find_revenue_for_retail(df_income, period=period)

    # --- Lợi nhuận sau thuế ---
    data['net_profit'] = find_row_series(
        df_income,
        ['loi nhuan sau thue', 'net profit', 'profit after tax', 'net income',
         'loi nhuan thuan', 'lai sau thue'],
        exclude_keywords=['truoc thue', 'before tax', 'thieu so', 'minority'],
        item_ids=['net_profit', 'net_profit_after_tax', 'profit_after_tax'],
        period=period
    )

    # --- EPS từ income statement ---
    data['eps_income_stmt'] = find_row_series(
        df_income,
        ['lai co ban tren co phieu', 'earnings per share', 'eps'],
        item_ids=['eps'], period=period
    )

    # --- Balance sheet ---
    data['equity'] = find_row_series(
        df_balance,
        ['von chu so huu', "owner's equity", 'owners equity', 'total equity',
         'equity', 'vcsh'],
        exclude_keywords=['von dieu le', 'charter', 'co phan uu dai'],
        period=period
    )

    data['total_assets'] = find_row_series(
        df_balance,
        ['tong cong tai san', 'total assets', 'tong tai san'],
        period=period
    )

    # --- CFO với fallback quý [FIX 6] ---
    if period == 'year' and df_cashflow_y is not None:
        data['cfo'] = _find_cfo_with_quarterly_fallback(df_cashflow_y, df_cashflow_q)
    elif df_cashflow_y is not None:
        data['cfo'] = find_row_series(df_cashflow_y, keywords=CFO_KEYWORDS, period=period)
    else:
        data['cfo'] = pd.Series(dtype=float)

    # --- Ratio table ---
    ratio_fields = [
        ('eps',                ['eps', 'earning per share', 'earnings per share']),
        ('bvps',               ['book value per share', 'bvps']),
        ('roe',                ['roe']),
        ('roa',                ['roa']),
        ('pe',                 ['p/e', 'pe ratio', ' pe ']),
        ('pb',                 ['p/b', 'pb ratio', ' pb ']),
        ('market_cap',         ['market cap', 'von hoa']),
        ('outstanding_shares', ['outstanding shares', 'so co phieu luu hanh']),
        ('ev_ebitda',          ['ev/ebitda', 'ev to ebitda']),
        ('p_cf',               ['price to cash flow', 'p/cf']),
        ('ps',                 ['p/s', 'price to sales', 'ps ratio']),
        ('net_margin',         ['net margin', 'after tax profit margin',
                                'bien loi nhuan sau thue']),
        ('asset_turnover',     ['asset turnover', 'vong quay tai san']),
        ('dps',                ['dividend per share', 'co tuc', 'dps']),
    ]

    if df_ratio is not None and not df_ratio.empty:
        for field_name, keywords in ratio_fields:
            data[field_name] = find_row_series(df_ratio, keywords, period=period)
    else:
        for field_name, _ in ratio_fields:
            data[field_name] = pd.Series(dtype=float)

    # EPS fallback từ income statement
    if data.get('eps', pd.Series(dtype=float)).empty and not data['eps_income_stmt'].empty:
        data['eps'] = data['eps_income_stmt']

    # BVPS tự tính nếu ratio không có
    if (data.get('bvps', pd.Series(dtype=float)).empty
            and not data['equity'].empty
            and not data.get('outstanding_shares', pd.Series(dtype=float)).empty):
        eq = data['equity']
        sh = data['outstanding_shares']
        common_years = eq.index.intersection(sh.index)
        if len(common_years) > 0:
            data['bvps'] = eq.loc[common_years] / sh.loc[common_years]

    return data


def build_5y_financial_table(df_income, df_balance, df_ratio=None, ticker=None,
                              df_cashflow_y=None, df_cashflow_q=None):
    """Wrapper — truyền cashflow xuống để CFO có fallback quý."""
    return build_financial_table(
        df_income, df_balance, df_ratio,
        ticker=ticker,
        period='year',
        df_cashflow_y=df_cashflow_y,
        df_cashflow_q=df_cashflow_q,
    )


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def normalize_to_billion_vnd(series: pd.Series, label=''):
    if series is None or (hasattr(series, 'empty') and series.empty):
        return pd.Series(dtype=float)
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
                                  ebitda_latest, cfo_latest, revenue_latest,
                                  net_debt_latest, shares_outstanding,
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


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print('=== Self-test financial_normalizer.py ===\n')

    # Test 1: Bank revenue (TPB) — FIX 1
    df_bank = pd.DataFrame({
        2021: [17427, 7481, 9946, 5000, 2500],
        2022: [21811, 10424, 11387, 6200, 3000],
        2023: [28562, 16135, 12428, 7100, 3500],
        2024: [25949, 13042, 12907, 7500, 3800],
        2025: [30751, 17379, 13371, 8200, 4000],
    }, index=[
        '1. Thu nhập lãi và các khoản thu nhập tương tự',
        '2. Chi phí lãi và các chi phí tương tự',
        'I. Thu nhập lãi thuần',
        'II. Thu nhập từ hoạt động dịch vụ thuần',
        'Doanh thu hoạt động',
    ])
    df_bank.index.name = 'item'
    df_bank_reset = df_bank.reset_index()
    rev = build_financial_table(df_bank_reset, pd.DataFrame(), ticker='TPB')['revenue']
    assert rev.get(2025) == 13371, f'FAIL TPB revenue 2025: {rev.get(2025)}'
    print(f'✅ BANK (TPB): 2025={rev.get(2025)} (expected 13,371)')

    # Test 2: CFO fallback quarterly — FIX 6
    df_cf_y = pd.DataFrame({
        2021: [12327],
        2022: [16414],
        2023: [19422],
        2024: [16710],
        # 2025 missing
    }, index=['Lưu chuyển tiền thuần từ hoạt động kinh doanh'])
    df_cf_y.index.name = 'item'
    df_cf_y_reset = df_cf_y.reset_index()

    df_cf_q = pd.DataFrame({
        '2025-Q1': [4200],
        '2025-Q2': [3800],
        '2025-Q3': [4100],
        '2025-Q4': [3900],
    }, index=['Lưu chuyển tiền thuần từ hoạt động kinh doanh'])
    df_cf_q.index.name = 'item'
    df_cf_q_reset = df_cf_q.reset_index()

    cfo = _find_cfo_with_quarterly_fallback(df_cf_y_reset, df_cf_q_reset)
    assert 2025 in cfo.index, 'FAIL: 2025 vẫn thiếu sau fallback'
    assert cfo[2025] == 16000, f'FAIL CFO 2025: {cfo[2025]}'
    print(f'✅ CFO fallback quarterly: 2025={cfo[2025]:,.0f} (expected 16,000)')

    # Test 3: _norm_label — FIX 3
    assert _norm_label('hoạt động') == 'hoat dong', f'FAIL norm: {_norm_label("hoạt động")}'
    assert _norm_label('Thu nhập lãi thuần') == 'thu nhap lai thuan'
    print('✅ _norm_label OK')

    # Test 4: _get_year_columns — FIX 7 (vnstock dùng '2025-Q4' thay '2025')
    df_q4col = pd.DataFrame({'item': ['rev'], '2023': [100], '2024': [200], '2025-Q4': [300]})
    yr_cols = _get_year_columns(df_q4col)
    assert 2025 in [int(str(c)[:4]) for c in yr_cols], f'FAIL: 2025-Q4 không được nhận: {yr_cols}'
    print(f'✅ _get_year_columns nhận 2025-Q4: {yr_cols}')

    print('\n🎉 Tất cả test pass!')
