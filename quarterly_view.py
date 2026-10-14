"""
quarterly_view.py
-----------------
Bảng KQKD theo QUÝ dạng pivot: mỗi hàng = 1 năm, cột = Q1 | Q2 | Q3 | Q4 | Cả năm.

  - 2021..TARGET_YEAR (đã đóng sổ): đủ 4 quý.
  - Năm đang chạy (vd 2026): chỉ có Q1..Q3 — quý chưa công bố (vd Q3/2026) và
    Q4 để TRỐNG (None), không đoán, không ước tính.

Module thuần pandas, không import streamlit → test được độc lập.

Xử lý 2 "bẫy" dữ liệu quý của vnstock:
  1. Dữ liệu quý có thể là LŨY KẾ (YTD: Q2 = Q1+Q2, Q3 = 9 tháng...) chứ không
     phải số riêng từng quý → tự nhận diện và đổi về số riêng từng quý.
  2. Thiếu Q4 nhưng có số cả năm → Q4 = Cả năm − (Q1+Q2+Q3).
"""
from __future__ import annotations

import math
import pandas as pd

QCOLS = ['Q1', 'Q2', 'Q3', 'Q4']
TOTAL_COL = 'Cả năm / Lũy kế'


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _parse_q_key(k):
    """'2025-Q3' | 'Q3/2025' -> (2025, 3); không parse được -> None."""
    s = str(k).strip().upper().replace(' ', '')
    try:
        if '-Q' in s:
            y, q = s.split('-Q')
            return int(y), int(q)
        if s.startswith('Q') and '/' in s:
            q, y = s[1:].split('/')
            return int(y), int(q)
    except ValueError:
        return None
    return None


def _looks_cumulative(vals, annual):
    """vals: [q1..q4] (None nếu thiếu). True nếu chuỗi là lũy kế YTD."""
    present = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(present) < 2 or any(v <= 0 for _, v in present):
        return False
    seq = [v for _, v in present]
    if any(b < a * 0.999 for a, b in zip(seq, seq[1:])):   # lũy kế không giảm
        return False
    # Có số cả năm: Q4 lũy kế ≈ cả năm
    if annual and annual > 0 and vals[3] is not None:
        if abs(vals[3] - annual) / annual <= 0.03:
            return seq[-1] >= seq[0] * 1.3
        return False
    # Không có số cả năm (năm đang chạy): dựa vào bước nhảy ~×2, ×1.5
    q1, q2 = vals[0], vals[1]
    if q1 is not None and q2 is not None:
        if q2 < q1 * 1.5:
            return False
        q3 = vals[2]
        return q3 is None or q3 >= q2 * 1.25
    return False


def _decumulate(vals):
    out, prev = [], 0.0
    for v in vals:
        if v is None:
            out.append(None)
            continue
        out.append(round(v - prev, 2))
        prev = v
    return out


def build_quarter_pivot(q_series: pd.Series, annual_series: pd.Series,
                        start_year: int, end_year: int,
                        in_progress_year: int | None = None) -> pd.DataFrame:
    """
    q_series      : index 'YYYY-Qn' -> giá trị (tỷ VNĐ), có thể lũy kế.
    annual_series : index int năm  -> giá trị cả năm (tỷ VNĐ), đã chuẩn hoá.
    Trả về DataFrame: Năm | Q1 | Q2 | Q3 | Q4 | Cả năm / Lũy kế
    """
    raw: dict[int, list] = {}
    if q_series is not None and len(q_series) > 0:
        for k, v in q_series.items():
            pk = _parse_q_key(k)
            fv = _num(v)
            if pk and 1 <= pk[1] <= 4 and fv is not None:
                raw.setdefault(pk[0], [None] * 4)[pk[1] - 1] = fv

    ann = {}
    if annual_series is not None and len(annual_series) > 0:
        for k, v in annual_series.items():
            try:
                fv = _num(v)
                if fv is not None:
                    ann[int(str(k).split('.')[0].split('-')[0])] = fv
            except ValueError:
                continue

    rows = []
    for yr in range(start_year, end_year + 1):
        vals = raw.get(yr, [None] * 4)
        a = ann.get(yr)
        in_prog = (in_progress_year is not None and yr == in_progress_year)
        if _looks_cumulative(vals, None if in_prog else a):
            vals = _decumulate(vals)

        # Q4 thiếu nhưng đã biết Q1-Q3 và số cả năm (chỉ cho năm đã đóng sổ)
        if (not in_prog and vals[3] is None and a is not None
                and all(v is not None for v in vals[:3])):
            vals[3] = round(a - sum(vals[:3]), 2)

        known = [v for v in vals if v is not None]
        if in_prog:
            total = round(sum(known), 2) if known else None
        elif a is not None:
            total = round(a, 2)
        else:
            total = round(sum(known), 2) if len(known) == 4 else None

        rows.append({'Năm': yr, 'Q1': vals[0], 'Q2': vals[1],
                     'Q3': vals[2], 'Q4': vals[3], TOTAL_COL: total})
    return pd.DataFrame(rows, columns=['Năm'] + QCOLS + [TOTAL_COL])
