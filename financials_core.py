"""
financials_core.py
------------------
MỘT luật duy nhất cho mọi năm trong TARGET_YEARS (2021 → 2025). Không có tầng vá riêng
cho năm nào.

Nguyên tắc
  1. Mỗi chỉ tiêu lấy từ MỘT dòng báo cáo (khái niệm theo ngành, xem financial_normalizer):
       Ngân hàng = Thu nhập lãi thuần · CTCK = Doanh thu hoạt động ·
       Bảo hiểm = Doanh thu phí bảo hiểm · Còn lại = Doanh thu thuần.
  2. Với từng chỉ tiêu, nguồn được chọn là nguồn có ĐỦ NĂM NHẤT.
  3. Năm còn thiếu chỉ được bù từ nguồn khác nếu nguồn đó KHỚP với nguồn chính trên
     >= 2 năm trùng (lệch <= 3%). Điều kiện này kiểm cả định nghĩa lẫn đơn vị, và áp
     dụng giống hệt cho mọi năm. Không khớp / không đủ năm trùng -> để TRỐNG.
  4. Đơn vị chuẩn hoá về tỷ VNĐ một lần cho cả chuỗi.

Module thuần pandas/numpy — không import streamlit/vnstock nên test được độc lập.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from financial_normalizer import build_financial_table

FIELDS = ('revenue', 'net_profit', 'equity', 'total_assets', 'eps')
SCORE_FIELDS = ('revenue', 'net_profit', 'equity', 'total_assets')
OVERLAP_MIN = 2          # số năm trùng tối thiểu để tin một nguồn bù
OVERLAP_TOL = 0.03       # lệch tối đa trên năm trùng


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


# ────────────────────────── tiện ích ──────────────────────────────────────

def _int_year(k):
    try:
        return int(str(k).strip().split('.')[0].split('-')[0])
    except ValueError:
        return None


def filter_years(s, years):
    """Giữ đúng các năm trong `years`, index về int, bỏ NaN."""
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    years = set(years)
    out = {}
    for k, v in s.items():
        y = _int_year(k)
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if y in years and not np.isnan(fv) and y not in out:
            out[y] = fv
    return pd.Series(out, dtype=float).sort_index()


# ────────────────────────── dựng lõi từ MỘT nguồn ─────────────────────────

def build_core(bundle: dict, ticker: str, years) -> dict:
    """
    bundle: {'income_y','balance_y','ratio_y','cashflow_y'} — DataFrame của CÙNG MỘT nguồn.
    Trả về các chuỗi năm đã chuẩn hoá (tỷ VNĐ), bảng fin_y thô và điểm đủ-dữ-liệu.
    """
    years = set(years)
    fin_y = build_financial_table(bundle.get('income_y'), bundle.get('balance_y'),
                                  bundle.get('ratio_y'), ticker=ticker, period='year')
    equity = normalize_to_billion_vnd(fin_y['equity'])
    core = {
        'revenue':      filter_years(normalize_to_billion_vnd(fin_y['revenue']), years),
        'net_profit':   filter_years(
            normalize_net_profit_with_anchor(fin_y['net_profit'], equity, fin_y['roe']), years),
        'equity':       filter_years(equity, years),
        'total_assets': filter_years(normalize_to_billion_vnd(fin_y['total_assets']), years),
        'eps':          filter_years(fin_y['eps'], years),
        'fin_y':        fin_y,
    }
    core['score'] = sum(len(core[f]) for f in SCORE_FIELDS)
    return core


def is_complete(core: dict, years) -> bool:
    return core is not None and core['score'] >= len(set(years)) * len(SCORE_FIELDS)


def pick_best_source(cores: dict, order, years=None):
    """Nguồn có điểm đủ-dữ-liệu cao nhất; hòa thì theo thứ tự ưu tiên."""
    best, best_score = None, -1
    for src in order:
        c = cores.get(src)
        if c is not None and c['score'] > best_score:
            best, best_score = src, c['score']
    return best


# ────────────────────────── gộp nguồn có kiểm chứng ────────────────────────

def _agrees(base: pd.Series, other: pd.Series):
    """(khớp?, lý do). Trên các năm trùng: trung vị tỷ lệ phải ≈ 1 và >= OVERLAP_MIN năm."""
    common = [y for y in other.index if y in base.index and base[y] != 0 and other[y] != 0]
    if len(common) < OVERLAP_MIN:
        return False, f'chỉ {len(common)} năm trùng (cần >= {OVERLAP_MIN})'
    ratios = [other[y] / base[y] for y in common]
    med = float(np.median(ratios))
    if abs(med - 1.0) > OVERLAP_TOL:
        return False, f'lệch {med:.3f}x so với nguồn chính (khác định nghĩa/đơn vị)'
    return True, ''


def merge_cores(cores: dict, order, years) -> dict:
    """
    Gộp các nguồn theo từng chỉ tiêu: nguồn nhiều năm nhất làm gốc, năm thiếu chỉ được bù
    từ nguồn khác khi nguồn đó khớp gốc trên >= 2 năm trùng. Cùng một luật cho mọi năm.
    Trả về {field: Series, 'notes': [...], 'sources': {field: nguồn gốc}}.
    """
    years = sorted(set(years))
    notes: list = []
    out: dict = {'sources': {}}
    avail = [s for s in order if s in cores]
    for field in FIELDS:
        ranked = sorted(avail, key=lambda s: (-len(cores[s][field]), order.index(s)))
        if not ranked:
            out[field] = pd.Series(dtype=float)
            continue
        base_src = ranked[0]
        base = cores[base_src][field].copy()
        out['sources'][field] = base_src
        for src in ranked[1:]:
            missing = [y for y in years if y not in base.index]
            if not missing:
                break
            other = cores[src][field]
            patch = {y: other[y] for y in missing if y in other.index}
            if not patch:
                continue
            ok, why = _agrees(cores[base_src][field], other)
            if ok:
                for y, v in patch.items():
                    base[y] = v
                notes.append(f'{field}: bù {sorted(patch)} từ {src} (khớp {base_src} trên năm trùng)')
            else:
                notes.append(f'{field}: KHÔNG bù {sorted(patch)} từ {src} — {why}')
        out[field] = base.sort_index()
    out['notes'] = notes
    return out


def coverage_text(merged: dict, years) -> str:
    """Ví dụ: 'Doanh thu 5/5 · LNST 5/5 · Vốn CSH thiếu 2021 · Tổng TS 5/5'."""
    years = sorted(set(years))
    labels = {'revenue': 'Doanh thu', 'net_profit': 'LNST',
              'equity': 'Vốn CSH', 'total_assets': 'Tổng TS'}
    parts = []
    for f, lab in labels.items():
        have = set(merged.get(f, pd.Series(dtype=float)).index)
        miss = [y for y in years if y not in have]
        parts.append(f'{lab} {len(years)}/{len(years)}' if not miss
                     else f'{lab} thiếu {", ".join(map(str, miss))}')
    return ' · '.join(parts)
