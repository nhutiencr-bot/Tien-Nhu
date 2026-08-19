# ruff: noqa
# pylint: skip-file
"""
financial_normalizer.py
------------------------
Các sửa đổi so với bản trước (399 dòng):

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

  [FIX 7] _get_year_columns(): match r'\d{4}-Q4' — vnstock annual format.

  [FIX 8] find_row_series(): khi nhiều dòng match, ưu tiên dòng có data ở năm MỚI NHẤT.

  [FIX 9] _search_with_priority(): không return ngay khi s not empty — ưu tiên
          series có data ở latest year.

  [FIX 10] vnstock v3.2.8 — Tidy Data + Semantic ID.

  [FIX 11] _align_2025(): chuẩn hoá key năm 2025 — merge dữ liệu từ cột '2025-Q4'
           vào key int 2025, đảm bảo mọi Series đều có index thống nhất là int năm.
           Áp dụng sau mỗi find_row_series() call trong build_financial_table().

  [FIX 12] find_row_series(): khi map cột '2025-Q4' → year key 2025, KHÔNG ghi đè
           nếu key 2025 đã có giá trị hợp lệ từ cột '2025' (tránh double-write).

  [FIX 13] _search_with_priority(): so sánh latest year bằng int năm thực tế
           (max của index) thay vì s.index[-1] == s.index[-1] (luôn True).
           Trước đây: nếu index là [2021,2022,2023,2024] thì s.index[-1]=2024,
           s.dropna().index[-1]=2024 → điều kiện True → return sớm dù thiếu 2025.
           Sau fix: kiểm tra max(s.dropna().index) >= TARGET_YEAR (2025).

  [FIX 14] _find_revenue_for_bank(): loại trừ keyword gây nhiễu mạnh hơn —
           thêm 'tuong tu' vào exclude để tránh pick dòng
           "Thu nhập lãi và các khoản thu nhập tương tự" (gross) thay vì NII (net).
           Fallback gross chỉ dùng khi NII thực sự rỗng.
"""

import re
import unicodedata
import pandas as pd


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
TARGET_YEAR  = 2025   # năm hiện tại cần đảm bảo có data

# ---------------------------------------------------------------------------
# [FIX 10] Semantic ID (Taxonomy) — vnstock v3.2.8
# ---------------------------------------------------------------------------

BANK_REVENUE_IDS = [
    'NI_NET_INTEREST_INCOME',
    'NI_TOTAL_NET_OPERATING_INCOME',
    'NI_TOTAL_OPERATING_INCOME',
    'NI_NET_INCOME_FROM_SERVICES',
]

SECURITIES_REVENUE_IDS = [
    'NI_OPERATING_REVENUE',
    'NI_TOTAL_OPERATING_REVENUE',
    'NI_NET_REVENUE',
]

INSURANCE_REVENUE_IDS = [
    'NI_NET_PREMIUM_REVENUE',
    'NI_INSURANCE_OPERATING_REVENUE',
    'NI_TOTAL_OPERATING_REVENUE',
    'NI_NET_REVENUE',
]

NET_PROFIT_IDS = [
    'NI_NET_PROFIT_AFTER_TAX',
    'NI_PROFIT_AFTER_TAX',
    'net_profit', 'net_profit_after_tax', 'profit_after_tax',
]

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
# Text normalizer
# ---------------------------------------------------------------------------

def _norm_label(text: str) -> str:
    if not isinstance(text, str):
        return ''
    text = text.lower().replace('đ', 'd').replace('Đ', 'd')
    nfkd = unicodedata.normalize('NFKD', text)
    ascii_str = nfkd.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', ascii_str).strip()


# ---------------------------------------------------------------------------
# [FIX 11] Align 2025 key — chuẩn hoá '2025-Q4' → int 2025
# ---------------------------------------------------------------------------

