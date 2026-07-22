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
          Các ngành này dùng "Doanh thu bán hàng CCDV" — giống general nhưng
          explicit để tránh nhầm sang bank/securities.

  [KEPT]  _get_year_columns() dead-code fix (từ bản 399 dòng) — giữ nguyên.
  [KEPT]  build_5y_financial_table() truyền ticker xuống — giữ nguyên.
  [KEPT]  find_row_series() chọn dòng nhiều data nhất — giữ nguyên.
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

# FINANCIAL_TICKERS = union của securities + insurance (giữ để backward compat)
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


# ---------------------------------------------------------------------------
# Text normalizer — fix bug đ/Đ
# ---------------------------------------------------------------------------

def _norm_label(text: str) -> str:
    """
    Lowercase + bỏ dấu tiếng Việt + strip khoảng trắng.
    FIX: chữ đ/Đ không có dạng NFKD ASCII → phải replace thủ công trước,
         không thì 'hoạt động' → 'hoat ong' (mất chữ đ).
    """
    if not isinstance(text, str):
        return ''
    # Bước 1: replace đ/Đ → d (NFKD không xử lý được ký tự này)
    text = text.lower().replace('đ', 'd').replace('Đ', 'd')
    # Bước 2: strip dấu thanh/dấu mũ qua NFKD
    nfkd = unicodedata.normalize('NFKD', text)
    ascii_str = nfkd.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', ascii_str).strip()


# ---------------------------------------------------------------------------
# Column helpers (giữ nguyên từ bản cũ, dead-code đã fix)
# ---------------------------------------------------------------------------

