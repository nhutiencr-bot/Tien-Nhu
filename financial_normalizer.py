"""
financial_normalizer.py — v2
Fix systematic cho 3 ngành: ngân hàng, chứng khoán, bảo hiểm.

Thay vì keyword fuzzy matching trên tên dòng (không reliable),
dùng 2 chiến lược chắc chắn hơn:

  CHIẾN LƯỢC A — Structural position:
    TOI = dòng ngay TRÊN dòng "Chi phí hoạt động" (operating expense)
    Vì trong BCTC ngân hàng VN, cấu trúc luôn là:
      ...các dòng thu nhập...
      TOI  ← dòng tổng, ngay trên chi phí
      Chi phí hoạt động
      Lợi nhuận thuần từ HĐKD
      ...

  CHIẾN LƯỢC B — Bottom-up aggregation:
    TOI = NII + tổng các dòng thu nhập ngoài lãi (non-interest income)
    Chắc chắn đúng vì tính từ các thành phần nhỏ lên.

  CHIẾN LƯỢC C — item_id whitelist (VCI source):
    Một số item_id VCI cố định, không đổi theo tên.

Thứ tự: A → B → C → NII fallback (báo warning)
"""

import pandas as pd
import re
import logging

logger = logging.getLogger(__name__)

# ── Sector classification ──────────────────────────────────────────────────────

BANK_TICKERS = {
    'VCB','BID','CTG','TCB','MBB','ACB','STB','VPB','HDB','TPB',
    'MSB','OCB','VIB','SHB','EIB','LPB','SSB','NAB','ABB','BAB',
    'BVB','KLB','PGB','VAB','NVB','SGB','CBB',
}

SECURITIES_TICKERS = {
    'SSI','VND','HCM','MBS','VCI','FTS','AGR','SBS','BSI','TVS',
    'BVS','CTS','VDS','WSS','APS','SHS','TCI','VIX','ORS',
}

INSURANCE_TICKERS = {
    'BVH','PVI','PTI','MIG','BMI','VNR','BIC','PRE','PGI','BLI',
}

def get_sector(ticker: str) -> str:
    t = ticker.upper()
    if t in BANK_TICKERS:       return 'bank'
    if t in SECURITIES_TICKERS: return 'securities'
    if t in INSURANCE_TICKERS:  return 'insurance'
    return 'normal'


# ── Column helpers ─────────────────────────────────────────────────────────────

def _norm_col_str(c) -> str:
    """'2025.0' → '2025', 2025 → '2025'"""
    s = str(c).strip()
    return s[:-2] if re.fullmatch(r'\d{4}\.0', s) else s

def _get_year_columns(df: pd.DataFrame) -> list:
    seen, cols = set(), []
    for c in df.columns:
        s = _norm_col_str(c)
        if re.fullmatch(r'\d{4}', s) and s not in seen:
            seen.add(s)
            cols.append(c)   # giữ tên gốc để đọc df
    return sorted(cols, key=lambda x: int(_norm_col_str(x)))

def _get_quarter_columns(df: pd.DataFrame) -> list:
    def _qkey(c):
        y, q = _norm_col_str(c).split('-Q')
        return (int(y), int(q))
    cols = [c for c in df.columns
            if re.fullmatch(r'\d{4}-Q[1-4]', _norm_col_str(c))]
    return sorted(cols, key=_qkey)

def _read_val(row, col) -> float | None:
    try:
        v = row[col]
        return float(v) if pd.notna(v) else None
    except Exception:
        return None

def _year_key(col, period: str):
    s = _norm_col_str(col)
    return s if period == 'quarter' else int(s)


# ── Text helpers ───────────────────────────────────────────────────────────────

def _text_of_row(df: pd.DataFrame, idx, text_cols: list) -> str:
    parts = []
    for c in text_cols:
        try:
            parts.append(str(df.loc[idx, c]))
        except Exception:
            pass
    return ' '.join(parts).lower().strip()

