import sys; import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
from financial_normalizer import _find_revenue_general, build_financial_table, TARGET_YEAR
from quarterly_view import build_quarter_pivot
print("TARGET_YEAR", TARGET_YEAR)

def mk(labels, cols):
    df = pd.DataFrame(cols); df.insert(0,'item',labels); return df

# 1) Bẫy chọn dòng: dòng phụ có nhiều năm hơn dòng chuẩn
df = mk(['1. Doanh thu bán hàng và cung cấp dịch vụ','2. Các khoản giảm trừ doanh thu',
         '10. Doanh thu thuần về bán hàng và cung cấp dịch vụ',
         'Tăng trưởng doanh thu thuần (%)','Doanh thu thuần hoạt động tài chính'],
        {2021:[100,5,95,None,7],2022:[120,6,114,20,8],2023:[130,7,123,8,9],
         2024:[140,8,132,7,10],2025:[160,12,148,12,11]})
r=_find_revenue_general(df); print("trap rows ->", r.to_dict()); assert r[2025]==148 and r[2021]==95

# 2) Cột '2025-Q4' đứng TRƯỚC '2025' (số riêng quý 4 = 40) -> phải lấy cả năm 148
df2 = pd.DataFrame({'item':['Doanh thu thuần'],'2025-Q4':[40],2021:[95],2022:[114],2023:[123],2024:[132],'2025':[148]})
df2 = df2[['item','2025-Q4',2021,2022,2023,2024,'2025']]
r=_find_revenue_general(df2); print("Q4-col trap ->", r.to_dict()); assert r[2025]==148
# Chỉ có Q4 (không có cột năm) vẫn dùng được
df3 = pd.DataFrame({'item':['Doanh thu thuần'],2024:[132],'2025-Q4':[148]})
assert _find_revenue_general(df3)[2025]==148

# 3) Pivot: số RIÊNG từng quý, 2026 chỉ Q1,Q2 (Q3 trống)
q={}
for y,base in zip(range(2021,2026),[20,24,26,28,32]):
    for i in range(4): q[f'{y}-Q{i+1}']=base+i
q['2026-Q1']=40; q['2026-Q2']=44
ann=pd.Series({2021:sum(20+i for i in range(4)),2022:sum(24+i for i in range(4)),2023:sum(26+i for i in range(4)),2024:sum(28+i for i in range(4)),2025:sum(32+i for i in range(4))})
p=build_quarter_pivot(pd.Series(q),ann,2021,2026,2026); print(p.to_string())
assert p.loc[p['Năm']==2026,'Q3'].isna().all() and p.loc[p['Năm']==2026,'Q1'].iloc[0]==40
assert p.loc[p['Năm']==2025,'Q4'].iloc[0]==35

# 4) Pivot: dữ liệu LŨY KẾ + thiếu Q4 năm cũ + 2026 lũy kế 9T chưa có
cum={}
for y,base in zip(range(2021,2026),[20,24,26,28,32]):
    c=0
    for i in range(4): c+=base; cum[f'{y}-Q{i+1}']=c
cum['2023-Q4']=np.nan
cum['2026-Q1']=40; cum['2026-Q2']=84
ann2=pd.Series({y:b*4 for y,b in zip(range(2021,2026),[20,24,26,28,32])})
p=build_quarter_pivot(pd.Series(cum),ann2,2021,2026,2026); print(p.to_string())
assert p.loc[p['Năm']==2022,'Q2'].iloc[0]==24 and p.loc[p['Năm']==2023,'Q4'].iloc[0]==26
assert p.loc[p['Năm']==2026,'Q2'].iloc[0]==44 and pd.isna(p.loc[p['Năm']==2026,'Q3'].iloc[0])
print("ALL OK")


# ── TCB: số thật từ TCBS (NII, tỷ VNĐ) ──────────────────────────────────────
from quarterly_view import quarter_year_sums, reconcile_with_quarters
from series_consistency import merge_consistent
notes = []
tcb_year = pd.Series({2021: 26699., 2022: 30290., 2023: 27691., 2024: 35508., 2025: 52316.})  # 2025 sai
q25 = {'2025-Q1': 8305, '2025-Q2': 9137, '2025-Q3': 9925, '2025-Q4': 10788}
q24 = {'2024-Q1': 8000, '2024-Q2': 8700, '2024-Q3': 9000, '2024-Q4': 9808}
sums = quarter_year_sums(pd.Series({**q24, **q25}))
assert sums[2025] == 38155 and sums[2024] == 35508, sums
fixed = reconcile_with_quarters(tcb_year, sums, range(2021, 2026), notes=notes)
assert fixed[2025] == 38155 and fixed[2024] == 35508 and fixed[2021] == 26699, fixed
print("TCB lệch 2025 ->", fixed[2025], "|", notes[0])

# Cùng dữ liệu nhưng nguồn trả LŨY KẾ
cum = pd.Series({'2025-Q1': 8305, '2025-Q2': 17442, '2025-Q3': 27367, '2025-Q4': 38155})
assert quarter_year_sums(cum)[2025] == 38155

# Năm 2025 THIẾU hoàn toàn -> điền từ tổng 4 quý
miss = tcb_year.drop(2025)
assert reconcile_with_quarters(miss, sums, range(2021, 2026))[2025] == 38155

# Năm đúng (lệch <5%) thì KHÔNG bị đụng; thiếu quý -> không đụng
ok = tcb_year.copy(); ok[2025] = 38155.09
assert reconcile_with_quarters(ok, sums, range(2021, 2026))[2025] == 38155.09
part = quarter_year_sums(pd.Series({'2025-Q1': 8305, '2025-Q2': 9137}))
assert 2025 not in part

# Yahoo chỉ có 2025 (không năm trùng): doanh thu/LNST bị LOẠI, equity vẫn nhận
base = tcb_year.drop(2025)
only25 = pd.Series({2025: 52316.})
assert 2025 not in merge_consistent(base, only25, label='revenue', require_overlap=True).index
assert 2025 in merge_consistent(base, only25, label='equity').index
print("ALL OK (TCB)")