def _align_2025(s: pd.Series) -> pd.Series:
    """
    Đảm bảo Series có key int 2025 nếu dữ liệu tồn tại dưới dạng '2025-Q4'
    hoặc string '2025'. Chuẩn hoá toàn bộ index về int.
    """
    if s is None or s.empty:
        return s

    new_index = {}
    for k, v in s.items():
        k_str = str(k).strip()
        # '2025-Q4' hoặc '2025' → int 2025
        if re.fullmatch(r'\d{4}-Q4', k_str):
            yr = int(k_str[:4])
        elif re.fullmatch(r'\d{4}', k_str):
            yr = int(k_str)
        else:
            continue
        # Không ghi đè nếu đã có giá trị hợp lệ (FIX 12)
        if yr not in new_index or pd.isna(new_index[yr]):
            new_index[yr] = v

    if not new_index:
        return s

    return pd.Series(new_index).sort_index()


# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------

def _get_year_columns(df: pd.DataFrame):
    """Return annual columns — matches both '2024' and '2025-Q4'."""
    meta_cols = {'item', 'item_en', 'item_id'}
    year_cols = [
        c for c in df.columns
        if c not in meta_cols and (
            re.fullmatch(r'\d{4}', str(c).strip())
            or re.fullmatch(r'\d{4}-Q4', str(c).strip())
        )
    ]
    return sorted(year_cols, key=lambda col: int(str(col).strip()[:4]))


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

    # [FIX 10] Taxonomy ID match trước
    if item_ids and 'item_id' in df.columns:
        id_lower = df['item_id'].astype(str).str.lower().str.strip()
        target_ids = [i.lower().strip() for i in item_ids]
        mask_id = id_lower.isin(target_ids)
        if mask_id.any():
            matched = df[mask_id]

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

    # [FIX 8] Ưu tiên dòng có data ở năm MỚI NHẤT, tiebreak: số cột non-NaN nhiều nhất
    if len(matched) > 1:
        latest_col = year_cols[-1]
        has_latest = matched[latest_col].notna()
        candidates = matched[has_latest] if has_latest.any() else matched
        non_na_counts = candidates[year_cols].notna().sum(axis=1)
        row = candidates.loc[non_na_counts.idxmax()]
    else:
        row = matched.iloc[0]

    # Build result dict
    result = {}
    for yc in year_cols:
        val = pd.to_numeric(pd.Series([row[yc]]), errors='coerce').iloc[0]
        if pd.notna(val):
            if period == 'quarter':
                result[str(yc).strip()] = float(val)
            else:
                # [FIX 12] Cột '2025-Q4' → key int 2025; không ghi đè key đã có
                yr = int(str(yc).strip()[:4])
                if yr not in result or pd.isna(result.get(yr)):
                    result[yr] = float(val)

    if period == 'quarter':
        ordered_keys = sorted(result.keys(), key=_quarter_sort_key)
        return pd.Series({k: result[k] for k in ordered_keys})

    # [FIX 11] Áp dụng align để đảm bảo index int thuần
    s = pd.Series(result).sort_index()
    return _align_2025(s)


# ---------------------------------------------------------------------------
# [FIX 13] _search_with_priority — so sánh theo TARGET_YEAR thực tế
# ---------------------------------------------------------------------------

def _search_with_priority(df_income, priority: list, period: str):
    """
    Duyệt priority list. Ưu tiên series có data tại TARGET_YEAR (2025).
    Không return sớm khi series thiếu năm hiện tại — thử priority tiếp theo.
    Chỉ dùng fallback khi không có lựa chọn nào có đủ 2025.
    """
    best_fallback = None

    for includes, excludes in priority:
        s = find_row_series(
            df_income,
            keywords=includes,
            exclude_keywords=excludes if excludes else None,
            period=period,
        )
        if s is None or s.empty:
            continue

        s_notna = s.dropna()
        if s_notna.empty:
            continue

        if period == 'year':
            # [FIX 13] Kiểm tra bằng int năm thực tế, không dùng positional index
            max_yr = int(s_notna.index.max())
            if max_yr >= TARGET_YEAR:
                return s          # ✅ Có data tại năm hiện tại → dùng luôn
            # Có data nhưng thiếu năm hiện tại → giữ fallback
            if best_fallback is None:
                best_fallback = s
        else:
            # Quarter: return ngay khi có data
            return s

    return best_fallback if best_fallback is not None else pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# Revenue finders theo ngành
# ---------------------------------------------------------------------------

