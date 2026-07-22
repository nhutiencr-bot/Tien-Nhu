"""
Test financial_normalizer.py với cấu trúc DataFrame đúng của vnstock
(cột 'item' chứa tên dòng, columns = năm int).
"""
import pandas as pd
from financial_normalizer import (
    build_financial_table, _find_revenue_for_bank,
    _find_revenue_for_securities, _norm_label,
)

def make_df(index_labels, data_dict):
    """Helper: tạo df với cột 'item' như vnstock."""
    df = pd.DataFrame(data_dict)
    df.insert(0, 'item', index_labels)
    return df


# ── Test 1: Ngân hàng TPB ──────────────────────────────────────────────────
df_bank = make_df(
    ['1. Thu nhập lãi và các khoản thu nhập tương tự',
     '2. Chi phí lãi và các chi phí tương tự',
     'I. Thu nhập lãi thuần',
     'II. Thu nhập từ hoạt động dịch vụ thuần',
     'Doanh thu hoạt động'],   # ← bẫy: bản cũ pick cái này
    {2021: [17427,7481,9946,5000,2500],
     2022: [21811,10424,11387,6200,3000],
     2023: [28562,16135,12428,7100,3500],
     2024: [25949,13042,12907,7500,3800],
     2025: [30751,17379,13371,8200,4000]},
)

rev = _find_revenue_for_bank(df_bank)
assert not rev.empty, 'FAIL bank: empty result'
assert rev[2025] == 13371, f'FAIL bank 2025: got {rev[2025]} (expected 13371 = NII)'
assert rev[2022] == 11387, f'FAIL bank 2022: got {rev[2022]}'
print(f'✅ BANK (TPB): values={rev.to_dict()} → 2025={rev[2025]:,.0f} tỷ (NII, không phải gross)')


# ── Test 2: Chứng khoán SSI ────────────────────────────────────────────────
df_sec = make_df(
    ['Doanh thu hoạt động', 'Phí hoa hồng môi giới', 'Doanh thu thuần'],
    {2021: [3500, 1200, 2300], 2022: [4200, 1500, 2700]},
)

rev = _find_revenue_for_securities(df_sec)
assert not rev.empty, 'FAIL sec: empty result'
assert rev[2022] == 4200, f'FAIL sec 2022: got {rev[2022]} (expected 4200 = Doanh thu HĐ)'
print(f'✅ SECURITIES (SSI): 2022={rev[2022]:,.0f} tỷ (Doanh thu hoạt động)')


# ── Test 3: BĐS VHM via build_financial_table ─────────────────────────────
df_bds = make_df(
    ['Doanh thu bán hàng và cung cấp dịch vụ', 'Doanh thu thuần', 'Giá vốn hàng bán'],
    {2021: [20000,18500,15000], 2022: [35000,32000,28000]},
)
result = build_financial_table(df_bds, pd.DataFrame(), ticker='VHM')
rev = result['revenue']
assert not rev.empty, 'FAIL bds: empty'
assert rev[2022] == 35000, f'FAIL bds: got {rev[2022]}'
print(f'✅ BĐS (VHM): 2022={rev[2022]:,.0f} tỷ (Doanh thu bán hàng CCDV)')


# ── Test 4: Bán lẻ MWG ────────────────────────────────────────────────────
df_mwg = make_df(
    ['Doanh thu bán hàng và cung cấp dịch vụ', 'Doanh thu thuần', 'Giá vốn hàng bán'],
    {2021: [100000,95000,60000], 2022: [115000,109000,70000]},
)
result = build_financial_table(df_mwg, pd.DataFrame(), ticker='MWG')
rev = result['revenue']
assert rev[2022] == 115000, f'FAIL retail: got {rev[2022]}'
print(f'✅ RETAIL (MWG): 2022={rev[2022]:,.0f} tỷ')


# ── Test 5: Dầu khí GAS (general path) ───────────────────────────────────
df_gas = make_df(
    ['Doanh thu bán hàng và cung cấp dịch vụ', 'Doanh thu thuần', 'Giá vốn hàng bán'],
    {2021: [60000,57000,40000], 2022: [80000,76000,55000]},
)
result = build_financial_table(df_gas, pd.DataFrame(), ticker='GAS')
rev = result['revenue']
assert rev[2022] == 80000, f'FAIL oilgas: got {rev[2022]}'
print(f'✅ OILGAS (GAS): 2022={rev[2022]:,.0f} tỷ')


# ── Test 6: _norm_label bug fix ───────────────────────────────────────────
assert _norm_label('hoạt động') == 'hoat dong', f'FAIL: {_norm_label("hoạt động")}'
assert _norm_label('Thu nhập lãi thuần') == 'thu nhap lai thuan'
assert _norm_label('Doanh thu bán hàng và cung cấp dịch vụ') == 'doanh thu ban hang va cung cap dich vu'
assert _norm_label('Đầu tư') == 'dau tu'
print('✅ _norm_label: đ/Đ bug fixed OK')


# ── Test 7: bank không pick "Doanh thu hoạt động" (2500 tỷ) ─────────────
rev_bank = _find_revenue_for_bank(df_bank)
assert rev_bank[2021] == 9946, f'FAIL: bank lấy sai dòng, got {rev_bank[2021]} (phải là NII=9946, không phải 2500)'
print(f'✅ BANK anti-trap: 2021={rev_bank[2021]:,.0f} (NII), không phải 2500 (Doanh thu HĐ)')


print('\n🎉 Tất cả 7 test pass!')
