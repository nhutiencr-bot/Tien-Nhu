"""
financial_normalizer.py — v3.2
Fix systematic cho 3 ngành: ngân hàng, chứng khoán, bảo hiểm.

=== THAY ĐỔI SO VỚI v3.1 ===

Root cause thực sự của lỗi "5 năm đều sai cùng 1 pattern" (TCB và nhiều
ngân hàng khác):

  Strategy A tìm dòng ngay TRÊN "Chi phí hoạt động" — nhưng với nhiều
  ngân hàng VCI, cấu trúc bảng IS là:
    ...
    Thu nhập lãi thuần                    ← đây, không phải TOI
    Chi phí hoạt động                     ← anchor
    ...
  Tức là dòng ngay trên opex là NII, không phải TOI tổng.
  Kết quả: revenue = NII thay vì TOI → sai toàn bộ 5 năm.

  Bổ sung Strategy D (TOI direct name match) chạy TRƯỚC Strategy A:
  Tìm trực tiếp dòng có tên "Tổng thu nhập hoạt động" / "Total operating
  income" / "Thu nhập hoạt động thuần" bằng keyword + item_id whitelist.
  Đây là cách đáng tin cậy nhất và nên là ưu tiên đầu tiên.

  Sửa Strategy A: trước khi accept candidate, kiểm tra text của dòng đó
  — nếu text match NII keywords → bỏ qua (đang pick nhầm NII).

  Thứ tự strategies: D → A → B → C → NII fallback
  (D mới, A được guard thêm, B/C/NII fallback giữ nguyên v3.1)
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
    s = str(c).strip()
    return s[:-2] if re.fullmatch(r'\d{4}\.0', s) else s

def _get_year_columns(df: pd.DataFrame) -> list:
    seen, cols = set(), []
    for c in df.columns:
        s = _norm_col_str(c)
        if re.fullmatch(r'\d{4}', s) and s not in seen:
            seen.add(s)
            cols.append(c)
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

    if item_ids and id_cols:
        id_text = df[id_cols[0]].astype(str).str.lower().str.strip()
        mask = id_text.isin([i.lower().strip() for i in item_ids])
        if mask.any():
            matched = df[mask]

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
# CORE: Revenue cho ngành tài chính
# ══════════════════════════════════════════════════════════════════════════════

_OPEX_KEYWORDS = [
    'chi phí hoạt động',
    'operating expense',
    'chi phí quản lý',
    'operating cost',
    'total operating expense',
    'tổng chi phí hoạt động',
]

_NII_KEYWORDS = [
    'thu nhập lãi thuần',
    'net interest income',
    'lãi thuần từ hoạt động tín dụng',
]
_NII_ITEM_IDS = ['net_interest_income', 'net_interest_and_similar_income']

_GROSS_INTEREST_EXCLUDES = [
    'và các khoản thu nhập tương tự',
    'and similar income',
    'interest and similar income',
    'thu nhập lãi và',
    'gross interest',
]

_NON_INTEREST_INCOME_KEYWORDS = [
    ('thu nhập thuần từ hoạt động dịch vụ',   ['chi phí']),
    ('thu nhập thuần từ kinh doanh ngoại hối', ['chi phí', 'lỗ']),
    ('thu nhập thuần từ mua bán chứng khoán',  ['chi phí', 'lỗ']),
    ('thu nhập thuần từ hoạt động khác',       ['chi phí']),
    ('lãi/lỗ thuần từ tài sản tài chính',      ['chi phí']),
    ('thu nhập từ góp vốn',                    ['chi phí']),
    ('thu nhập cổ tức',                        []),
    ('doanh thu thuần về hoạt động kinh doanh chứng khoán', ['chi phí']),
    ('doanh thu phí bảo hiểm thuần',           ['chi phí', 'bồi thường']),
    ('thu nhập đầu tư tài chính',              ['chi phí']),
    ('thu nhập hoạt động kinh doanh bảo hiểm', ['chi phí']),
]

# ── Strategy D: TOI direct name keywords (MỚI v3.2) ──────────────────────────
# Các tên dòng TOI thực tế xuất hiện trong BCTC ngân hàng/CK/bảo hiểm VN
_TOI_DIRECT_KEYWORDS = [
    'tổng thu nhập hoạt động',
    'total operating income',
    'thu nhập hoạt động thuần',
    'tổng thu nhập thuần',
    'total net income',
    'net operating income',
    'thu nhập thuần từ hoạt động',
    'tổng thu nhập từ hoạt động kinh doanh',
    'tổng doanh thu hoạt động',
    'tổng thu nhập',           # fallback rộng hơn, dùng cuối
]

# item_id whitelist cho TOI — VCI source
_TOI_ITEM_IDS = [
    'net_operating_income',
    'total_operating_income',
    'operating_revenue',
    'net_revenue_banking',
    'total_net_revenue',
    'net_revenue',
    'total_operating_revenue',
    'gross_profit',
]

# Keywords loại trừ cho Strategy D — tránh pick nhầm sub-items
_TOI_DIRECT_EXCLUDES = [
    'chi phí',           # tránh dòng chi phí
    'lợi nhuận',         # tránh dòng profit (sau khi trừ opex)
    'profit before',
    'profit after',
    'trước thuế',
    'after tax',
    'lãi trước',
    'dự phòng',          # tránh dòng trước dự phòng rủi ro tín dụng
    'provision',
]


def _strategy_D_direct_toi(df_income: pd.DataFrame,
                            year_cols: list,
                            text_cols: list,
                            period: str) -> pd.Series:
    """
    Chiến lược D (MỚI v3.2): Tìm trực tiếp dòng TOI bằng tên dòng.

    Đây là strategy ưu tiên nhất vì:
    - Không phụ thuộc vào vị trí tương đối (dễ sai khi cấu trúc bảng thay đổi)
    - Không phụ thuộc vào anchor "Chi phí hoạt động"
    - Khớp trực tiếp với tên dòng trong BCTC chính thức

    Ưu tiên: item_id match → keyword match chính xác → keyword match rộng.
    Với mỗi match group: chọn dòng có nhiều năm data nhất và value lớn nhất
    trong nhóm (TOI là dòng tổng, phải >= các sub-item).

    Guard: loại bỏ dòng có text match NII keywords (tránh pick nhầm NII
    khi tên dòng NII của một số ngân hàng khá giống TOI).
    """
    if not text_cols and not _get_id_cols(df_income):
        return pd.Series(dtype=float)

    id_cols = _get_id_cols(df_income)

    # ── Bước 1: thử item_id whitelist trước ──
    if id_cols:
        id_text = df_income[id_cols[0]].astype(str).str.lower().str.strip()
        id_mask = id_text.isin([x.lower() for x in _TOI_ITEM_IDS])
        if id_mask.any():
            candidates = df_income[id_mask]
            # Chọn dòng có nhiều năm data nhất
            best_idx = candidates[year_cols].notna().sum(axis=1).idxmax()
            row = candidates.loc[best_idx]
            result = {}
            for yc in year_cols:
                v = _read_val(row, yc)
                if v is not None and v > 0:
                    result[_year_key(yc, period)] = v
            if result:
                s = pd.Series(result).sort_index()
                logger.debug(f"Strategy D (item_id) TOI: {s.to_dict()}")
                return s

    if not text_cols:
        return pd.Series(dtype=float)

    combined = df_income[text_cols].astype(str).agg(' '.join, axis=1).str.lower()

    # Pre-compute NII mask để loại trừ
    nii_mask = pd.Series(False, index=df_income.index)
    for kw in _NII_KEYWORDS:
        nii_mask |= combined.str.contains(kw, regex=False, na=False)

    # ── Bước 2: keyword match — thử từng keyword theo thứ tự ưu tiên ──
    # Dừng ngay khi tìm được match hợp lệ (không phải NII, không phải exclude)
    for kw in _TOI_DIRECT_KEYWORDS:
        mask = combined.str.contains(kw, regex=False, na=False)

        # Loại trừ các dòng có từ khóa không mong muốn
        for ex_kw in _TOI_DIRECT_EXCLUDES:
            mask &= ~combined.str.contains(ex_kw, regex=False, na=False)

        # Loại trừ NII rows
        mask &= ~nii_mask

        if not mask.any():
            continue

        candidates = df_income[mask]

        # Chọn dòng tốt nhất trong candidates:
        # ưu tiên dòng có nhiều năm data nhất, tie-break bằng sum giá trị lớn hơn
        if len(candidates) > 1:
            data_count = candidates[year_cols].notna().sum(axis=1)
            max_count = data_count.max()
            top_candidates = candidates[data_count == max_count]

            if len(top_candidates) > 1:
                # Tie-break: chọn dòng có sum value lớn hơn (TOI = tổng)
                def _row_sum(row):
                    vals = [_read_val(row, yc) for yc in year_cols]
                    return sum(v for v in vals if v is not None and v > 0)
                sums = top_candidates.apply(_row_sum, axis=1)
                best_idx = sums.idxmax()
            else:
                best_idx = top_candidates.index[0]

            row = candidates.loc[best_idx]
        else:
            row = candidates.iloc[0]

        result = {}
        for yc in year_cols:
            v = _read_val(row, yc)
            if v is not None and v > 0:
                result[_year_key(yc, period)] = v

        if result:
            s = pd.Series(result).sort_index()
            logger.debug(f"Strategy D (keyword='{kw}') TOI: {s.to_dict()}")
            return s

    return pd.Series(dtype=float)


def _nii_fallback(df_income: pd.DataFrame,
                  year_cols: list,
                  text_cols: list,
                  period: str) -> pd.Series:
    """
    NII fallback — chỉ dùng khi tất cả strategies trên thất bại.
    v3.1: chọn dòng có median nhỏ nhất (NII < gross interest).
    """
    if not text_cols:
        return pd.Series(dtype=float)

    combined = df_income[text_cols].astype(str).agg(' '.join, axis=1).str.lower()

    mask = pd.Series(False, index=df_income.index)
    for kw in _NII_KEYWORDS:
        mask |= combined.str.contains(kw, regex=False, na=False)

    for ex_kw in _GROSS_INTEREST_EXCLUDES:
        mask &= ~combined.str.contains(ex_kw, regex=False, na=False)

    if not mask.any():
        return pd.Series(dtype=float)

    candidates = df_income[mask]

    if len(candidates) > 1:
        def _row_median(row):
            vals = [_read_val(row, yc) for yc in year_cols]
            vals = [v for v in vals if v is not None and v > 0]
            return pd.Series(vals).median() if vals else float('inf')
        medians = candidates.apply(_row_median, axis=1)
        row = candidates.loc[medians.idxmin()]
    else:
        row = candidates.iloc[0]

    result = {}
    for yc in year_cols:
        v = _read_val(row, yc)
        if v is not None:
            result[_year_key(yc, period)] = v

    if result:
        logger.warning("Using NII fallback — may undercount TOI by ~15-25%")
    return pd.Series(result).sort_index() if result else pd.Series(dtype=float)


def _strategy_A_structural(df_income: pd.DataFrame,
                            year_cols: list,
                            text_cols: list,
                            period: str) -> pd.Series:
    """
    Chiến lược A: TOI = dòng ngay TRÊN "Chi phí hoạt động".

    v3.2 FIX thêm: trước khi accept candidate row, kiểm tra text của
    dòng đó — nếu match NII keywords → bỏ qua (đang pick nhầm NII).
    Đây là root cause lỗi TCB 5 năm đều sai: bảng TCB có cấu trúc
    [NII] → [Chi phí hoạt động], Strategy A pick NII thay vì TOI.

    Giữ nguyên các fix v3.1: ưu tiên vị trí (min iloc có NII), min value (không có NII).
    """
    if not text_cols:
        return pd.Series(dtype=float)

    combined = df_income[text_cols].astype(str).agg(' '.join, axis=1).str.lower()

    opex_mask = pd.Series(False, index=df_income.index)
    for kw in _OPEX_KEYWORDS:
        opex_mask |= combined.str.contains(kw, regex=False, na=False)

    if not opex_mask.any():
        return pd.Series(dtype=float)

    opex_positions = df_income.index[opex_mask].tolist()

    candidate_rows_with_iloc = []
    for opex_pos in opex_positions:
        opex_iloc = df_income.index.get_loc(opex_pos)
        if opex_iloc == 0:
            continue
        toi_iloc = opex_iloc - 1
        cand_row = df_income.iloc[toi_iloc]

        # ── v3.2 GUARD: bỏ qua nếu candidate row là NII ──────────────
        cand_text = combined.iloc[toi_iloc]
        is_nii = any(kw in cand_text for kw in _NII_KEYWORDS)
        if is_nii:
            logger.debug(
                f"Strategy A: skipping candidate at iloc={toi_iloc} "
                f"— identified as NII row (text: '{cand_text[:80]}')"
            )
            # Thử lùi thêm 1-3 dòng để tìm TOI thật
            for step_back in range(2, 5):
                alt_iloc = opex_iloc - step_back
                if alt_iloc < 0:
                    break
                alt_text = combined.iloc[alt_iloc]
                alt_is_nii = any(kw in alt_text for kw in _NII_KEYWORDS)
                alt_is_opex = any(kw in alt_text for kw in _OPEX_KEYWORDS)
                if not alt_is_nii and not alt_is_opex:
                    candidate_rows_with_iloc.append((alt_iloc, df_income.iloc[alt_iloc]))
                    logger.debug(f"Strategy A: stepped back {step_back} rows → iloc={alt_iloc}")
                    break
            continue

        candidate_rows_with_iloc.append((toi_iloc, cand_row))

    if not candidate_rows_with_iloc:
        return pd.Series(dtype=float)

    candidate_rows_with_iloc.sort(key=lambda x: x[0])

    nii_series = _nii_fallback(df_income, year_cols, text_cols, period)

    result = {}
    for yc in year_cols:
        yk = _year_key(yc, period)
        has_nii = (yk in nii_series.index and pd.notna(nii_series.get(yk))
                   and nii_series[yk] > 0)
        nii_val = nii_series[yk] if has_nii else None

        valid_candidates = []
        for (cand_iloc, cand_row) in candidate_rows_with_iloc:
            v = _read_val(cand_row, yc)
            if v is None or v <= 0:
                continue
            if has_nii and v < nii_val:
                continue
            valid_candidates.append((cand_iloc, v))

        if not valid_candidates:
            continue

        if has_nii:
            best_iloc, best_val = min(valid_candidates, key=lambda x: x[0])
        else:
            _, best_val = min(valid_candidates, key=lambda x: x[1])

        result[yk] = best_val

    if not result:
        return pd.Series(dtype=float)

    s = pd.Series(result).sort_index()
    logger.debug(f"Strategy A (structural v3.2) TOI: {s.to_dict()}")
    return s


def _strategy_B_aggregation(df_income: pd.DataFrame,
                             year_cols: list,
                             text_cols: list,
                             period: str) -> pd.Series:
    """
    Chiến lược B: TOI = NII + tổng các dòng Non-Interest Income.
    Giữ nguyên v3.1 (fix Bug 2: lấy dòng cuối cùng match thay vì
    dòng có nhiều năm nhất).
    """
    if not text_cols:
        return pd.Series(dtype=float)

    combined = df_income[text_cols].astype(str).agg(' '.join, axis=1).str.lower()

    def _find_component(keywords, excludes=None):
        mask = pd.Series(False, index=df_income.index)
        for kw in keywords:
            mask |= combined.str.contains(kw, regex=False, na=False)
        for ex_kw in _GROSS_INTEREST_EXCLUDES:
            mask &= ~combined.str.contains(ex_kw, regex=False, na=False)
        if excludes:
            for ex in excludes:
                mask &= ~combined.str.contains(ex, regex=False, na=False)
        if not mask.any():
            return None
        candidates = df_income[mask]
        if len(candidates) == 1:
            return candidates.iloc[0]

        # Thử dòng cuối cùng trước (v3.1)
        last_row = candidates.iloc[-1]
        last_count = sum(1 for yc in year_cols
                         if _read_val(last_row, yc) is not None)
        if last_count >= max(1, len(year_cols) // 2):
            return last_row

        best_idx = candidates[year_cols].notna().sum(axis=1).idxmax()
        return df_income.loc[best_idx]

    nii_row = _find_component(_NII_KEYWORDS, excludes=_GROSS_INTEREST_EXCLUDES)
    if nii_row is None:
        id_cols = _get_id_cols(df_income)
        if id_cols:
            id_mask = df_income[id_cols[0]].astype(str).str.lower().isin(
                [x.lower() for x in _NII_ITEM_IDS])
            if id_mask.any():
                nii_row = df_income[id_mask].iloc[0]

    if nii_row is None:
        return pd.Series(dtype=float)

    totals = {}
    for yc in year_cols:
        nii_val = _read_val(nii_row, yc)
        if nii_val is None:
            continue
        toi = nii_val
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
    logger.debug(f"Strategy B (aggregation v3.2) TOI: {s.to_dict()}")
    return s


def _strategy_C_item_id(df_income: pd.DataFrame,
                         year_cols: list,
                         period: str) -> pd.Series:
    """
    Chiến lược C: item_id whitelist (VCI source).
    Giữ nguyên v3.
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


