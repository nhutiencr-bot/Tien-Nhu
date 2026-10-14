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
# Khung năm hiển thị. TARGET_YEAR = năm cuối ĐÃ đóng sổ (BCTC năm công bố hạn chót ~31/3
# năm sau, nên từ tháng 4 trở đi mới coi năm trước là đã đóng sổ).
# IN_PROGRESS_YEAR = năm lịch hiện tại, chỉ dùng cho bảng quý (Q1..Qn).
# ---------------------------------------------------------------------------

def _compute_target_year(today: _dt.date = None) -> int:
    """
    Năm CUỐI của bảng 5 năm — năm gần nhất được coi là đã đóng sổ.

    Quy tắc: doanh nghiệp niêm yết VN phải công bố BCTC năm (kiểm toán)
    trong vòng ~90 ngày sau khi kết thúc năm tài chính, hạn chót thường
    là 31/3 năm sau. Để an toàn, chỉ coi năm N là "đã đóng sổ" (đưa vào
    bảng) kể từ tháng 4 năm N+1 trở đi; nếu đang ở Q1 (tháng 1-3), lùi
    thêm 1 năm nữa vì báo cáo của năm N có thể chưa công bố xong.
    """
    d = today or _dt.date.today()
    if d.month >= 4:
        return d.year - 1
    return d.year - 2


def _compute_in_progress_year(today: _dt.date = None) -> int:
    """Năm lịch THỰC TẾ hôm nay — năm chắc chắn chưa đóng sổ (≤4 quý)."""
    d = today or _dt.date.today()
    return d.year


TARGET_YEAR = _compute_target_year()
IN_PROGRESS_YEAR = _compute_in_progress_year()
TARGET_YEARS = list(range(TARGET_YEAR - 4, TARGET_YEAR + 1))  # 5 năm gần nhất ĐÃ đóng sổ


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

# (TARGET_YEARS / TARGET_YEAR đã được tính động ở đầu file — xem [ROOT-FIX])

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