def _get_text_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns
            if any(k in str(c).lower() for k in ['item', 'name', 'chỉ tiêu'])
            and 'id' not in str(c).lower()]

def _get_id_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns
            if 'item_id' in str(c).lower() or str(c).lower() == 'item_id']


# ── find_row_series (giữ nguyên cho các chỉ tiêu không phải revenue) ──────────

def find_row_series(df: pd.DataFrame,
                    keywords: list,
                    exclude_keywords: list | None = None,
                    item_ids: list | None = None,
                    prefer_top_level: bool = True,
                    period: str = 'year') -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)

    year_cols = (_get_quarter_columns(df) if period == 'quarter'
                 else _get_year_columns(df))
    if not year_cols:
        return pd.Series(dtype=float)

    text_cols = _get_text_cols(df)
    id_cols   = _get_id_cols(df)
    matched   = pd.DataFrame()

    # Bước 1: item_id exact match
    if item_ids and id_cols:
        id_text = df[id_cols[0]].astype(str).str.lower().str.strip()
        mask = id_text.isin([i.lower().strip() for i in item_ids])
        if mask.any():
            matched = df[mask]

    # Bước 2: keyword search
    if matched.empty and text_cols:
        combined = df[text_cols].astype(str).agg(' '.join, axis=1).str.lower()
        mask = pd.Series(False, index=df.index)
        for kw in keywords:
            mask |= combined.str.contains(kw.lower(), regex=False, na=False)
        if exclude_keywords:
            for kw in exclude_keywords:
                mask &= ~combined.str.contains(kw.lower(), regex=False, na=False)
        matched = df[mask]

    if matched.empty:
        return pd.Series(dtype=float)

    # Chọn 1 dòng tốt nhất
    if len(matched) > 1 and prefer_top_level and 'levels' in matched.columns:
        lvl = pd.to_numeric(matched['levels'], errors='coerce')
        if lvl.notna().any():
            top = matched[lvl == lvl.min()]
            row = (top.loc[top[year_cols].notna().sum(axis=1).idxmax()]
                   if len(top) > 1 else top.iloc[0])
        else:
            row = matched.loc[matched[year_cols].notna().sum(axis=1).idxmax()]
    elif len(matched) > 1:
        row = matched.loc[matched[year_cols].notna().sum(axis=1).idxmax()]
    else:
        row = matched.iloc[0]

    result = {}
    for yc in year_cols:
        v = _read_val(row, yc)
        if v is not None:
            result[_year_key(yc, period)] = v

    if period == 'quarter':
        def _qsort(k):
            y, q = k.split('-Q')
            return (int(y), int(q))
        return pd.Series({k: result[k] for k in sorted(result, key=_qsort)})
    return pd.Series(result).sort_index()


# ══════════════════════════════════════════════════════════════════════════════
# CORE FIX: Revenue cho ngành tài chính
# 3 chiến lược, áp dụng tuần tự
# ══════════════════════════════════════════════════════════════════════════════

# Keyword nhận diện "Chi phí hoạt động" (anchor cố định trong BCTC)
_OPEX_KEYWORDS = [
    'chi phí hoạt động',
    'operating expense',
    'chi phí quản lý',
    'operating cost',
    'total operating expense',
    'tổng chi phí hoạt động',
]

# Keyword nhận diện "Lợi nhuận thuần từ HĐKD" (anchor thứ 2)
_OPINCOME_KEYWORDS = [
    'lợi nhuận thuần từ hoạt động kinh doanh',
    'lợi nhuận từ hoạt động kinh doanh trước',
    'profit from business activities before',
    'income from business operations',
    'lợi nhuận hoạt động',
]

# Dòng thành phần NII (Net Interest Income) — dòng ③
_NII_KEYWORDS = [
    'thu nhập lãi thuần',
    'net interest income',
    'lãi thuần từ hoạt động tín dụng',
]
_NII_ITEM_IDS = ['net_interest_income', 'net_interest_and_similar_income']