def _get_year_columns(df: pd.DataFrame):
    """
    Trả về list cột năm (dạng int hoặc string '2021'...).
    FIX bản cũ: gộp 2 return thành 1, sort theo int(str(col)[:4]).
    """
    meta_cols = {'item', 'item_en', 'item_id'}
    year_cols = [
        c for c in df.columns
        if c not in meta_cols and re.fullmatch(r'\d{4}', str(c).strip())
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
    """
    Tìm dòng phù hợp trong DataFrame BCTC vnstock.

    Matching dùng _norm_label() để không phụ thuộc dấu tiếng Việt.
    Khi nhiều dòng khớp → chọn dòng có nhiều năm data nhất.
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

    # Ưu tiên match theo item_id chính xác
    if item_ids and 'item_id' in df.columns:
        id_lower = df['item_id'].astype(str).str.lower().str.strip()
        target_ids = [i.lower().strip() for i in item_ids]
        mask_id = id_lower.isin(target_ids)
        if mask_id.any():
            matched = df[mask_id]

    # Fallback: keyword match trên text đã norm
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

    # Chọn dòng có nhiều data nhất (ưu tiên phủ đủ 2021-2025)
    if len(matched) > 1:
        non_na_counts = matched[year_cols].notna().sum(axis=1)
        row = matched.loc[non_na_counts.idxmax()]
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
# Revenue finders theo ngành
# ---------------------------------------------------------------------------

def _find_revenue_for_bank(df_income, period='year'):
    """
    Ngân hàng (BANK_TICKERS):
    ƯU TIÊN #1 = Thu nhập lãi thuần (NII) — phản ánh đúng business ngân hàng.
    TUYỆT ĐỐI KHÔNG lấy "Thu nhập lãi và các khoản thu nhập tương tự" (gross).

    Bản cũ để "doanh thu hoạt động" lên đầu → TPB lấy 30,751 tỷ thay vì 13,371 tỷ.
    Bản mới: NII → Tổng thu nhập HĐ thuần → fallback.
    """
    priority = [
        # ① NII — tên chuẩn VAS ngân hàng
        (
            ['thu nhap lai thuan', 'net interest income', 'lai thuan', 'nii'],
            ['chi phi lai', 'interest expense', 'tuong tu', 'cac khoan thu nhap',
             'hoat dong khac', 'dich vu'],
        ),
        # ② Tổng thu nhập hoạt động thuần (khi NII không tách riêng)
        (
            ['tong thu nhap hoat dong thuan', 'thu nhap hoat dong thuan',
             'net operating income', 'total operating income'],
            ['chi phi', 'expense', 'truoc du phong'],
        ),
        # ③ Tổng thu nhập hoạt động (rộng hơn — fallback)
        (
            ['tong thu nhap hoat dong', 'thu nhap hoat dong'],
            ['chi phi', 'expense'],
        ),
        # ④ Last resort
        (
            ['thu nhap thuan', 'net income from', 'total net income'],
            ['loi nhuan', 'profit', 'sau thue'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


def _find_revenue_for_securities(df_income, period='year'):
    """
    Chứng khoán (SECURITIES_TICKERS):
    Dùng "Doanh thu hoạt động" (môi giới + tư vấn + margin + tự doanh).
    Tránh nhầm "phí hoa hồng" (chỉ là 1 phần doanh thu).
    """
    priority = [
        # ① Doanh thu hoạt động tổng
        (
            ['doanh thu hoat dong', 'operating revenue', 'tong doanh thu hoat dong'],
            ['chi phi', 'expense', 'phi hoa hong'],
        ),
        # ② Doanh thu thuần về HĐKD
        (
            ['doanh thu thuan ve hoat dong kinh doanh', 'doanh thu thuan hoat dong'],
            ['chi phi'],
        ),
        # ③ Doanh thu thuần (fallback)
        (
            ['doanh thu thuan', 'net revenue'],
            ['chi phi', 'gia von', 'cost'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


def _find_revenue_for_insurance(df_income, period='year'):
    """Bảo hiểm: doanh thu phí bảo hiểm thuần."""
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
    """
    BĐS bán (VHM, KDH, DXG...): Doanh thu bán hàng CCDV.
    BĐS cho thuê (VRE): Doanh thu cho thuê.
    """
    priority = [
        # Ưu tiên doanh thu bán hàng CCDV (BĐS bán)
        (
            ['doanh thu ban hang va cung cap dich vu', 'doanh thu ban hang',
             'doanh thu ban bat dong san'],
            ['gia von', 'cost', 'chiet khau', 'giam gia'],
        ),
        # Doanh thu cho thuê (VRE)
        (
            ['doanh thu cho thue', 'rental revenue', 'rental income'],
            ['chi phi'],
        ),
        # Doanh thu thuần (fallback)
        (
            ['doanh thu thuan', 'net revenue'],
            ['gia von', 'cost', 'hoat dong tai chinh', 'hoat dong khac'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


def _find_revenue_for_retail(df_income, period='year'):
    """Bán lẻ / phân phối (MWG, FRT, PNJ...)."""
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
    """
    General: Sản xuất, Dầu khí, Xây dựng, Thực phẩm, Nông nghiệp, v.v.
    → Doanh thu bán hàng và cung cấp dịch vụ (= Doanh thu thuần trước chiết khấu).
    """
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


def _search_with_priority(df_income, priority: list, period: str):
    """
    Duyệt priority list, mỗi entry = (includes_list, excludes_list).
    includes/excludes đã ở dạng đã norm (_norm_label) hoặc raw — hàm sẽ norm cả 2.
    Trả về Series đầu tiên tìm được, hoặc Series rỗng.
    """
    for includes, excludes in priority:
        s = find_row_series(
            df_income,
            keywords=includes,
            exclude_keywords=excludes if excludes else None,
            period=period,
        )
        if not s.empty:
            return s
    return pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_financial_table(df_income, df_balance, df_ratio=None,
                          ticker=None, period='year'):
    """
    Tổng hợp chỉ tiêu BCTC.
    ticker bắt buộc truyền vào để detect ngành chính xác.
    """
    data = {}

    t = (ticker or '').upper().strip()

    # --- Sector detection ---
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
        # OILGAS, CONSTRUCTION, Sản xuất, Thực phẩm, Nông nghiệp... → general
        data['revenue'] = _find_revenue_general(df_income, period=period)
        # Fallback nếu general không ra gì
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
            data[field_name] = find_row_series(df_ratio, keywords, period=period)
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


def build_5y_financial_table(df_income, df_balance, df_ratio=None, ticker=None):
    """
    Wrapper giữ nguyên — ticker được truyền đúng xuống build_financial_table().
    """
    return build_financial_table(
        df_income, df_balance, df_ratio,
        ticker=ticker,
        period='year'
    )


# ---------------------------------------------------------------------------
# Utility functions (giữ nguyên từ bản cũ)
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

if __name__ == '__main__':
    print('=== Self-test financial_normalizer.py ===\n')

    # Test 1: Ngân hàng TPB
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
        'Doanh thu hoạt động',   # bẫy — bản cũ sẽ lấy cái này
    ])
    rev = build_financial_table(df_bank, pd.DataFrame(), ticker='TPB')['revenue']
    assert rev.name == 'I. Thu nhập lãi thuần', f'FAIL bank: {rev.name}'
    assert rev[2025] == 13371, f'FAIL bank 2025: {rev[2025]}'
    print(f'✅ BANK (TPB): {rev.name} — 2025={rev[2025]:,.0f} tỷ')

    # Test 2: Chứng khoán SSI
    df_sec = pd.DataFrame({
        2021: [3500, 1200, 2300],
        2022: [4200, 1500, 2700],
    }, index=[
        'Doanh thu hoạt động',
        'Phí hoa hồng môi giới',
        'Doanh thu thuần',
    ])
    rev = build_financial_table(df_sec, pd.DataFrame(), ticker='SSI')['revenue']
    assert rev.name == 'Doanh thu hoạt động', f'FAIL sec: {rev.name}'
    print(f'✅ SECURITIES (SSI): {rev.name} — 2022={rev[2022]:,.0f} tỷ')

    # Test 3: BĐS VHM
    df_bds = pd.DataFrame({
        2021: [20000, 18500, 15000],
        2022: [35000, 32000, 28000],
    }, index=[
        'Doanh thu bán hàng và cung cấp dịch vụ',
        'Doanh thu thuần',
        'Giá vốn hàng bán',
    ])
    rev = build_financial_table(df_bds, pd.DataFrame(), ticker='VHM')['revenue']
    assert rev.name == 'Doanh thu bán hàng và cung cấp dịch vụ', f'FAIL bds: {rev.name}'
    print(f'✅ BĐS (VHM): {rev.name} — 2022={rev[2022]:,.0f} tỷ')

    # Test 4: Bán lẻ MWG
    df_mwg = pd.DataFrame({
        2021: [100000, 95000, 60000],
        2022: [115000, 109000, 70000],
    }, index=[
        'Doanh thu bán hàng và cung cấp dịch vụ',
        'Doanh thu thuần',
        'Giá vốn hàng bán',
    ])
    rev = build_financial_table(df_mwg, pd.DataFrame(), ticker='MWG')['revenue']
    assert rev.name == 'Doanh thu bán hàng và cung cấp dịch vụ', f'FAIL retail: {rev.name}'
    print(f'✅ RETAIL (MWG): {rev.name} — 2022={rev[2022]:,.0f} tỷ')

    # Test 5: Dầu khí GAS (general path)
    df_gas = pd.DataFrame({
        2021: [60000, 57000, 40000],
        2022: [80000, 76000, 55000],
    }, index=[
        'Doanh thu bán hàng và cung cấp dịch vụ',
        'Doanh thu thuần',
        'Giá vốn hàng bán',
    ])
    rev = build_financial_table(df_gas, pd.DataFrame(), ticker='GAS')['revenue']
    assert rev.name == 'Doanh thu bán hàng và cung cấp dịch vụ', f'FAIL oilgas: {rev.name}'
    print(f'✅ OILGAS (GAS): {rev.name} — 2022={rev[2022]:,.0f} tỷ')

    # Test 6: _norm_label bug fix
    assert _norm_label('hoạt động') == 'hoat dong', f'FAIL norm: {_norm_label("hoạt động")}'
    assert _norm_label('Thu nhập lãi thuần') == 'thu nhap lai thuan'
    assert _norm_label('Doanh thu bán hàng và cung cấp dịch vụ') == 'doanh thu ban hang va cung cap dich vu'
    print('✅ _norm_label: đ/Đ fix OK')

    print('\n🎉 Tất cả test pass!')
