"""Test luật MỘT-NGUỒN-MỘT-LUẬT cho 2021-2025 + bảng quý. Chạy: python test_quarterly_and_revenue.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from financial_normalizer import _find_revenue_general, _find_revenue_for_bank, TARGET_YEAR
from financials_core import (build_core, pick_best_source, is_complete, resolve_flow,
                             resolve_stock, normalize_to_billion_vnd)
from quarterly_view import build_quarter_pivot, quarter_year_sums

YEARS = list(range(2021, 2026))
assert TARGET_YEAR == 2025, "test viết cho khung 2021-2025"


def mk(labels, cols):
    df = pd.DataFrame(cols); df.insert(0, 'item', labels); return df


# ── 1) Bẫy chọn dòng ───────────────────────────────────────────────────────
df = mk(['1. Doanh thu bán hàng và cung cấp dịch vụ', '2. Các khoản giảm trừ doanh thu',
         '10. Doanh thu thuần về bán hàng và cung cấp dịch vụ',
         'Tăng trưởng doanh thu thuần (%)', 'Doanh thu thuần hoạt động tài chính'],
        {2021: [100, 5, 95, None, 7], 2022: [120, 6, 114, 20, 8], 2023: [130, 7, 123, 8, 9],
         2024: [140, 8, 132, 7, 10], 2025: [160, 12, 148, 12, 11]})
r = _find_revenue_general(df); assert r[2025] == 148 and r[2021] == 95

# ── 2) Cột '2025-Q4' đứng trước cột năm '2025' ─────────────────────────────
df2 = pd.DataFrame({'item': ['Doanh thu thuần'], '2025-Q4': [40], 2021: [95], 2022: [114],
                    2023: [123], 2024: [132], '2025': [148]})
assert _find_revenue_general(df2)[2025] == 148

# ── 3) Ngân hàng: chỉ NII, không rơi sang Tổng thu nhập hoạt động ──────────
dfb = mk(['I. Thu nhập lãi thuần', 'Tổng thu nhập hoạt động'],
         {2021: [26699, 37076], 2022: [30290, 40527], 2023: [27691, 40061],
          2024: [35508, 46990], 2025: [None, 52316]})        # NII thiếu 2025, TOI có
rb = _find_revenue_for_bank(dfb)
assert rb[2024] == 35508 and 2025 not in rb.dropna().index, rb.to_dict()   # KHÔNG mượn 52316

# ── 4) TCB bằng số thật TCBS: 2025 thiếu ở cột năm -> tổng 4 quý của CHÍNH dòng NII ──
inc_y = mk(['Thu nhập lãi thuần', 'Tổng thu nhập hoạt động', 'Lợi nhuận sau thuế'],
           {2021: [26699, 37076, 18415], 2022: [30290, 40527, 20436], 2023: [27691, 40061, 18191],
            2024: [35508, 46990, 21760], 2025: [None, 52316, None]})
bal_y = mk(['Vốn chủ sở hữu', 'Tổng cộng tài sản'],
           {2021: [90000, 350000], 2022: [105000, 380000], 2023: [120000, 420000],
            2024: [150000, 450000], 2025: [None, None]})
inc_q = mk(['Thu nhập lãi thuần', 'Tổng thu nhập hoạt động', 'Lợi nhuận sau thuế'],
           {'2025-Q1': [8305, 11611, 6014], '2025-Q2': [9137, 12743, 6348],
            '2025-Q3': [9925, 14242, 6614], '2025-Q4': [10788, 14795, 6980]})
bal_q = mk(['Vốn chủ sở hữu', 'Tổng cộng tài sản'], {'2025-Q4': [172000, 480000]})
core = build_core({'income_y': inc_y, 'balance_y': bal_y, 'ratio_y': pd.DataFrame(),
                   'income_q': inc_q, 'balance_q': bal_q, 'ratio_q': pd.DataFrame()}, 'TCB', YEARS)
rev = core['revenue']
assert [rev[y] for y in YEARS] == [26699, 30290, 27691, 35508, 38155], rev.to_dict()
assert core['net_profit'][2025] == 6014 + 6348 + 6614 + 6980
assert core['equity'][2025] == 172000 and core['total_assets'][2025] == 480000
print("TCB:", rev.to_dict())

# ── 5) Cột năm 2025 CÓ giá trị thì dùng cột năm; lệch >5% với 4 quý chỉ GHI CHÚ ─────
n = []
s = resolve_flow(pd.Series({2025: 45000.}), pd.Series({'2025-Q1': 8305, '2025-Q2': 9137,
                 '2025-Q3': 9925, '2025-Q4': 10788}), [2025], n, 'revenue')
assert s[2025] == 45000 and n and 'lệch' in n[0]

# ── 6) Nguồn trả LŨY KẾ -> tự đổi về từng quý ─────────────────────────────
cum = pd.Series({'2025-Q1': 8305, '2025-Q2': 17442, '2025-Q3': 27367, '2025-Q4': 38155})
assert resolve_flow(pd.Series(dtype=float), cum, [2025])[2025] == 38155

# ── 7) Thiếu cả cột năm lẫn đủ 4 quý -> TRỐNG, không đoán ──────────────────
assert resolve_flow(pd.Series(dtype=float), pd.Series({'2025-Q1': 1, '2025-Q2': 2}), [2025]).empty
assert resolve_stock(pd.Series(dtype=float), pd.Series({'2025-Q3': 5}), [2025]).empty

# ── 8) Chọn nguồn theo ĐỘ ĐỦ của cả bảng, hòa thì theo thứ tự ───────────────
full = {'score': 20, 'x': 1}; part = {'score': 16}
assert pick_best_source({'DNSE': part, 'KBS': full}, ['DNSE', 'KBS', 'VCI'], YEARS) == 'KBS'
assert pick_best_source({'DNSE': full, 'KBS': dict(full)}, ['DNSE', 'KBS', 'VCI'], YEARS) == 'DNSE'
assert is_complete(full, YEARS) and not is_complete(part, YEARS)

# ── 9) Doanh nghiệp thường: mọi năm cùng một đường đi ─────────────────────────
inc = mk(['10. Doanh thu thuần', 'Giá vốn hàng bán', 'Lợi nhuận sau thuế'],
         {y: [100 + 10 * i, 60, 10 + i] for i, y in enumerate(YEARS)})
bal = mk(['Vốn chủ sở hữu', 'Tổng cộng tài sản'], {y: [500 + i, 900 + i] for i, y in enumerate(YEARS)})
c2 = build_core({'income_y': inc, 'balance_y': bal, 'income_q': pd.DataFrame(),
                 'balance_q': pd.DataFrame(), 'ratio_y': pd.DataFrame(), 'ratio_q': pd.DataFrame()},
                'ABC', YEARS)
assert c2['revenue'].to_dict() == {y: 100 + 10 * i for i, y in enumerate(YEARS)} and is_complete(c2, YEARS)

# ── 10) Bảng pivot quý ───────────────────────────────────────────────────────
q = {}
for y, base in zip(range(2021, 2026), [20, 24, 26, 28, 32]):
    for i in range(4): q[f'{y}-Q{i+1}'] = base + i
q['2026-Q1'] = 40; q['2026-Q2'] = 44
ann = pd.Series({y: sum(b + i for i in range(4)) for y, b in zip(range(2021, 2026), [20, 24, 26, 28, 32])})
p = build_quarter_pivot(pd.Series(q), ann, 2021, 2026, 2026)
assert p.loc[p['Năm'] == 2026, 'Q3'].isna().all() and p.loc[p['Năm'] == 2025, 'Q4'].iloc[0] == 35
cumq = {}
for y, base in zip(range(2021, 2026), [20, 24, 26, 28, 32]):
    c = 0
    for i in range(4): c += base; cumq[f'{y}-Q{i+1}'] = c
cumq['2023-Q4'] = np.nan; cumq['2026-Q1'] = 40; cumq['2026-Q2'] = 84
ann2 = pd.Series({y: b * 4 for y, b in zip(range(2021, 2026), [20, 24, 26, 28, 32])})
p = build_quarter_pivot(pd.Series(cumq), ann2, 2021, 2026, 2026)
assert p.loc[p['Năm'] == 2022, 'Q2'].iloc[0] == 24 and p.loc[p['Năm'] == 2023, 'Q4'].iloc[0] == 26
assert p.loc[p['Năm'] == 2026, 'Q2'].iloc[0] == 44 and pd.isna(p.loc[p['Năm'] == 2026, 'Q3'].iloc[0])
print("ALL OK")