# Dòng Gross Interest Income — dòng ① (PHẢI LOẠI TRỪ)
_GROSS_INTEREST_EXCLUDES = [
    'và các khoản thu nhập tương tự',
    'and similar income',
    'interest and similar income',
    'thu nhập lãi và',        # "Thu nhập lãi VÀ các khoản..."
    'gross interest',
]

# Các dòng thu nhập ngoài lãi (non-interest income) — dùng cho chiến lược B
_NON_INTEREST_INCOME_KEYWORDS = [
    ('thu nhập thuần từ hoạt động dịch vụ',   ['chi phí']),
    ('thu nhập thuần từ kinh doanh ngoại hối', ['chi phí', 'lỗ']),
    ('thu nhập thuần từ mua bán chứng khoán',  ['chi phí', 'lỗ']),
    ('thu nhập thuần từ hoạt động khác',       ['chi phí']),
    ('lãi/lỗ thuần từ tài sản tài chính',      ['chi phí']),
    ('thu nhập từ góp vốn',                    ['chi phí']),
    ('thu nhập cổ tức',                        []),
    # CK/bảo hiểm
    ('doanh thu thuần về hoạt động kinh doanh chứng khoán', ['chi phí']),
    ('doanh thu phí bảo hiểm thuần',           ['chi phí', 'bồi thường']),
    ('thu nhập đầu tư tài chính',              ['chi phí']),
    ('thu nhập hoạt động kinh doanh bảo hiểm', ['chi phí']),
]

# item_id whitelist — VCI source thường có
_TOI_ITEM_IDS = [
    'net_operating_income',
    'total_operating_income',
    'operating_revenue',
    'net_revenue_banking',
    'total_net_revenue',
    'net_revenue',            # cho CK
    'total_operating_revenue',
    'gross_profit',           # bảo hiểm đôi khi dùng
]


def _strategy_A_structural(df_income: pd.DataFrame,
                            year_cols: list,
                            text_cols: list,
                            period: str) -> pd.Series:
    """
    Chiến lược A: TOI = dòng ngay TRÊN "Chi phí hoạt động".

    Cấu trúc BCTC ngân hàng VN (không đổi theo thông lệ kế toán):
        ...
        TOI                ← đây
        Chi phí hoạt động  ← anchor tìm được
        LNTT từ HĐKD
        ...

    Tìm index dòng "Chi phí hoạt động", lấy dòng trước đó.
    """
    if not text_cols:
        return pd.Series(dtype=float)

    combined = df_income[text_cols].astype(str).agg(' '.join, axis=1).str.lower()

    # Tìm dòng "Chi phí hoạt động"
    opex_mask = pd.Series(False, index=df_income.index)
    for kw in _OPEX_KEYWORDS:
        opex_mask |= combined.str.contains(kw, regex=False, na=False)

    if not opex_mask.any():
        return pd.Series(dtype=float)

    opex_positions = df_income.index[opex_mask].tolist()
    opex_pos = opex_positions[0]   # lấy lần xuất hiện đầu tiên

    # Lấy vị trí integer trong DataFrame
    opex_iloc = df_income.index.get_loc(opex_pos)
    if opex_iloc == 0:
        return pd.Series(dtype=float)

    # Dòng ngay trên = TOI
    toi_iloc = opex_iloc - 1
    toi_row  = df_income.iloc[toi_iloc]

    result = {}
    for yc in year_cols:
        v = _read_val(toi_row, yc)
        if v is not None and v > 0:
            result[_year_key(yc, period)] = v

    if not result:
        return pd.Series(dtype=float)

    s = pd.Series(result).sort_index()

    # ── Sanity check: TOI (Strategy A) phải >= NII ──────────────────
    # Nếu dòng tìm được nhỏ hơn NII → đã lấy nhầm dòng thành phần
    # (xảy ra khi không có dòng tổng, dòng cuối cùng trước chi phí = 1 dòng nhỏ)
    nii_series = _nii_fallback(df_income, year_cols, text_cols, period)
    if not nii_series.empty:
        common = s.index.intersection(nii_series.index)
        if len(common) > 0:
            # Nếu quá 50% các năm có TOI < NII → kết quả sai, bỏ qua
            toi_lt_nii = (s.loc[common] < nii_series.loc[common]).sum()
            if toi_lt_nii > len(common) * 0.5:
                logger.debug("Strategy A sanity FAIL: result < NII, skipping")
                return pd.Series(dtype=float)

    logger.debug(f"Strategy A (structural) found TOI: {s.to_dict()}")
    return s


