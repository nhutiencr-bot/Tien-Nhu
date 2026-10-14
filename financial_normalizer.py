# ruff: noqa
# pylint: skip-file
r"""
financial_normalizer.py
------------------------
Tìm DÒNG chỉ tiêu trong báo cáo tài chính vnstock và đọc các cột năm/quý của dòng đó.

Luật (không có xử lý riêng cho năm nào):
  - Doanh thu theo NGÀNH, mỗi ngành MỘT khái niệm:
      Ngân hàng = Thu nhập lãi thuần · CTCK = Doanh thu hoạt động ·
      Bảo hiểm = Doanh thu phí bảo hiểm · Còn lại = Doanh thu thuần (MS10).
    Chọn tier ưu tiên đầu tiên có dữ liệu và dùng cho TOÀN BỘ các năm.
  - Khi nhiều dòng cùng khớp từ khoá: xếp hạng theo độ khớp NHÃN (nguyên văn > bắt đầu
    bằng > chứa), trừ điểm nhãn nhiễu (giảm trừ, tỷ lệ, tài chính...), rồi mới tới số ô
    có dữ liệu, cuối cùng nhãn ngắn nhất.
  - Cột năm thuần ('2025', 2025.0) luôn thắng cột '2025-Q4'.
  - Nhãn được chuẩn hoá bỏ dấu (đ/Đ -> d) trước khi so khớp.
Việc ghép năm thiếu bằng tổng 4 quý nằm ở financials_core.py.
"""

import re
import unicodedata
import datetime as _dt
import pandas as pd


# ---------------------------------------------------------------------------
# DANH SÁCH NĂM — định nghĩa MỘT lần, cố định: 2021 → 2025. Mọi module import từ đây.
# ---------------------------------------------------------------------------
TARGET_YEARS = [2021, 2022, 2023, 2024, 2025]
TARGET_YEAR = TARGET_YEARS[-1]
IN_PROGRESS_YEAR = _dt.date.today().year      # chỉ data_fetcher dùng (hiển thị năm hiện tại)


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


# ---------------------------------------------------------------------------
# [FIX 10] Semantic ID (Taxonomy) — vnstock v3.2.8
# ---------------------------------------------------------------------------

# [FIX 21] Ngành tài chính: MỘT khái niệm doanh thu duy nhất, không rơi sang khái niệm khác.
BANK_REVENUE_IDS = [
    'NI_NET_INTEREST_INCOME',          # Thu nhập lãi thuần (NII)
]

SECURITIES_REVENUE_IDS = [
    'NI_OPERATING_REVENUE',
    'NI_TOTAL_OPERATING_REVENUE',
]