if __name__ == '__main__':
    print('=== Self-test financial_normalizer.py ===\n')
    print(f'TARGET_YEAR = {TARGET_YEAR}  (tính động theo ngày hệ thống, KHÔNG hardcode)')
    Y0, Y1, Y2, Y3, Y4 = TARGET_YEARS  # 5 năm gần nhất, vd [2021..2025] hoặc cuốn chiếu

    # --- Test 1: Bank revenue pick đúng NII, không pick gross ---
    df_bank = _make_df(
        ['Thu nhập lãi và các khoản thu nhập tương tự',   # gross — KHÔNG pick
         'Chi phí lãi và các chi phí tương tự',
         'I. Thu nhập lãi thuần',                          # NII — PHẢI pick
         'II. Thu nhập từ hoạt động dịch vụ thuần',
         'Doanh thu thuần'],                               # sai — KHÔNG pick
        {Y0:  [70749, 28476, 42273, 5000,  2500],
         Y1:  [88113, 34867, 53246, 6200,  3000],
         Y2:  [108122,54508, 53615, 7100,  3500],
         Y3:  [93655, 38249, 55406, 7500,  3800],
         f'{Y4}-Q4': [105216,36162, 58771, 8200, 69054]},  # năm mới nhất dạng Q4
    )

    rev = build_financial_table(df_bank, pd.DataFrame(), ticker='VCB')['revenue']
    print(f'VCB revenue: {dict(rev)}')
    assert rev.get(Y4) == 58771, f'FAIL {Y4}: {rev.get(Y4)} (expected 58771)'
    assert rev.get(Y0) == 42273, f'FAIL {Y0}: {rev.get(Y0)}'
    assert rev.get(Y3) == 55406, f'FAIL {Y3}: {rev.get(Y3)}'
    print('✅ BANK (VCB): NII đúng cả 5 năm, không pick gross hay Doanh thu thuần')

    # --- Test 2: TPB giữ nguyên ---
    df_tpb = _make_df(
        ['1. Thu nhập lãi và các khoản thu nhập tương tự',
         '2. Chi phí lãi và các chi phí tương tự',
         'I. Thu nhập lãi thuần',
         'II. Thu nhập từ hoạt động dịch vụ thuần',
         'Doanh thu hoạt động'],
        {Y0:  [17427, 7481,  9946,  5000, 2500],
         Y1:  [21811, 10424, 11387, 6200, 3000],
         Y2:  [28562, 16135, 12428, 7100, 3500],
         Y3:  [25949, 13042, 12907, 7500, 3800],
         f'{Y4}-Q4': [30751, 17379, 13371, 8200, 4000]},
    )
    rev_tpb = build_financial_table(df_tpb, pd.DataFrame(), ticker='TPB')['revenue']
    assert rev_tpb.get(Y4) == 13371, f'FAIL TPB {Y4}: {rev_tpb.get(Y4)}'
    print(f'✅ BANK (TPB): {Y4}={rev_tpb.get(Y4):,.0f} (NII đúng, không pick gross 30,751)')

    # --- Test 3: _align_2025 (đổi tên ý nghĩa: align năm mới nhất) ---
    s_test = pd.Series({f'{Y4}-Q4': 58771.0, Y3: 55406.0, Y2: 53615.0})
    aligned = _align_2025(s_test)
    assert Y4 in aligned.index, f'FAIL: {Y4} không có sau align'
    assert aligned[Y4] == 58771.0
    print(f'✅ _align_2025: {Y4}-Q4 → int {Y4} OK')

    # --- Test 4: _search_with_priority không return sớm khi thiếu TARGET_YEAR ---
    df_missing_latest = _make_df(
        ['Thu nhập lãi thuần'],
        {Y0: [42273], Y1: [53246], Y2: [53615], Y3: [55406]},
    )
    df_has_latest = _make_df(
        ['Doanh thu thuần'],
        {Y0: [100], Y1: [110], Y2: [120], Y3: [130], Y4: [999]},
    )
    df_combined = pd.concat([df_missing_latest, df_has_latest], ignore_index=True)
    priority_test = [
        (['thu nhap lai thuan'], ['tuong tu', 'chi phi']),
        (['doanh thu thuan'], []),
    ]
    result = _search_with_priority(df_combined, priority_test, 'year')
    # [FIX 18] Doanh thu thuần (100..999) KHÁC khái niệm NII (42k..55k) -> không được mượn
    assert Y4 not in result.index or pd.isna(result.get(Y4)), f'FAIL: mượn nhầm khái niệm: {result.get(Y4)}'
    assert result.get(Y0) == 42273
    print('✅ _search_with_priority [FIX 18]: không mượn năm mới nhất từ dòng khác khái niệm')

    # --- Test 4c: [ROOT-FIX v4] _resolve_year_tiers — tách theo TIER
    #     (khái niệm đặt tên rõ ràng), KHÔNG tách theo dòng khớp mờ như
    #     ROOT-FIX v3 (đã revert vì gây lỗi thực tế trên VCB: 2025 bị
    #     "mượn" nhầm giá trị 69,054 từ dòng khác thay vì 58,771 đúng).
    #
    # Trường hợp 1 — bình thường: tier ưu tiên cao nhất (NII) có ĐỦ cả
    # 2021-2024 lẫn TARGET_YEAR → dùng nguyên tier đó, KHÔNG được lấy giá
    # trị TARGET_YEAR từ tier khác dù tier khác có giá trị lớn hơn.
    # (Đây chính là bug đã xảy ra: nếu code lỡ ưu tiên "tier nào có
    # TARGET_YEAR" thay vì "tier 0 nếu đã đủ", 58,771 sẽ bị 69,054 ghi đè.)
    tier0_full = pd.Series({Y0: 42273.0, Y1: 53246.0, Y2: 53615.0, Y3: 55406.0, Y4: 58771.0})
    tier1_bigger = pd.Series({Y0: 50000.0, Y1: 62000.0, Y2: 63000.0, Y3: 65000.0, Y4: 69054.0})
    resolved = _resolve_year_tiers([tier0_full, tier1_bigger])
    assert resolved[Y4] == 58771.0, (
        f'FAIL: TARGET_YEAR bị ghi đè bởi tier khác dù tier 0 đã có đủ dữ liệu '
        f'({resolved[Y4]} thay vì 58771.0) — đúng dạng bug đã gặp trên VCB.'
    )
    assert resolved[Y0] == 42273.0
    print(f'✅ _resolve_year_tiers: tier 0 đủ dữ liệu → dùng nguyên, không bị tier khác '
          f'ghi đè TARGET_YEAR (58771 giữ nguyên, không lẫn 69054)')

    # Trường hợp 2 — tier 0 THỰC SỰ thiếu TARGET_YEAR (vd NaN thật) →
    # mới mượn từ tier 1, nhưng KHÔNG đụng tới 2021-2024 của tier 0.
    tier0_missing_target = pd.Series({Y0: 42273.0, Y1: 53246.0, Y2: 53615.0, Y3: 55406.0})
    # [FIX 18] tier 1 KHÁC khái niệm (tỷ lệ ~1.2x, như TOI so với NII) -> KHÔNG được mượn
    resolved2 = _resolve_year_tiers([tier0_missing_target, tier1_bigger])
    assert Y4 not in resolved2.index or pd.isna(resolved2.get(Y4)), (
        f'FAIL: mượn TARGET_YEAR từ khái niệm khác: {resolved2.get(Y4)}')
    assert resolved2[Y0] == 42273.0, 'FAIL: 2021-2024 của tier 0 bị đụng vào'
    # tier 1 CÙNG khái niệm (khớp ±8% trên năm trùng) -> mượn được
    tier1_same = pd.Series({Y0: 42300.0, Y1: 53200.0, Y2: 53700.0, Y3: 55400.0, Y4: 58771.0})
    resolved3 = _resolve_year_tiers([tier0_missing_target, tier1_same])
    assert resolved3[Y4] == 58771.0 and resolved3[Y0] == 42273.0, dict(resolved3)
    # HDB: NII 2022-2024, tier sau là TOI (≈1.22x) có 2025 -> phải bỏ TOI
    hdb_nii = pd.Series({2022: 18011.57, 2023: 22183.97, 2024: 30857.08})
    hdb_toi = pd.Series({2022: 21900.0, 2023: 27000.0, 2024: 37600.0, 2025: 42675.0})
    r_hdb = _resolve_year_tiers([hdb_nii, hdb_toi])
    assert 2025 not in r_hdb.index, f'FAIL HDB: 2025 vẫn lấy từ TOI: {r_hdb.get(2025)}'
    print('✅ _resolve_year_tiers [FIX 18]: chỉ mượn TARGET_YEAR khi cùng khái niệm; '
          'HDB không còn lấy 2025=42,675 (TOI) thay cho NII')

    # --- Test 4b: [ROOT-FIX v2 regression] TARGET_YEAR phải là năm ĐÃ đóng sổ,
    #     KHÔNG bao giờ được bằng năm lịch hiện tại (IN_PROGRESS_YEAR) ---
    assert TARGET_YEAR < IN_PROGRESS_YEAR, (
        f'FAIL: TARGET_YEAR ({TARGET_YEAR}) phải nhỏ hơn IN_PROGRESS_YEAR '
        f'({IN_PROGRESS_YEAR}) — nếu bằng nhau, năm chưa đóng sổ sẽ lọt '
        f'vào bảng 5 năm (đúng bug đã gặp: cào thêm 2026 khi 2026 mới có 2 quý).'
    )
    # Giữa năm (tháng >= 4): năm trước liền kề coi là đã đóng sổ
    assert _compute_target_year(_dt.date(2026, 8, 20)) == 2025
    # Đầu năm (tháng 1-3): báo cáo năm trước có thể CHƯA công bố xong → lùi thêm 1 năm
    assert _compute_target_year(_dt.date(2026, 2, 1)) == 2024
    print(f'✅ TARGET_YEAR={TARGET_YEAR} (đã đóng sổ) < IN_PROGRESS_YEAR={IN_PROGRESS_YEAR} '
          f'(đang chạy dở) — không còn lẫn 2 khái niệm vào 1 biến')

    # --- Test 5: _norm_label ---
    assert _norm_label('hoạt động') == 'hoat dong'
    assert _norm_label('Thu nhập lãi thuần') == 'thu nhap lai thuan'
    print('✅ _norm_label OK')

    # --- Test 6: CFO fallback quarterly ---
    df_cf_y = _make_df(
        ['Lưu chuyển tiền thuần từ hoạt động kinh doanh'],
        {Y0: [12327], Y1: [16414], Y2: [19422], Y3: [16710]},
    )
    df_cf_q = _make_df(
        ['Lưu chuyển tiền thuần từ hoạt động kinh doanh'],
        {f'{Y4}-Q1': [4200], f'{Y4}-Q2': [3800], f'{Y4}-Q3': [4100], f'{Y4}-Q4': [3900]},
    )
    cfo = _find_cfo_with_quarterly_fallback(df_cf_y, df_cf_q)
    assert Y4 in cfo.index, f'FAIL: CFO {Y4} vẫn thiếu'
    assert cfo[Y4] == 16000, f'FAIL CFO {Y4}: {cfo[Y4]}'
    print(f'✅ CFO fallback quarterly: {Y4}={cfo[Y4]:,.0f} tỷ')

    # --- Test 7: [FIX 16] nhãn cột năm linh hoạt (float / int / 'Q4/2025' / 'FY2025') ---
    for lbl in (f'{Y4}.0', float(Y4), int(Y4), f'FY{Y4}', f'Q4/{Y4}', f'{Y4}-Q4'):
        cols_ok = {Y0: [42273], Y1: [53246], Y2: [53615], Y3: [55406], lbl: [58771]}
        d = _make_df(['I. Thu nhập lãi thuần'], cols_ok)
        r = build_financial_table(d, pd.DataFrame(), ticker='VCB')['revenue']
        assert r.get(Y4) == 58771, f'FAIL nhãn cột {lbl!r}: {dict(r)}'
    print('✅ [FIX 16] cột năm mới dạng 2025.0 / 2025 / FY2025 / Q4/2025 đều ra đúng 2025')

    # --- Test 8: [FIX 16] DataFrame dạng DÀI (date + cột chỉ tiêu) ---
    df_long = pd.DataFrame({
        'date': [f'{Y0}-12-31', f'{Y1}-12-31', f'{Y2}-12-31', f'{Y3}-12-31', f'{Y4}-12-31'],
        'Thu nhập lãi thuần': [42273, 53246, 53615, 55406, 58771],
        'Doanh thu thuần': [1, 2, 3, 4, 69054],
    })
    r = build_financial_table(df_long, pd.DataFrame(), ticker='VCB')['revenue']
    assert r.get(Y4) == 58771 and r.get(Y0) == 42273, f'FAIL dạng dài: {dict(r)}'
    print('✅ [FIX 16] DataFrame dạng dài (date × chỉ tiêu) được chuyển đúng')

    # --- Test 9: [FIX 16] dạng dài theo QUÝ -> CFO cộng 4 quý ---
    df_cf_long_q = pd.DataFrame({
        'date': [f'{Y4}-03-31', f'{Y4}-06-30', f'{Y4}-09-30', f'{Y4}-12-31'],
        'Lưu chuyển tiền thuần từ hoạt động kinh doanh': [4200, 3800, 4100, 3900],
    })
    cfo2 = _find_cfo_with_quarterly_fallback(df_cf_y, df_cf_long_q)
    assert cfo2.get(Y4) == 16000, f'FAIL CFO dạng dài theo quý: {dict(cfo2)}'
    print('✅ [FIX 16] CFO từ dữ liệu quý dạng dài: 2025 = 16,000')

    # --- Test 10: [FIX 16] tên chỉ tiêu ở index, cột Item viết hoa ---
    d_idx = pd.DataFrame({Y3: [55406], f'{Y4}.0': [58771]}, index=['Thu nhập lãi thuần'])
    r = build_financial_table(d_idx, pd.DataFrame(), ticker='VCB')['revenue']
    assert r.get(Y4) == 58771, f'FAIL item ở index: {dict(r)}'
    d_up = _make_df(['Thu nhập lãi thuần'], {Y3: [55406], Y4: [58771]}).rename(columns={'item': 'Item'})
    r = build_financial_table(d_up, pd.DataFrame(), ticker='VCB')['revenue']
    assert r.get(Y4) == 58771, f'FAIL cột Item viết hoa: {dict(r)}'
    print('✅ [FIX 16] item ở index / cột "Item" viết hoa OK')

    print('\n🎉 Tất cả test pass!')