def _strategy_B_aggregation(df_income: pd.DataFrame,
                             year_cols: list,
                             text_cols: list,
                             period: str) -> pd.Series:
    """
    Chiến lược B: TOI = NII + tổng các dòng Non-Interest Income.

    Tính bottom-up từ các thành phần nhỏ. Không phụ thuộc tên dòng tổng.
    Đảm bảo không bao giờ lấy Gross Interest Income.
    """
    if not text_cols:
        return pd.Series(dtype=float)

    combined = df_income[text_cols].astype(str).agg(' '.join, axis=1).str.lower()

    def _find_component(keywords, excludes=None):
        mask = pd.Series(False, index=df_income.index)
        for kw in keywords:
            mask |= combined.str.contains(kw, regex=False, na=False)
        # LUÔN exclude gross interest income
        for ex_kw in _GROSS_INTEREST_EXCLUDES:
            mask &= ~combined.str.contains(ex_kw, regex=False, na=False)
        if excludes:
            for ex in excludes:
                mask &= ~combined.str.contains(ex, regex=False, na=False)
        if not mask.any():
            return None
        # Ưu tiên dòng có nhiều năm có data nhất
        candidates = df_income[mask]
        best_idx = candidates[year_cols].notna().sum(axis=1).idxmax()
        return df_income.loc[best_idx]

    # Lấy NII (dòng ③) — phải exclude gross (dòng ①)
    nii_row = _find_component(
        _NII_KEYWORDS,
        excludes=_GROSS_INTEREST_EXCLUDES  # đã include trong hàm, double-safe
    )
    if nii_row is None:
        # Thử item_id
        id_cols = _get_id_cols(df_income)
        if id_cols:
            id_mask = df_income[id_cols[0]].astype(str).str.lower().isin(
                [x.lower() for x in _NII_ITEM_IDS])
            if id_mask.any():
                nii_row = df_income[id_mask].iloc[0]

    if nii_row is None:
        return pd.Series(dtype=float)

    # Tích lũy TOI = NII + non-interest income lines
    totals = {}
    for yc in year_cols:
        nii_val = _read_val(nii_row, yc)
        if nii_val is None:
            continue
        toi = nii_val

        # Cộng thêm từng dòng non-interest income
        for (kws_str, excl) in _NON_INTEREST_INCOME_KEYWORDS:
            comp_row = _find_component([kws_str], excludes=excl)
            if comp_row is not None:
                v = _read_val(comp_row, yc)
                if v is not None and v > 0:
                    toi += v

        if toi > 0:
            totals[_year_key(yc, period)] = toi

    if not totals:
        return pd.Series(dtype=float)

    s = pd.Series(totals).sort_index()
    logger.debug(f"Strategy B (aggregation) TOI: {s.to_dict()}")
    return s


def _strategy_C_item_id(df_income: pd.DataFrame,
                         year_cols: list,
                         period: str) -> pd.Series:
    """
    Chiến lược C: item_id whitelist (chỉ hoạt động tốt với VCI source).
    """
    id_cols = _get_id_cols(df_income)
    if not id_cols:
        return pd.Series(dtype=float)

    id_text = df_income[id_cols[0]].astype(str).str.lower().str.strip()
    mask = id_text.isin([x.lower() for x in _TOI_ITEM_IDS])
    if not mask.any():
        return pd.Series(dtype=float)

    row = df_income[mask].iloc[0]
    result = {}
    for yc in year_cols:
        v = _read_val(row, yc)
        if v is not None and v > 0:
            result[_year_key(yc, period)] = v

    return pd.Series(result).sort_index() if result else pd.Series(dtype=float)


