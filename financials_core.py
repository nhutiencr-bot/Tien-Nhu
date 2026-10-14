"""
financials_core.py
------------------
MỘT luật duy nhất cho mọi năm 2021 → TARGET_YEAR. Không có tầng vá riêng cho năm nào.

Nguyên tắc
  1. Mỗi chỉ tiêu lấy từ MỘT dòng báo cáo của MỘT nguồn (vnstock KBS / DNSE / VCI) cho
     toàn bộ các năm. Không trộn cột giữa các nguồn, không vá năm cuối bằng nguồn khác.
  2. Khái niệm doanh thu theo NGÀNH (financial_normalizer._find_revenue_for_*):
       Ngân hàng = Thu nhập lãi thuần (NII) · CTCK = Doanh thu hoạt động ·
       Bảo hiểm = Doanh thu phí bảo hiểm · Còn lại = Doanh thu thuần.
  3. Giá trị mỗi năm tính BẰNG CÙNG MỘT HÀM:
       - chỉ tiêu dòng chảy (doanh thu, LNST, EPS): cột năm của dòng đó; nếu nguồn không có
         cột năm đó thì = tổng 4 quý CỦA CHÍNH DÒNG ĐÓ;
       - chỉ tiêu số dư (vốn CSH, tổng tài sản): cột năm; nếu thiếu = quý 4 của chính dòng đó.
     Thiếu cả hai -> để TRỐNG (không đoán, không mượn khái niệm khác).
  4. Chọn nguồn theo ĐỘ ĐỦ của cả bảng (không chọn theo từng năm).
  5. Đơn vị chuẩn hoá về tỷ VNĐ một lần cho cả chuỗi.

Module thuần pandas/numpy — không import streamlit/vnstock nên test được độc lập.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from financial_normalizer import build_financial_table
from quarterly_view import quarter_year_sums, _parse_q_key, _num

# ────────────────────────── chuẩn hoá đơn vị ──────────────────────────────


def normalize_to_billion_vnd(series):
    """
    Chuẩn hoá series về TỶ VNĐ. Đơn vị API: đồng (>5e11) / triệu (>5e5) / tỷ.
    Chọn theo trung vị (>=3 giá trị) hoặc max (1-2 giá trị), rồi kiểm từng giá trị.
    """
    if series is None or len(series) == 0:
        return series
    numeric = pd.to_numeric(series, errors='coerce').dropna()
    if numeric.empty:
        return series
    ref_val = numeric.abs().median() if len(numeric) >= 3 else numeric.abs().max()
    divisor = 1e9 if ref_val > 5e11 else (1e3 if ref_val > 5e5 else 1.0)

    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            v = float(val)
            a = abs(v)
            if a > 5e11:
                return round(v / 1e9, 2)
            if a > 5e5:
                return round(v / 1e3, 2)
            return round(v / divisor, 2) if divisor != 1.0 else round(v, 2)
        except Exception:
            return None

    return series.map(_to_ty).dropna()


def normalize_pct_series(s):
    """Chuẩn hoá ROE/ROA/ROS về đơn vị % hợp lý (0–200%)."""
    if s is None or len(s) == 0:
        return s
    valid = s.dropna()
    if valid.empty:
        return s
    max_abs = valid.abs().max()
    if max_abs > 500:
        s_fixed = s / 1000
        if s_fixed.dropna().abs().max() <= 200:
            return s_fixed
        return pd.Series([None] * len(s), index=s.index, dtype=float)
    if max_abs < 1:
        return s * 100
    return s


def normalize_net_profit_with_anchor(net_profit_raw, equity_series, roe_series):
    """Chuẩn hoá LNST về tỷ VNĐ; dùng vốn CSH × ROE làm mốc đối chiếu bậc 10^3."""
    base = normalize_to_billion_vnd(net_profit_raw)
    if base is None or len(base) == 0:
        return base
    roe_norm = normalize_pct_series(roe_series)
    if roe_norm is None or len(roe_norm) == 0 or roe_norm.dropna().empty:
        return base
    fixed = {}
    for year, raw_val in base.items():
        if (year not in equity_series.index or year not in roe_norm.index
                or pd.isna(equity_series.get(year)) or pd.isna(roe_norm.get(year))):
            fixed[year] = raw_val
            continue
        expected = equity_series[year] * roe_norm[year] / 100
        if expected == 0 or raw_val == 0:
            fixed[year] = raw_val
            continue
        ratio = raw_val / expected
        if ratio <= 0:
            fixed[year] = raw_val
            continue
        power = round(np.log10(ratio))
        fixed[year] = raw_val if power == 0 else round(raw_val / (10 ** power), 2)
    return pd.Series(fixed)


# ────────────────────────── luật tính từng năm ────────────────────────────

def _int_year(k):
    try:
        return int(str(k).strip().split('.')[0].split('-')[0])
    except ValueError:
        return None


def filter_years(s, years):
    """Giữ đúng các năm trong `years`, index về int."""
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    out = {}
    for k, v in s.items():
        y = _int_year(k)
        fv = _num(v)
        if y in years and fv is not None and y not in out:
            out[y] = fv
    return pd.Series(out, dtype=float).sort_index()


def _annual_map(annual) -> dict:
    return {} if annual is None or len(annual) == 0 else filter_years(annual, set(range(1900, 2200))).to_dict()


def resolve_flow(annual, quarterly, years, notes=None, label='') -> pd.Series:
    """
    Chỉ tiêu dòng chảy. Mỗi năm: cột năm; thiếu thì tổng 4 quý của CHÍNH dòng đó.
    Cùng một hàm cho mọi năm. Có cả hai mà lệch >5% -> chỉ GHI CHÚ, không thay số.
    """
    ann = _annual_map(annual)
    qsum = quarter_year_sums(quarterly)
    out = {}
    for y in sorted(years):
        a, q = ann.get(y), qsum.get(y)
        if a is not None:
            out[y] = a
            if notes is not None and q and abs(a - q) / abs(q) > 0.05:
                notes.append(f'{label}[{y}]: cột năm {a:,.2f} lệch {abs(a - q) / abs(q):.0%} '
                             f'so với tổng 4 quý {q:,.2f} — kiểm tra nguồn')
        elif q is not None:
            out[y] = q
    return pd.Series(out, dtype=float).sort_index()


def resolve_stock(annual, quarterly, years) -> pd.Series:
    """Chỉ tiêu số dư: cột năm; thiếu thì quý 4 của chính dòng đó."""
    ann = _annual_map(annual)
    q4 = {}
    if quarterly is not None and len(quarterly) > 0:
        for k, v in quarterly.items():
            pk = _parse_q_key(k)
            fv = _num(v)
            if pk and pk[1] == 4 and fv is not None:
                q4[pk[0]] = fv
    out = {}
    for y in sorted(years):
        if ann.get(y) is not None:
            out[y] = ann[y]
        elif q4.get(y) is not None:
            out[y] = q4[y]
    return pd.Series(out, dtype=float).sort_index()


# ────────────────────────── dựng lõi từ MỘT nguồn ─────────────────────────

CORE_FIELDS = ('revenue', 'net_profit', 'equity', 'total_assets')


def build_core(bundle: dict, ticker: str, years) -> dict:
    """
    bundle: {'income_y','balance_y','ratio_y','cashflow_y','income_q','balance_q','ratio_q'}
            — các DataFrame của CÙNG MỘT nguồn.
    Trả về dict series đã chuẩn hoá (tỷ VNĐ) + fin_y / fin_q thô + notes + score.
    """
    years = set(years)
    notes: list = []
    fin_y = build_financial_table(bundle.get('income_y'), bundle.get('balance_y'),
                                  bundle.get('ratio_y'), ticker=ticker, period='year')
    fin_q = build_financial_table(bundle.get('income_q'), bundle.get('balance_q'),
                                  bundle.get('ratio_q'), ticker=ticker, period='quarter')

    rev_y, rev_q = normalize_to_billion_vnd(fin_y['revenue']), normalize_to_billion_vnd(fin_q['revenue'])
    eq_y, eq_q = normalize_to_billion_vnd(fin_y['equity']), normalize_to_billion_vnd(fin_q['equity'])
    ta_y, ta_q = normalize_to_billion_vnd(fin_y['total_assets']), normalize_to_billion_vnd(fin_q['total_assets'])
    np_y = normalize_net_profit_with_anchor(fin_y['net_profit'], eq_y, fin_y['roe'])
    np_q = normalize_net_profit_with_anchor(fin_q['net_profit'], eq_q, fin_q['roe'])

    core = {
        'revenue':      resolve_flow(rev_y, rev_q, years, notes, 'revenue'),
        'net_profit':   resolve_flow(np_y, np_q, years, notes, 'net_profit'),
        'equity':       resolve_stock(eq_y, eq_q, years),
        'total_assets': resolve_stock(ta_y, ta_q, years),
        'eps':          resolve_flow(fin_y['eps'], fin_q['eps'], years),
        'fin_y': fin_y, 'fin_q': fin_q,
        'rev_q': rev_q, 'np_q': np_q, 'eq_q': eq_q, 'ta_q': ta_q,
        'notes': notes,
    }
    core['score'] = sum(int(core[f].notna().sum()) for f in CORE_FIELDS)
    return core


def pick_best_source(cores: dict, order, years) -> str | None:
    """Chọn MỘT nguồn cho cả bảng: điểm đủ-dữ-liệu cao nhất, hòa thì theo thứ tự ưu tiên."""
    best, best_score = None, -1
    for src in order:
        c = cores.get(src)
        if c is None:
            continue
        if c['score'] > best_score:
            best, best_score = src, c['score']
    return best


def is_complete(core: dict, years) -> bool:
    return core is not None and core['score'] >= len(set(years)) * len(CORE_FIELDS)