def _find_revenue_for_bank(df_income, period='year'):
    """
    [FIX 14] Loại trừ 'tuong tu' mạnh hơn để tránh pick dòng gross interest income.
    Ưu tiên NII (thu nhập lãi thuần) — sau khi đã trừ chi phí lãi.
    Fallback gross chỉ dùng khi NII hoàn toàn rỗng.
    """
    # [FIX 10] Taxonomy ID trước
    for tax_id in BANK_REVENUE_IDS:
        s = find_row_series(
            df_income,
            keywords=['net interest income', 'thu nhap lai thuan'],
            item_ids=[tax_id],
            period=period,
        )
        if s is not None and not s.empty:
            s_notna = s.dropna()
            if not s_notna.empty:
                max_yr = int(s_notna.index.max()) if period == 'year' else None
                if period != 'year' or max_yr >= TARGET_YEAR:
                    return s

    # Keyword fallback — priority có loại trừ mạnh để tránh gross income
    priority = [
        (
            # Priority 1: NII — loại trừ "tương tự" để không pick gross
            ['thu nhap lai thuan', 'net interest income', 'lai thuan', 'nii'],
            ['chi phi lai', 'interest expense', 'tuong tu',          # [FIX 14] thêm 'tuong tu'
             'cac khoan thu nhap', 'hoat dong khac', 'dich vu',
             'thu nhap lai va'],                                       # loại trừ gross label
        ),
        (
            ['tong thu nhap hoat dong thuan', 'thu nhap hoat dong thuan',
             'net operating income', 'total operating income'],
            ['chi phi', 'expense', 'truoc du phong'],
        ),
        (
            ['tong thu nhap hoat dong', 'thu nhap hoat dong'],
            ['chi phi', 'expense'],
        ),
        (
            ['thu nhap thuan', 'net income from', 'total net income'],
            ['loi nhuan', 'profit', 'sau thue'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


def _find_revenue_for_securities(df_income, period='year'):
    # [FIX 10] Taxonomy ID trước
    for tax_id in SECURITIES_REVENUE_IDS:
        s = find_row_series(
            df_income,
            keywords=['doanh thu hoat dong', 'operating revenue'],
            item_ids=[tax_id],
            period=period,
        )
        if s is not None and not s.empty:
            s_notna = s.dropna()
            if not s_notna.empty:
                max_yr = int(s_notna.index.max()) if period == 'year' else None
                if period != 'year' or max_yr >= TARGET_YEAR:
                    return s

    priority = [
        (
            ['doanh thu hoat dong', 'operating revenue', 'tong doanh thu hoat dong'],
            ['chi phi', 'expense', 'phi hoa hong'],
        ),
        (
            ['doanh thu thuan ve hoat dong kinh doanh', 'doanh thu thuan hoat dong'],
            ['chi phi'],
        ),
        (
            ['doanh thu thuan', 'net revenue'],
            ['chi phi', 'gia von', 'cost'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


def _find_revenue_for_insurance(df_income, period='year'):
    # [FIX 10] Taxonomy ID trước
    for tax_id in INSURANCE_REVENUE_IDS:
        s = find_row_series(
            df_income,
            keywords=['phi bao hiem thuan', 'net premium'],
            item_ids=[tax_id],
            period=period,
        )
        if s is not None and not s.empty:
            s_notna = s.dropna()
            if not s_notna.empty:
                max_yr = int(s_notna.index.max()) if period == 'year' else None
                if period != 'year' or max_yr >= TARGET_YEAR:
                    return s

    priority = [
        (
            ['phi bao hiem thuan', 'doanh thu phi bao hiem', 'net premium',
             'doanh thu hoat dong bao hiem'],
            ['chi phi', 'expense'],
        ),
        (
            ['tong doanh thu hoat dong', 'tong thu nhap hoat dong'],
            ['chi phi'],
        ),
        (
            ['doanh thu thuan', 'net revenue'],
            ['chi phi', 'gia von'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


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
# CFO helper
# ---------------------------------------------------------------------------

def _find_cfo_with_quarterly_fallback(df_cashflow_y, df_cashflow_q=None):
    cfo_annual = find_row_series(
        df_cashflow_y,
        keywords=CFO_KEYWORDS,
        period='year',
    )

    if df_cashflow_q is None or df_cashflow_q.empty:
        return cfo_annual

    current_year = TARGET_YEAR
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

    # [FIX 11] Align 2025 cho revenue
    data['revenue'] = _align_2025(data['revenue'])

    # --- Lợi nhuận sau thuế ---
    data['net_profit'] = _align_2025(find_row_series(
        df_income,
        ['loi nhuan sau thue', 'net profit', 'profit after tax', 'net income',
         'loi nhuan thuan', 'lai sau thue'],
        exclude_keywords=['truoc thue', 'before tax', 'thieu so', 'minority'],
        item_ids=NET_PROFIT_IDS,
        period=period,
    ))

    # --- EPS từ income statement ---
    data['eps_income_stmt'] = _align_2025(find_row_series(
        df_income,
        ['lai co ban tren co phieu', 'earnings per share', 'eps'],
        item_ids=['eps'], period=period,
    ))

    # --- Balance sheet ---
    data['equity'] = _align_2025(find_row_series(
        df_balance,
        ['von chu so huu', "owner's equity", 'owners equity', 'total equity',
         'equity', 'vcsh'],
        exclude_keywords=['von dieu le', 'charter', 'co phan uu dai'],
        period=period,
    ))

    data['total_assets'] = _align_2025(find_row_series(
        df_balance,
        ['tong cong tai san', 'total assets', 'tong tai san'],
        period=period,
    ))

    # --- CFO với fallback quý ---
    if period == 'year' and df_cashflow_y is not None:
        data['cfo'] = _align_2025(_find_cfo_with_quarterly_fallback(df_cashflow_y, df_cashflow_q))
    elif df_cashflow_y is not None:
        data['cfo'] = find_row_series(df_cashflow_y, keywords=CFO_KEYWORDS, period=period)
    else:
        data['cfo'] = pd.Series(dtype=float)

    # --- Ratio table ---
    ratio_fields = [
        ('eps',               ['eps', 'earning per share', 'earnings per share']),
        ('bvps',              ['book value per share', 'bvps']),
        ('roe',               ['roe']),
        ('roa',               ['roa']),
        ('pe',                ['p/e', 'pe ratio', ' pe ']),
        ('pb',                ['p/b', 'pb ratio', ' pb ']),
        ('market_cap',        ['market cap', 'von hoa']),
        ('outstanding_shares',['outstanding shares', 'so co phieu luu hanh']),
        ('ev_ebitda',         ['ev/ebitda', 'ev to ebitda']),
        ('p_cf',              ['price to cash flow', 'p/cf']),
        ('ps',                ['p/s', 'price to sales', 'ps ratio']),
        ('net_margin',        ['net margin', 'after tax profit margin',
                               'bien loi nhuan sau thue']),
        ('asset_turnover',    ['asset turnover', 'vong quay tai san']),
        ('dps',               ['dividend per share', 'co tuc', 'dps']),
    ]

    if df_ratio is not None and not df_ratio.empty:
        for field_name, keywords in ratio_fields:
            data[field_name] = _align_2025(find_row_series(df_ratio, keywords, period=period))
    else:
        for field_name, _ in ratio_fields:
            data[field_name] = pd.Series(dtype=float)

    # EPS fallback
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

def _make_df(items, data_dict):
    """Helper tạo df đúng format vnstock: có cột 'item' + cột năm."""
    d = {'item': items}
    d.update(data_dict)
    return pd.DataFrame(d)


if __name__ == '__main__':
    print('=== Self-test financial_normalizer.py ===\n')

    # --- Test 1: Bank revenue pick đúng NII, không pick gross ---
    df_bank = _make_df(
        ['Thu nhập lãi và các khoản thu nhập tương tự',   # gross — KHÔNG pick
         'Chi phí lãi và các chi phí tương tự',
         'I. Thu nhập lãi thuần',                          # NII — PHẢI pick
         'II. Thu nhập từ hoạt động dịch vụ thuần',
         'Doanh thu thuần'],                               # sai — KHÔNG pick
        {2021:  [70749, 28476, 42273, 5000,  2500],
         2022:  [88113, 34867, 53246, 6200,  3000],
         2023:  [108122,54508, 53615, 7100,  3500],
         2024:  [93655, 38249, 55406, 7500,  3800],
         '2025-Q4': [105216,36162, 58771, 8200, 69054]},  # 2025 dạng Q4
    )

    rev = build_financial_table(df_bank, pd.DataFrame(), ticker='VCB')['revenue']
    print(f'VCB revenue: {dict(rev)}')
    assert rev.get(2025) == 58771, f'FAIL 2025: {rev.get(2025)} (expected 58771)'
    assert rev.get(2021) == 42273, f'FAIL 2021: {rev.get(2021)}'
    assert rev.get(2024) == 55406, f'FAIL 2024: {rev.get(2024)}'
    print('✅ BANK (VCB): NII đúng cả 5 năm, không pick gross hay Doanh thu thuần')

    # --- Test 2: TPB giữ nguyên ---
    df_tpb = _make_df(
        ['1. Thu nhập lãi và các khoản thu nhập tương tự',
         '2. Chi phí lãi và các chi phí tương tự',
         'I. Thu nhập lãi thuần',
         'II. Thu nhập từ hoạt động dịch vụ thuần',
         'Doanh thu hoạt động'],
        {2021:  [17427, 7481,  9946,  5000, 2500],
         2022:  [21811, 10424, 11387, 6200, 3000],
         2023:  [28562, 16135, 12428, 7100, 3500],
         2024:  [25949, 13042, 12907, 7500, 3800],
         '2025-Q4': [30751, 17379, 13371, 8200, 4000]},
    )
    rev_tpb = build_financial_table(df_tpb, pd.DataFrame(), ticker='TPB')['revenue']
    assert rev_tpb.get(2025) == 13371, f'FAIL TPB 2025: {rev_tpb.get(2025)}'
    print(f'✅ BANK (TPB): 2025={rev_tpb.get(2025):,.0f} (NII đúng, không pick gross 30,751)')

    # --- Test 3: _align_2025 ---
    s_test = pd.Series({'2025-Q4': 58771.0, 2024: 55406.0, 2023: 53615.0})
    aligned = _align_2025(s_test)
    assert 2025 in aligned.index, 'FAIL: 2025 không có sau align'
    assert aligned[2025] == 58771.0
    print('✅ _align_2025: 2025-Q4 → int 2025 OK')

    # --- Test 4: _search_with_priority không return sớm khi thiếu TARGET_YEAR ---
    df_missing_2025 = _make_df(
        ['Thu nhập lãi thuần'],
        {2021: [42273], 2022: [53246], 2023: [53615], 2024: [55406]},
    )
    df_has_2025 = _make_df(
        ['Doanh thu thuần'],
        {2021: [100], 2022: [110], 2023: [120], 2024: [130], 2025: [999]},
    )
    df_combined = pd.concat([df_missing_2025, df_has_2025], ignore_index=True)
    priority_test = [
        (['thu nhap lai thuan'], ['tuong tu', 'chi phi']),
        (['doanh thu thuan'], []),
    ]
    result = _search_with_priority(df_combined, priority_test, 'year')
    # Nên pick Doanh thu thuần (có 2025=999) vì NII thiếu 2025
    assert result.get(2025) == 999, f'FAIL priority fallback: {result.get(2025)}'
    print('✅ _search_with_priority: skip dòng thiếu 2025, fallback đúng sang dòng có 2025')

    # --- Test 5: _norm_label ---
    assert _norm_label('hoạt động') == 'hoat dong'
    assert _norm_label('Thu nhập lãi thuần') == 'thu nhap lai thuan'
    print('✅ _norm_label OK')

    # --- Test 6: CFO fallback quarterly ---
    df_cf_y = _make_df(
        ['Lưu chuyển tiền thuần từ hoạt động kinh doanh'],
        {2021: [12327], 2022: [16414], 2023: [19422], 2024: [16710]},
    )
    df_cf_q = _make_df(
        ['Lưu chuyển tiền thuần từ hoạt động kinh doanh'],
        {'2025-Q1': [4200], '2025-Q2': [3800], '2025-Q3': [4100], '2025-Q4': [3900]},
    )
    cfo = _find_cfo_with_quarterly_fallback(df_cf_y, df_cf_q)
    assert 2025 in cfo.index, 'FAIL: CFO 2025 vẫn thiếu'
    assert cfo[2025] == 16000, f'FAIL CFO 2025: {cfo[2025]}'
    print(f'✅ CFO fallback quarterly: 2025={cfo[2025]:,.0f} tỷ')

    print('\n🎉 Tất cả test pass!')