def _nii_fallback(df_income: pd.DataFrame,
                  year_cols: list,
                  text_cols: list,
                  period: str) -> pd.Series:
    """
    Last resort: NII (dòng ③), với exclude cẩn thận để không lấy gross (dòng ①).
    Sẽ undercount ~15-25% nhưng tốt hơn gross hoặc wrong row.
    """
    if not text_cols:
        return pd.Series(dtype=float)

    combined = df_income[text_cols].astype(str).agg(' '.join, axis=1).str.lower()

    mask = pd.Series(False, index=df_income.index)
    for kw in _NII_KEYWORDS:
        mask |= combined.str.contains(kw, regex=False, na=False)

    # Loại trừ gross interest income (dòng ①) — đây là fix chính cho HDB
    for ex_kw in _GROSS_INTEREST_EXCLUDES:
        mask &= ~combined.str.contains(ex_kw, regex=False, na=False)

    if not mask.any():
        return pd.Series(dtype=float)

    row = df_income[mask].iloc[0]
    result = {}
    for yc in year_cols:
        v = _read_val(row, yc)
        if v is not None:
            result[_year_key(yc, period)] = v

    logger.warning("Using NII fallback — may undercount TOI by ~15-25%")
    return pd.Series(result).sort_index() if result else pd.Series(dtype=float)


def _find_revenue_financial_sector(df_income: pd.DataFrame,
                                   period: str = 'year') -> pd.Series:
    """
    Entry point cho ngân hàng, CK, bảo hiểm.
    Chạy 3 chiến lược theo thứ tự, lấy kết quả đầu tiên hợp lệ.
    """
    if df_income is None or df_income.empty:
        return pd.Series(dtype=float)

    year_cols  = (_get_quarter_columns(df_income) if period == 'quarter'
                  else _get_year_columns(df_income))
    text_cols  = _get_text_cols(df_income)

    if not year_cols:
        return pd.Series(dtype=float)

    # ── A: Structural (dòng ngay trên chi phí hoạt động) ──────────────
    s = _strategy_A_structural(df_income, year_cols, text_cols, period)
    if not s.empty:
        return s

    # ── B: Bottom-up aggregation (NII + non-interest lines) ────────────
    s = _strategy_B_aggregation(df_income, year_cols, text_cols, period)
    if not s.empty:
        return s

    # ── C: item_id whitelist ────────────────────────────────────────────
    s = _strategy_C_item_id(df_income, year_cols, period)
    if not s.empty:
        return s

    # ── Fallback: NII với exclude gross ────────────────────────────────
    return _nii_fallback(df_income, year_cols, text_cols, period)


# ══════════════════════════════════════════════════════════════════════════════
# Public API (giữ signature cũ để không break pipeline.py)
# ══════════════════════════════════════════════════════════════════════════════