INSURANCE_REVENUE_IDS = [
    'NI_NET_PREMIUM_REVENUE',
    'NI_INSURANCE_OPERATING_REVENUE',
    'NI_TOTAL_OPERATING_REVENUE',
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
# [FIX 16] Nhận diện nhãn cột năm/quý linh hoạt + adapter dữ liệu dạng dài
# ---------------------------------------------------------------------------

_META_COLS = {'item', 'item_en', 'item_id'}

_RE_YEAR = re.compile(r'^(\d{4})(?:\.0+)?$')
_RE_YEAR_Q4 = re.compile(r'^(?:(\d{4})\s*[-_/ ]?\s*Q4|Q4\s*[-_/ ]?\s*(\d{4}))$', re.I)
_RE_FY = re.compile(r'^(?:FY|CY)\s*[-_ ]?(\d{4})$', re.I)
_RE_Q_A = re.compile(r'^(\d{4})\s*[-_/ ]?\s*Q([1-4])$', re.I)
_RE_Q_B = re.compile(r'^Q([1-4])\s*[-_/ ]?\s*(\d{4})$', re.I)


def _col_year(c):
    """Nhãn cột -> năm (int) nếu là cột NĂM; None nếu không phải.
    Nhận: 2025, '2025', 2025.0, '2025.0', '2025-Q4', 'Q4/2025', 'FY2025'."""
    s = str(c).strip()
    for rx in (_RE_YEAR, _RE_FY):
        m = rx.match(s)
        if m:
            return int(m.group(1))
    m = _RE_YEAR_Q4.match(s)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def _col_quarter(c):
    """Nhãn cột -> 'YYYY-Qn' nếu là cột QUÝ; None nếu không."""
    s = str(c).strip()
    m = _RE_Q_A.match(s)
    if m:
        return f'{int(m.group(1))}-Q{int(m.group(2))}'
    m = _RE_Q_B.match(s)
    if m:
        return f'{int(m.group(2))}-Q{int(m.group(1))}'
    return None


def _lower_meta_cols(df):
    """Item/ITEM/Item_En -> item/item_en (không phân biệt hoa thường)."""
    ren = {}
    for c in df.columns:
        lc = str(c).strip().lower()
        if lc in _META_COLS and c != lc:
            ren[c] = lc
    return df.rename(columns=ren) if ren else df


def _to_wide(df: pd.DataFrame, period='year'):
    """
    Dạng DÀI (mỗi hàng = 1 kỳ, mỗi chỉ tiêu = 1 cột; có cột date/year/quarter)
    -> dạng RỘNG (cột 'item' + cột năm/quý) mà phần còn lại của file mong đợi.
    Không đoán được thì trả nguyên df.
    """
    lower = {str(c).strip().lower(): c for c in df.columns}
    date_key = next((lower[k] for k in
                     ('date', 'period', 'time', 'report_date', 'reportdate',
                      'ky_bao_cao', 'ngay') if k in lower), None)
    year_key = lower.get('year') or lower.get('nam')
    q_key = (lower.get('quarter') or lower.get('lengthreport')
             or lower.get('quy') or lower.get('lengthReport'))

    yrs, qs = None, None
    if year_key is not None:
        yrs = pd.to_numeric(df[year_key], errors='coerce')
        if q_key is not None:
            qs = pd.to_numeric(df[q_key], errors='coerce')
    elif date_key is not None:
        dt = pd.to_datetime(df[date_key], errors='coerce')
        if dt.notna().sum() == 0:
            return df
        yrs = dt.dt.year.astype('float')
        qs = dt.dt.quarter.astype('float')
        mon = dt.dt.month
    else:
        return df

    d = df.copy()
    d['__y'] = yrs
    d['__q'] = qs if qs is not None else float('nan')
    d = d[d['__y'].notna()]
    if d.empty:
        return df

    if period == 'quarter':
        d = d[d['__q'].between(1, 4)]
        d['__label'] = [f'{int(y)}-Q{int(q)}' for y, q in zip(d['__y'], d['__q'])]
    else:
        # Năm: nếu có nhiều kỳ/năm (dữ liệu quý) -> lấy kỳ cuối năm (Q4)
        if qs is not None and d['__y'].duplicated().any():
            d = d[(d['__q'] == 4) | (d['__q'].isna()) | (d['__q'] == 0) | (d['__q'] == 5)]
        elif date_key is not None and year_key is None and d['__y'].duplicated().any():
            d = d[mon.loc[d.index] == 12]
        d = d.drop_duplicates('__y', keep='last')
        d['__label'] = d['__y'].astype(int)

    if d.empty:
        return df

    skip = {c for c in (date_key, year_key, q_key, '__y', '__q', '__label') if c is not None}
    value_cols = [c for c in d.columns if c not in skip]
    wide = d.set_index('__label')[value_cols].T
    wide.index.name = None
    wide.columns.name = None
    wide = wide.reset_index().rename(columns={'index': 'item'})
    wide['item_en'] = wide['item']
    wide['item_id'] = wide['item']
    return wide


def _prepare_df(df: pd.DataFrame, period='year'):
    """Chuẩn hoá mọi biến thể DataFrame đầu vào về dạng rộng: item + cột năm/quý."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df
    df = _lower_meta_cols(df)

    has_period_cols = any(
        (_col_year(c) is not None or _col_quarter(c) is not None)
        for c in df.columns if c not in _META_COLS
    )
    if has_period_cols:
        # Tên chỉ tiêu nằm ở index thay vì cột 'item'
        if not (set(df.columns) & _META_COLS) and not pd.api.types.is_numeric_dtype(df.index):
            df = df.reset_index().rename(columns={df.index.name or 'index': 'item'})
        return df

    return _to_wide(df, period=period)


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
        # '2025-Q4' / '2025' / 2025.0 / 'FY2025' -> int 2025   [FIX 16]
        yr = _col_year(k)
        if yr is None:
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
    """Return annual columns — '2024', 2024, 2024.0, '2025-Q4', 'FY2025'... [FIX 16]"""
    year_cols = [
        c for c in df.columns
        if c not in _META_COLS and _col_year(c) is not None
    ]
    return sorted(year_cols, key=_col_year)


def _quarter_sort_key(c):
    y, q = str(c).strip().split('-Q')
    return (int(y), int(q))


def _get_quarter_columns(df: pd.DataFrame):
    q_cols = [
        c for c in df.columns
        if c not in _META_COLS and _col_quarter(c) is not None
    ]
    return sorted(q_cols, key=lambda c: _quarter_sort_key(_col_quarter(c)))



# [FIX 19] Helper cho việc xếp hạng dòng + phân biệt cột năm / cột Q4
_NOISE_TOKENS = (
    'giam tru', 'ty le', 'ty suat', 'tang truong', 'growth', 'margin', 'bien ',
    '%', 'tai chinh', 'financial', 'khac', 'other', 'lien doanh', 'lien ket',
    'cho thue', 'noi bo', 'intersegment', 'tren co phieu', 'per share',
)
_RE_LEAD_NUM = re.compile(r'^(?:[ivxlcdm]+|\d+)(?:\.\d+)*[\.\)]\s*', re.I)


def _clean_row_label(text) -> str:
    """Nhãn dòng chuẩn hoá: bỏ dấu, bỏ số thứ tự đầu dòng ('10. ', 'II. ', '1.1 ')."""
    t = _norm_label(str(text) if text is not None else '')
    prev = None
    while prev != t:            # bóc nhiều lớp: "I. 1. ..."
        prev = t
        t = _RE_LEAD_NUM.sub('', t).strip()
    return t


def _is_q4_col(c) -> int:
    """1 nếu cột là dạng '2025-Q4' / 'Q4/2025', 0 nếu là cột năm thuần."""
    return 1 if _RE_YEAR_Q4.match(str(c).strip()) else 0

# ---------------------------------------------------------------------------
# Core row finder
# ---------------------------------------------------------------------------

def find_row_series(df: pd.DataFrame, keywords, exclude_keywords=None,
                    item_ids=None, prefer_top_level=True, period='year'):
    if df is None or df.empty:
        return pd.Series(dtype=float)

    df = _prepare_df(df, period=period)   # [FIX 16] dạng dài / tên cột lạ -> dạng rộng
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

    if len(matched) > 1:
        # [FIX 19] "BẪY TÊN GỌI": trước đây chọn dòng có NHIỀU ô có dữ liệu nhất
        # trong số dòng khớp từ khoá lỏng → dễ trúng dòng phụ/dòng tỷ lệ
        # ("Doanh thu thuần hoạt động tài chính", "Tăng trưởng doanh thu thuần"...)
        # có nhiều năm hơn dòng chuẩn → doanh thu năm mới nhất lệch.
        # Giờ xếp hạng theo ĐỘ KHỚP NHÃN: khớp nguyên văn từ khoá > bắt đầu bằng
        # từ khoá > chỉ chứa từ khoá; trừ điểm dòng có từ nhiễu; rồi mới tới
        # số ô có dữ liệu, cuối cùng là nhãn ngắn nhất (dòng tổng thường gọn hơn).
        latest_col = year_cols[-1]
        has_latest = matched[latest_col].notna()
        candidates = matched[has_latest] if has_latest.any() else matched
        non_na_counts = candidates[year_cols].notna().sum(axis=1)
        primary = 'item' if 'item' in candidates.columns else search_cols[0]
        kws_norm = [_norm_label(k) for k in keywords]

        def _rank(idx):
            lab = _clean_row_label(candidates.at[idx, primary])
            if any(lab == k for k in kws_norm):
                mr = 0
            elif any(lab.startswith(k) for k in kws_norm):
                mr = 1
            else:
                mr = 2
            noise = sum(1 for t in _NOISE_TOKENS if t in lab)
            return (mr, noise, -int(non_na_counts.at[idx]), len(lab))

        best_idx = min(candidates.index, key=_rank)
        row = candidates.loc[best_idx]
    else:
        row = matched.iloc[0]

    # Build result dict
    result = {}
    # [FIX 19] Cột NĂM thuần ('2025', 2025.0) luôn được xử lý TRƯỚC cột '2025-Q4'.
    # Cột '2025-Q4' có thể là số RIÊNG QUÝ 4 (không phải cả năm); nếu nó đứng trước
    # '2025' trong DataFrame thì cơ chế "không ghi đè" cũ giữ nhầm số quý làm
    # doanh thu cả năm 2025. Chỉ dùng Q4 khi năm đó không có cột năm thuần.
    if period != 'quarter':
        year_cols = sorted(year_cols, key=lambda c: (_col_year(c), _is_q4_col(c)))
    for yc in year_cols:
        val = pd.to_numeric(pd.Series([row[yc]]), errors='coerce').iloc[0]
        if pd.notna(val):
            if period == 'quarter':
                result[_col_quarter(yc)] = float(val)   # [FIX 16] key 'YYYY-Qn' chuẩn
            else:
                # [FIX 12] Cột '2025-Q4' → key int 2025; không ghi đè key đã có
                yr = _col_year(yc)   # [FIX 16]
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

def _resolve_year_tiers(all_tier_series):
    """
    [FIX 21] Nhận danh sách Series theo THỨ TỰ ƯU TIÊN (mỗi phần tử = MỘT khái niệm kế
    toán). Trả về tier đầu tiên có dữ liệu, dùng cho TOÀN BỘ các năm.

    Đã BỎ cơ chế cũ "tier đầu thiếu TARGET_YEAR thì mượn riêng năm đó từ tier sau":
    đó chính là cách trộn 2 khái niệm trong cùng một chuỗi (HDB/TCB 2025 = Tổng thu nhập
    hoạt động thay vì NII). Thiếu năm nào thì để trống; pipeline chỉ bù bằng tổng 4 quý
    của CHÍNH dòng đó (financials_core.resolve_flow).
    """
    for s in all_tier_series:
        if s is not None and not s.empty and not s.dropna().empty:
            return s.sort_index()
    return pd.Series(dtype=float)


def _search_with_priority(df_income, priority: list, period: str):
    """
    Duyệt priority list theo thứ tự ưu tiên (mỗi tier = một khái niệm kế
    toán được đặt tên rõ ràng, vd tier 1 = NII, tier 2 = Tổng thu nhập HĐ...).
    Xem _resolve_year_tiers() để biết cách gộp lịch sử/TARGET_YEAR.
    """
    if period != 'year':
        # Quarter: giữ hành vi cũ — trả về tier đầu tiên có dữ liệu
        for includes, excludes in priority:
            s = find_row_series(
                df_income, keywords=includes,
                exclude_keywords=excludes if excludes else None, period=period,
            )
            if s is not None and not s.empty and not s.dropna().empty:
                return s
        return pd.Series(dtype=float)

    all_tier_series = [
        find_row_series(
            df_income, keywords=includes,
            exclude_keywords=excludes if excludes else None, period='year',
        )
        for includes, excludes in priority
    ]
    return _resolve_year_tiers(all_tier_series)


# ---------------------------------------------------------------------------
# Revenue finders theo ngành
# ---------------------------------------------------------------------------

def _find_revenue_for_bank(df_income, period='year'):
    """
    [FIX 14] Loại trừ 'tuong tu' mạnh hơn để tránh pick dòng gross interest income.
    Ưu tiên NII (thu nhập lãi thuần) — sau khi đã trừ chi phí lãi.
    Fallback gross chỉ dùng khi NII hoàn toàn rỗng.
    """
    priority = [
        (
            # NII — loại trừ "tương tự" để không pick gross interest income
            ['thu nhap lai thuan', 'net interest income', 'lai thuan', 'nii'],
            ['chi phi lai', 'interest expense', 'tuong tu',
             'cac khoan thu nhap', 'hoat dong khac', 'dich vu',
             'thu nhap lai va', 'du phong'],
        ),
    ]

    if period != 'year':
        for tax_id in BANK_REVENUE_IDS:
            s = find_row_series(
                df_income, keywords=['net interest income', 'thu nhap lai thuan'],
                item_ids=[tax_id], period=period,
            )
            if s is not None and not s.empty and not s.dropna().empty:
                return s
        return _search_with_priority(df_income, priority, period)

    # [ROOT-FIX v4] Gộp taxonomy-ID tiers + keyword tiers thành MỘT danh
    # sách ưu tiên duy nhất, xử lý qua _resolve_year_tiers — không còn
    # vứt bỏ nguyên series taxonomy chỉ vì thiếu đúng TARGET_YEAR.
    tier_series = [
        find_row_series(
            df_income, keywords=['net interest income', 'thu nhap lai thuan'],
            item_ids=[tax_id], period='year',
        )
        for tax_id in BANK_REVENUE_IDS
    ]
    tier_series += [
        find_row_series(
            df_income, keywords=includes,
            exclude_keywords=excludes if excludes else None, period='year',
        )
        for includes, excludes in priority
    ]
    return _resolve_year_tiers(tier_series)


def _find_revenue_for_securities(df_income, period='year'):
    priority = [
        (
            ['doanh thu hoat dong', 'operating revenue', 'tong doanh thu hoat dong'],
            ['chi phi', 'expense', 'phi hoa hong'],
        ),
        (
            ['doanh thu thuan ve hoat dong kinh doanh', 'doanh thu thuan hoat dong'],
            ['chi phi'],
        ),
    ]

    if period != 'year':
        for tax_id in SECURITIES_REVENUE_IDS:
            s = find_row_series(
                df_income, keywords=['doanh thu hoat dong', 'operating revenue'],
                item_ids=[tax_id], period=period,
            )
            if s is not None and not s.empty and not s.dropna().empty:
                return s
        return _search_with_priority(df_income, priority, period)

    # [ROOT-FIX v4] Gộp taxonomy-ID + keyword thành 1 danh sách ưu tiên
    tier_series = [
        find_row_series(
            df_income, keywords=['doanh thu hoat dong', 'operating revenue'],
            item_ids=[tax_id], period='year',
        )
        for tax_id in SECURITIES_REVENUE_IDS
    ]
    tier_series += [
        find_row_series(
            df_income, keywords=includes,
            exclude_keywords=excludes if excludes else None, period='year',
        )
        for includes, excludes in priority
    ]
    return _resolve_year_tiers(tier_series)


def _find_revenue_for_insurance(df_income, period='year'):
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
    ]

    if period != 'year':
        for tax_id in INSURANCE_REVENUE_IDS:
            s = find_row_series(
                df_income, keywords=['phi bao hiem thuan', 'net premium'],
                item_ids=[tax_id], period=period,
            )
            if s is not None and not s.empty and not s.dropna().empty:
                return s
        return _search_with_priority(df_income, priority, period)

    # [ROOT-FIX v4] Gộp taxonomy-ID + keyword thành 1 danh sách ưu tiên
    tier_series = [
        find_row_series(
            df_income, keywords=['phi bao hiem thuan', 'net premium'],
            item_ids=[tax_id], period='year',
        )
        for tax_id in INSURANCE_REVENUE_IDS
    ]
    tier_series += [
        find_row_series(
            df_income, keywords=includes,
            exclude_keywords=excludes if excludes else None, period='year',
        )
        for includes, excludes in priority
    ]
    return _resolve_year_tiers(tier_series)


def _find_revenue_for_realestate(df_income, period='year'):
    # [FIX 15] "doanh thu thuan" (MS10, net) lên tier 0 — xem giải thích ở
    # docstring đầu file. Gross (MS01) và doanh thu cho thuê chỉ dùng khi
    # công ty không tách riêng dòng MS10.
    priority = [
        (
            ['doanh thu thuan', 'net revenue'],
            ['gia von', 'cost', 'hoat dong tai chinh', 'hoat dong khac'],
        ),
        (
            ['doanh thu ban hang va cung cap dich vu', 'doanh thu ban hang',
             'doanh thu ban bat dong san'],
            ['gia von', 'cost', 'chiet khau', 'giam gia'],
        ),
        (
            ['doanh thu cho thue', 'rental revenue', 'rental income'],
            ['chi phi'],
        ),
    ]
    return _search_with_priority(df_income, priority, period)


def _find_revenue_for_retail(df_income, period='year'):
    # [FIX 15] "doanh thu thuan" (MS10, net) lên tier 0 — xem giải thích ở
    # docstring đầu file.
    priority = [
        (
            ['doanh thu thuan', 'net revenue', 'net sales'],
            ['gia von', 'chi phi lai'],
        ),
        (
            ['doanh thu ban hang va cung cap dich vu',
             'doanh thu thuan ve ban hang va cung cap dich vu'],
            ['gia von', 'cost'],
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
    # [FIX 15] "doanh thu thuan" (MS10, net) lên tier 0 — xem giải thích ở
    # docstring đầu file. Trước đây gross (MS01) là tier 0, khiến cột
    # "Doanh thu thuần" hiển thị nhầm doanh thu gộp khi MS01 và MS10 lệch
    # nhau (điển hình lộ ra ở năm mới nhất — 2025).
    priority = [
        (
            ['doanh thu thuan', 'net revenue', 'net sales'],
            ['gia von', 'cost of', 'hoat dong tai chinh', 'hoat dong khac'],
        ),
        (
            ['doanh thu ban hang va cung cap dich vu', 'doanh thu ban hang',
             'revenue from goods and services', 'sales revenue'],
            ['gia von', 'cost of', 'chiet khau', 'giam gia', 'hang ban tra lai'],
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
    """CFO theo năm từ dòng LCTT hoạt động kinh doanh (không có nhánh riêng cho năm nào)."""
    return find_row_series(df_cashflow_y, keywords=CFO_KEYWORDS, period='year')


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_financial_table(df_income, df_balance, df_ratio=None,
                          ticker=None, period='year',
                          df_cashflow_y=None, df_cashflow_q=None):
    data = {}
    t = (ticker or '').upper().strip()
    # Chuẩn hoá khóa '2025-Q4' -> 2025 CHỈ áp dụng cho dữ liệu NĂM. Với dữ liệu quý phải giữ
    # nguyên khóa 'YYYY-Qn', nếu không 4 quý của một năm bị gộp thành 1 ô.
    _align_keys = _align_2025 if period == 'year' else (lambda s: s)

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
    data['revenue'] = _align_keys(data['revenue'])

    # --- Lợi nhuận sau thuế ---
    data['net_profit'] = _align_keys(find_row_series(
        df_income,
        ['loi nhuan sau thue', 'net profit', 'profit after tax', 'net income',
         'loi nhuan thuan', 'lai sau thue'],
        exclude_keywords=['truoc thue', 'before tax', 'thieu so', 'minority'],
        item_ids=NET_PROFIT_IDS,
        period=period,
    ))

    # --- EPS từ income statement ---
    data['eps_income_stmt'] = _align_keys(find_row_series(
        df_income,
        ['lai co ban tren co phieu', 'earnings per share', 'eps'],
        item_ids=['eps'], period=period,
    ))

    # --- Balance sheet ---
    data['equity'] = _align_keys(find_row_series(
        df_balance,
        ['von chu so huu', "owner's equity", 'owners equity', 'total equity',
         'equity', 'vcsh'],
        exclude_keywords=['von dieu le', 'charter', 'co phan uu dai'],
        period=period,
    ))

    data['total_assets'] = _align_keys(find_row_series(
        df_balance,
        ['tong cong tai san', 'total assets', 'tong tai san'],
        period=period,
    ))

    # --- CFO với fallback quý ---
    if period == 'year' and df_cashflow_y is not None:
        data['cfo'] = _align_keys(_find_cfo_with_quarterly_fallback(df_cashflow_y, df_cashflow_q))
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
            data[field_name] = _align_keys(find_row_series(df_ratio, keywords, period=period))
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
