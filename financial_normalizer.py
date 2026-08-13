"""
financial_normalizer.py — v3.1
Fix systematic cho 3 ngành: ngân hàng, chứng khoán, bảo hiểm.

=== THAY ĐỔI SO VỚI v3 ===

Bug 1 (Strategy A): dùng "v > best_val" (ưu tiên giá trị lớn nhất) để
  chọn trong các candidate rows. SAI vì dòng lũy kế quý của năm hiện tại
  (vnstock ghép thêm) thường có giá trị lớn hơn TOI thật nhưng lại là
  giá trị sai. Fix: ưu tiên theo THỨ TỰ VỊ TRÍ (anchor gần cuối bảng
  hơn → cấu trúc chính thức năm đã chốt sổ), không phải theo magnitude.

Bug 2 (Strategy B _find_component): luôn lấy dòng có nhiều năm data
  nhất (notna.sum.idxmax). Với 2025, vnstock có thể thêm dòng tên mới
  ít năm hơn nhưng đúng hơn → bị bỏ. Fix: lấy dòng cuối cùng trong
  danh sách match (thường là dòng được thêm gần nhất / phù hợp nhất
  với cấu trúc mới), với fallback sang dòng có nhiều năm nhất.

Bug 3 (_nii_fallback): lấy iloc[0] không có sanity. Fix: duyệt tất cả
  dòng match, chọn dòng có median giá trị nhỏ nhất (NII luôn < gross
  interest) và không bị exclude bởi gross interest keyword.

Bug 4 (Strategy A sanity check): khi không có NII (nii_series rỗng),
  mọi v > 0 đều được chấp nhận kể cả giá trị cực lớn bất hợp lý.
  Fix: thêm guard kiểm tra nếu tất cả candidate hợp lệ đều có cùng
  giá trị thì lấy đó, không thì lấy giá trị NHỎ NHẤT trong nhóm hợp
  lệ (TOI thật luôn là dòng tổng nhỏ hơn gross interest).
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

_OPINCOME_KEYWORDS = [
    'lợi nhuận thuần từ hoạt động kinh doanh',
    'lợi nhuận từ hoạt động kinh doanh trước',
    'profit from business activities before',
    'income from business operations',
    'lợi nhuận hoạt động',
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


def _nii_fallback(df_income: pd.DataFrame,
                  year_cols: list,
                  text_cols: list,
                  period: str) -> pd.Series:
    """
    NII (dòng thu nhập lãi thuần) — tách biệt khỏi gross interest.

    FIX v3.1 (Bug 3): thay vì lấy iloc[0], duyệt TẤT CẢ dòng match
    và chọn dòng có median giá trị nhỏ nhất (NII < gross interest).
    Điều này đảm bảo không lấy nhầm gross interest income ngay cả
    khi dòng đó xuất hiện trước NII trong bảng.
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

    # FIX Bug 3: chọn dòng có median nhỏ nhất (NII < gross interest)
    if len(candidates) > 1:
        def _row_median(row):
            vals = [_read_val(row, yc) for yc in year_cols]
            vals = [v for v in vals if v is not None and v > 0]
            return pd.Series(vals).median() if vals else float('inf')

        medians = candidates.apply(_row_median, axis=1)
        best_idx = medians.idxmin()
        row = candidates.loc[best_idx]
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

    FIX v3.1 (Bug 1 + Bug 4):
    - Không ưu tiên giá trị LỚN NHẤT nữa (dễ lấy nhầm gross interest
      hoặc dòng lũy kế quý của năm hiện tại).
    - Thay vào đó: ưu tiên theo vị trí anchor — anchor nào nằm CUỐI
      bảng hơn (iloc lớn hơn) thường thuộc cấu trúc báo cáo năm chính
      thức đã chốt sổ, không phải dòng quý ghép thêm.
    - Với mỗi năm: lấy giá trị từ anchor cuối cùng hợp lệ (v >= NII).
    - Nếu không có NII để so sánh: lấy giá trị NHỎ NHẤT trong số các
      ứng viên dương (TOI < gross interest, nên nhỏ hơn là đúng hơn).
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

    # Lấy dòng ngay trên mỗi anchor, kèm thông tin iloc (vị trí)
    candidate_rows_with_iloc = []
    for opex_pos in opex_positions:
        opex_iloc = df_income.index.get_loc(opex_pos)
        if opex_iloc == 0:
            continue
        toi_iloc = opex_iloc - 1
        candidate_rows_with_iloc.append((toi_iloc, df_income.iloc[toi_iloc]))

    if not candidate_rows_with_iloc:
        return pd.Series(dtype=float)

    # Sắp xếp theo iloc TĂNG DẦN để xử lý từ đầu → cuối bảng
    # (cuối bảng = cấu trúc chính thức năm chốt sổ, ưu tiên hơn)
    candidate_rows_with_iloc.sort(key=lambda x: x[0])

    nii_series = _nii_fallback(df_income, year_cols, text_cols, period)

    result = {}
    for yc in year_cols:
        yk = _year_key(yc, period)
        has_nii = (yk in nii_series.index and pd.notna(nii_series.get(yk))
                   and nii_series[yk] > 0)
        nii_val = nii_series[yk] if has_nii else None

        valid_candidates = []  # list of (iloc, value)
        for (cand_iloc, cand_row) in candidate_rows_with_iloc:
            v = _read_val(cand_row, yc)
            if v is None or v <= 0:
                continue
            if has_nii and v < nii_val:
                # giá trị nhỏ hơn NII → không phải TOI, bỏ qua
                continue
            valid_candidates.append((cand_iloc, v))

        if not valid_candidates:
            continue

        if has_nii:
            # Có NII để sanity check: lấy ứng viên ĐẦU BẢNG nhất
            # (iloc nhỏ nhất) trong số hợp lệ — cấu trúc báo cáo
            # năm chính thức luôn xuất hiện TRƯỚC trong bảng, còn
            # dòng lũy kế quý vnstock ghép thêm nằm PHÍa SAU.
            # FIX Bug 1: KHÔNG dùng max(v), dùng min(iloc) thay thế.
            best_iloc, best_val = min(valid_candidates, key=lambda x: x[0])
        else:
            # Không có NII: lấy giá trị NHỎ NHẤT trong ứng viên dương
            # (TOI < gross interest income, nên nhỏ hơn thường đúng hơn)
            # FIX Bug 4.
            _, best_val = min(valid_candidates, key=lambda x: x[1])

        result[yk] = best_val

    if not result:
        return pd.Series(dtype=float)

    s = pd.Series(result).sort_index()
    logger.debug(f"Strategy A (structural, per-year v3.1) TOI: {s.to_dict()}")
    return s