def _find_revenue_financial_sector(df_income: pd.DataFrame,
                                   period: str = 'year') -> pd.Series:
    """
    Entry point cho ngân hàng, CK, bảo hiểm.

    Thứ tự strategies v3.2:
      D (direct TOI name) → A (structural, đã guard NII) →
      B (aggregation) → C (item_id) → NII fallback

    Merge theo từng năm: strategy ưu tiên cao hơn win, không ghi đè.
    """
    if df_income is None or df_income.empty:
        return pd.Series(dtype=float)

    year_cols = (_get_quarter_columns(df_income) if period == 'quarter'
                 else _get_year_columns(df_income))
    text_cols = _get_text_cols(df_income)

    if not year_cols:
        return pd.Series(dtype=float)

    all_year_keys = [_year_key(yc, period) for yc in year_cols]

    strategies = [
        ('D (direct TOI)',  lambda: _strategy_D_direct_toi(df_income, year_cols, text_cols, period)),
        ('A (structural)',  lambda: _strategy_A_structural(df_income, year_cols, text_cols, period)),
        ('B (aggregation)', lambda: _strategy_B_aggregation(df_income, year_cols, text_cols, period)),
        ('C (item_id)',     lambda: _strategy_C_item_id(df_income, year_cols, period)),
        ('NII fallback',    lambda: _nii_fallback(df_income, year_cols, text_cols, period)),
    ]

    merged: dict = {}
    for name, fn in strategies:
        if set(all_year_keys) <= set(merged.keys()):
            break
        s = fn()
        if s.empty:
            continue
        filled_this_round = []
        for yk in s.index:
            if yk not in merged:
                merged[yk] = s[yk]
                filled_this_round.append(yk)
        if filled_this_round:
            logger.debug(f"Strategy {name} filled years: {filled_this_round}")

    if not merged:
        return pd.Series(dtype=float)

    return pd.Series(merged).sort_index()


# ══════════════════════════════════════════════════════════════════════════════
# Public API
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

    if data.get('eps', pd.Series(dtype=float)).empty:
        data['eps'] = data.get('eps_income_stmt', pd.Series(dtype=float))

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


# ── Utility functions ──────────────────────────────────────────────────────────

def normalize_to_billion_vnd(series: pd.Series, label="") -> pd.Series:
    if series is None or series.empty:
        return series
    median_abs = series.abs().median()
    if median_abs > 1e11:
        return series / 1e9
    if median_abs > 1e5:
        return series / 1e3
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
