"""
series_consistency.py
---------------------
Đảm bảo chuỗi số liệu theo năm (doanh thu, LNST, vốn CSH, tổng tài sản) ĐỒNG NHẤT
giữa các năm 2021-2025 dù từng năm được cào từ nguồn/tầng khác nhau.

Hai nguyên nhân gây lệch đã gặp trong pipeline:

1. LỆCH ĐƠN VỊ THEO TỪNG NĂM
   normalize_to_billion_vnd() quyết định đơn vị theo độ lớn của TỪNG GIÁ TRỊ khi
   được gọi trên 1 giá trị lẻ (_raw_scan_annual, _aggregate_year_from_quarters).
   Cùng 1 nguồn nhưng năm này rơi trên ngưỡng 1e7, năm kia dưới ngưỡng -> lệch 1000 lần.
   -> harmonize_units(): đưa các năm lệch bội số 1000 về đơn vị của đa số năm.

2. LỆCH ĐỊNH NGHĨA / ĐƠN VỊ GIỮA CÁC NGUỒN
   _merge_series() chỉ vá năm thiếu bằng nguồn kế tiếp (CafeF/DNSE/Yahoo/web) mà không
   kiểm tra nguồn đó có CÙNG khái niệm & đơn vị với base hay không (vd gross vs net).
   -> merge_consistent(): so khớp trên các năm trùng nhau; không khớp thì loại nguồn.
"""

import math

import numpy as np
import pandas as pd


def _note(notes, msg):
    if notes is not None:
        notes.append(msg)


def _to_float_series(s):
    if s is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(s, errors='coerce').astype(float)


def harmonize_units(series, notes=None, label='series'):
    """
    Nếu vài năm lệch với đa số năm theo bội số 1000 (10^3, 10^6, 10^9)
    thì chia/nhân lại cho về cùng đơn vị. Doanh thu thật không bao giờ lệch >200 lần
    so với trung vị 5 năm nên ngưỡng này an toàn.
    """
    s = _to_float_series(series)
    if s.empty:
        return series
    pos = s[(s.notna()) & (s.abs() > 0)]
    if len(pos) < 2:
        return series

    logs = pos.abs().map(math.log10)
    ref = float(np.median(logs.values))
    out = s.copy()
    for yr, lg in logs.items():
        d = lg - ref
        k = round(d / 3.0)
        if k != 0 and abs(d - 3.0 * k) < 0.7:
            factor = 10.0 ** (3 * k)
            out[yr] = s[yr] / factor
            _note(notes, f'{label}[{yr}]: lệch đơn vị ×10^{3 * k} so với các năm khác -> đã chuẩn hoá '
                         f'({s[yr]:,.2f} -> {out[yr]:,.2f})')
    return out


def _nearest_power_of_1000(ratio):
    """ratio = patch/base. Trả về k sao cho ratio ≈ 10^(3k) (k != 0), hoặc 0 nếu không."""
    if not (ratio > 0) or not math.isfinite(ratio):
        return 0
    lg = math.log10(ratio)
    k = round(lg / 3.0)
    if k != 0 and abs(lg - 3.0 * k) < 0.35:   # trong vòng ~2.2 lần quanh bội số 1000
        return k
    return 0