def build_financial_table(df_income, df_balance, df_ratio=None,
                          ticker=None, period='year') -> dict:
    sector = get_sector(ticker) if ticker else 'normal'
    is_financial = sector in ('bank', 'securities', 'insurance')

    data = {}

    # ── Revenue ─────────────────────────────────────────────────────────
    if is_financial:
        data['revenue'] = _find_revenue_financial_sector(df_income, period)
    else:
        data['revenue'] = find_row_series(
            df_income,
            ['doanh thu thuần', 'net revenue', 'net sales',
             'doanh thu bán hàng và cung cấp dịch vụ'],
            exclude_keywords=['giá vốn', 'cost of', 'chi phí lãi'],
            item_ids=['revenue', 'net_revenue', 'net_sales'],
            period=period)
        if data['revenue'].empty:
            # Fallback: doanh nghiệp có cấu trúc tương tự tài chính
            data['revenue'] = _find_revenue_financial_sector(df_income, period)

    # ── Net profit ───────────────────────────────────────────────────────
    data['net_profit'] = find_row_series(
        df_income,
        ['lợi nhuận sau thuế', 'net profit after tax', 'profit after tax',
         'net income', 'lãi sau thuế'],
        exclude_keywords=['trước thuế', 'before tax', 'thiểu số', 'minority'],
        item_ids=['net_profit', 'net_profit_after_tax', 'profit_after_tax'],
        period=period)

    data['eps_income_stmt'] = find_row_series(
        df_income, ['lãi cơ bản trên cổ phiếu', 'earnings per share', 'eps'],
        item_ids=['eps'], period=period)

    # ── Balance sheet ────────────────────────────────────────────────────
    if df_balance is not None and not df_balance.empty:
        data['equity'] = find_row_series(
            df_balance,
            ['vốn chủ sở hữu', "owner's equity", 'total equity'],
            exclude_keywords=['vốn điều lệ', 'charter', 'cổ phần ưu đãi'],
            period=period)
        data['total_assets'] = find_row_series(
            df_balance,
            ['tổng cộng tài sản', 'total assets', 'tổng tài sản'],
            period=period)
    else:
        data['equity'] = data['total_assets'] = pd.Series(dtype=float)

    # ── Ratio ────────────────────────────────────────────────────────────
    ratio_keys = {
        'eps':               ['eps', 'earning per share'],
        'bvps':              ['book value per share', 'bvps'],
        'roe':               ['roe'],
        'roa':               ['roa'],
        'pe':                ['p/e', 'pe ratio', ' pe '],
        'pb':                ['p/b', 'pb ratio', ' pb '],
        'market_cap':        ['market cap', 'vốn hóa'],
        'outstanding_shares':['outstanding shares', 'số cổ phiếu lưu hành'],
        'net_margin':        ['net margin', 'biên lợi nhuận sau thuế'],
        'asset_turnover':    ['asset turnover', 'vòng quay tổng tài sản'],
        'dps':               ['dividend per share', 'cổ tức tiền mặt', 'dps'],
        'ev_ebitda':         ['ev/ebitda'],
        'p_cf':              ['price to cash flow', 'p/cf'],
        'ps':                ['p/s', 'price to sales'],
    }

    if df_ratio is not None and not df_ratio.empty:
        for key, kws in ratio_keys.items():
            data[key] = find_row_series(df_ratio, kws, period=period)
    else:
        for key in ratio_keys:
            data[key] = pd.Series(dtype=float)

    # EPS fallback từ income statement
    if data.get('eps', pd.Series(dtype=float)).empty:
        data['eps'] = data.get('eps_income_stmt', pd.Series(dtype=float))

    # BVPS fallback tính từ equity / shares
    if (data.get('bvps', pd.Series(dtype=float)).empty
            and not data['equity'].empty
            and not data.get('outstanding_shares', pd.Series(dtype=float)).empty):
        common = data['equity'].index.intersection(data['outstanding_shares'].index)
        if len(common):
            data['bvps'] = (data['equity'].loc[common]
                            / data['outstanding_shares'].loc[common])

    return data


def build_5y_financial_table(df_income, df_balance, df_ratio=None,
                              ticker=None) -> dict:
    """Backward-compatible wrapper."""
    return build_financial_table(df_income, df_balance, df_ratio,
                                 ticker=ticker, period='year')


# ── Utility functions (giữ nguyên) ────────────────────────────────────────────

def normalize_to_billion_vnd(series: pd.Series, label="") -> pd.Series:
    if series is None or series.empty:
        return series
    median_abs = series.abs().median()
    if median_abs > 1e11:    # đơn vị đồng
        return series / 1e9
    if median_abs > 1e5:     # đơn vị triệu
        return series / 1e3
    return series            # đã là tỷ

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
    start, end = float(s.iloc[0]), float(s.iloc[-1])
    if start <= 0:
        return None
    periods = n_years if n_years else (len(s) - 1)
    if periods <= 0:
        return None
    try:
        return (end / start) ** (1 / periods) - 1
    except Exception:
        return None