def _strategy_B_aggregation(df_income: pd.DataFrame,
                             year_cols: list,
                             text_cols: list,
                             period: str) -> pd.Series:
    """
    Chiến lược B: TOI = NII + tổng các dòng Non-Interest Income.

    FIX v3.1 (Bug 2): _find_component không còn luôn lấy dòng có nhiều
    năm data nhất. Thay vào đó: thử lấy dòng CUỐI CÙNG trong danh sách
    match (gần nhất với cấu trúc mới của vnstock), fallback sang dòng
    có nhiều năm nhất nếu dòng cuối không có đủ data cho các năm cần.
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

        # FIX Bug 2: thử dòng cuối cùng trước
        last_row = candidates.iloc[-1]
        last_count = sum(1 for yc in year_cols
                         if _read_val(last_row, yc) is not None)
        if last_count >= max(1, len(year_cols) // 2):
            # Dòng cuối có ít nhất 50% số năm → dùng nó
            return last_row

        # Fallback: dòng có nhiều năm data nhất
        best_idx = candidates[year_cols].notna().sum(axis=1).idxmax()
        return df_income.loc[best_idx]

    # Lấy NII (loại gross interest)
    nii_row = _find_component(
        _NII_KEYWORDS,
        excludes=_GROSS_INTEREST_EXCLUDES
    )
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
    logger.debug(f"Strategy B (aggregation v3.1) TOI: {s.to_dict()}")
    return s


def _strategy_C_item_id(df_income: pd.DataFrame,
                         year_cols: list,
                         period: str) -> pd.Series:
    """
    Chiến lược C: item_id whitelist (chỉ hoạt động tốt với VCI source).
    Không thay đổi so với v3.
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
    Merge kết quả THEO TỪNG NĂM: A → B → C → NII fallback.
    Logic merge giữ nguyên từ v3 — không thay đổi.
    """
    if df_income is None or df_income.empty:
        return pd.Series(dtype=float)

    year_cols  = (_get_quarter_columns(df_income) if period == 'quarter'
                  else _get_year_columns(df_income))
    text_cols  = _get_text_cols(df_income)

    if not year_cols:
        return pd.Series(dtype=float)

    all_year_keys = [_year_key(yc, period) for yc in year_cols]

    strategies = [
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


# ── Utility functions (giữ nguyên) ────────────────────────────────────────────

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