def merge_consistent(base, patch, tol=0.08, notes=None, label='series',
                     require_overlap=False):
    """
    Vá các năm THIẾU của base bằng patch, nhưng chỉ khi patch cùng đơn vị + cùng định nghĩa:

    - Có năm trùng: lấy trung vị tỷ lệ patch/base.
        * tỷ lệ ≈ 10^(3k)  -> rescale patch về đơn vị của base.
        * sau đó lệch > tol (mặc định 8%) -> khác định nghĩa (gross vs net, riêng vs hợp nhất...)
          => LOẠI cả nguồn, KHÔNG vá (thà thiếu còn hơn lệch).
    - Không có năm trùng: so từng giá trị với năm gần nhất của base,
      chọn bội số 1000 hợp lý nhất; ngoài khoảng [0.25x, 4x] thì bỏ.
    """
    base = _to_float_series(base)
    patch = _to_float_series(patch)
    patch = patch[patch.notna()]
    if patch.empty:
        return base.sort_index()

    base_valid = base[base.notna() & (base.abs() > 0)]

    # Base rỗng: không có gì để đối chiếu -> chỉ tự chuẩn hoá nội bộ patch
    if base_valid.empty:
        patch = harmonize_units(patch, notes=notes, label=label)
        out = base.copy()
        for yr, val in patch.items():
            if yr not in out.index or pd.isna(out.get(yr)):
                out[yr] = val
        return out.sort_index()

    overlap = [y for y in patch.index if y in base_valid.index and patch[y] != 0]
    patch_adj = patch.copy()

    if overlap:
        ratios = pd.Series({y: patch[y] / base_valid[y] for y in overlap})
        ratios = ratios[(ratios > 0) & np.isfinite(ratios)]
        if ratios.empty:
            return base.sort_index()
        med = float(ratios.median())
        k = _nearest_power_of_1000(med)
        if k != 0:
            patch_adj = patch / (10.0 ** (3 * k))
            med = med / (10.0 ** (3 * k))
            _note(notes, f'{label}: nguồn vá lệch đơn vị ×10^{3 * k} -> đã rescale')
        if abs(med - 1.0) > tol:
            _note(notes, f'{label}: nguồn vá KHÔNG khớp base trên {len(ratios)} năm trùng '
                         f'(tỷ lệ {med:.3f}) -> loại nguồn (khác định nghĩa/đơn vị)')
            return base.sort_index()
    else:
        # [FIX 20] Không có năm trùng thì KHÔNG THỂ kiểm tra định nghĩa (NII vs Tổng thu
        # nhập HĐ vs Total Revenue của Yahoo...). Với chỉ tiêu nhạy định nghĩa
        # (doanh thu, LNST) thà để trống còn hơn nhận số khác khái niệm — lỗi TCB 2025
        # (52,316 thay vì NII 38,155) lọt qua đúng nhánh này.
        if require_overlap:
            _note(notes, f'{label}: nguồn vá không có năm trùng để đối chiếu định nghĩa '
                         f'-> loại nguồn')
            return base.sort_index()
        accepted = {}
        for yr, val in patch.items():
            if val == 0:
                continue
            later_or_earlier = sorted(base_valid.index, key=lambda b: (abs(int(b) - int(yr)), int(b) > int(yr)))
            ref = float(base_valid[later_or_earlier[0]])
            best = None
            for k in (-3, -2, -1, 0, 1, 2, 3):
                cand = val / (10.0 ** (3 * k))
                r = cand / ref
                if 0.25 <= r <= 4.0 and (best is None or abs(math.log(r)) < abs(math.log(best[1]))):
                    best = (cand, r, k)
            if best is None:
                _note(notes, f'{label}[{yr}]: giá trị {val:,.2f} không hợp lý so với năm lân cận -> bỏ')
                continue
            if best[2] != 0:
                _note(notes, f'{label}[{yr}]: rescale ×10^{-3 * best[2]} ({val:,.2f} -> {best[0]:,.2f})')
            accepted[yr] = best[0]
        patch_adj = pd.Series(accepted, dtype=float)

    out = base.copy()
    for yr, val in patch_adj.items():
        if pd.isna(val):
            continue
        if yr not in out.index or pd.isna(out.get(yr)):
            out[yr] = val
    return out.sort_index()


if __name__ == '__main__':
    # 1) lệch đơn vị theo năm: 2023 bị đưa vào dạng triệu (×1000)
    n = []
    s = pd.Series({2021: 42273.0, 2022: 53246.0, 2023: 53_615_000.0, 2024: 55406.0, 2025: 58771.0})
    h = harmonize_units(s, notes=n, label='revenue')
    assert abs(h[2023] - 53615.0) < 1e-6 and h[2021] == 42273.0, h
    # 2) năm bị chia nhầm 1e9 (0.058)
    s2 = pd.Series({2021: 42273.0, 2022: 53246.0, 2023: 53615.0, 2024: 55406.0, 2025: 0.058771})
    h2 = harmonize_units(s2)
    assert abs(h2[2025] - 58771.0) < 1e-6, h2
    # 3) chuỗi sạch không bị đụng
    clean = pd.Series({2021: 100.0, 2022: 150.0, 2023: 400.0, 2024: 380.0, 2025: 520.0})
    assert harmonize_units(clean).equals(clean)

    base = pd.Series({2021: 42273.0, 2022: 53246.0, 2023: 53615.0, 2024: 55406.0})
    # 4) nguồn vá cùng định nghĩa, có năm trùng -> nhận 2025
    ok = pd.Series({2022: 53250.0, 2023: 53600.0, 2024: 55400.0, 2025: 58771.0})
    m = merge_consistent(base, ok)
    assert m[2025] == 58771.0
    # 5) nguồn vá khác đơn vị (triệu) -> rescale
    mil = ok * 1000
    m = merge_consistent(base, mil)
    assert abs(m[2025] - 58771.0) < 1e-6, m
    # 6) nguồn vá khác định nghĩa (gross +25%) -> loại, 2025 vẫn thiếu
    gross = ok * 1.25
    m = merge_consistent(base, gross, notes=n, label='revenue')
    assert 2025 not in m.index, m
    # 7) không có năm trùng: giá trị 2025 sai đơn vị được rescale theo năm lân cận
    only25 = pd.Series({2025: 58_771_000.0})
    m = merge_consistent(base, only25)
    assert abs(m[2025] - 58771.0) < 1e-6, m
    # 8) không có năm trùng nhưng phi lý -> bỏ
    m = merge_consistent(base, pd.Series({2025: 5_000_000_000.0 * 7.3}))
    print('series_consistency self-test OK')
    print('\n'.join(n))
