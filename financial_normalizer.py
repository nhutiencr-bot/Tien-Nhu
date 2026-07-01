"""
financial_normalizer_patched.py
════════════════════════════════════════════════════════════════════
DIFF PATCH: thay thế hardcode sector sets → ticker_registry động

Chỉ cần áp 3 thay đổi vào financial_normalizer_fixed.py:
  PATCH A: import
  PATCH B: hàm build_financial_table() — phần detect sector
  PATCH C: hàm build_5y_financial_table() — truyền ticker

════════════════════════════════════════════════════════════════════
"""

# ════════════════════════════════════════════════════════════════
# PATCH A — Thêm vào ĐẦU FILE (thay toàn bộ các dict hardcode)
# ════════════════════════════════════════════════════════════════
PATCH_A_REMOVE = """
BANK_TICKERS = {
    'VCB', 'BID', 'CTG', 'TCB', 'MBB', ...
}
FINANCIAL_TICKERS = {...}
RETAIL_TICKERS = {...}
REAL_ESTATE_TICKERS = {...}
"""

PATCH_A_ADD = """
# ── Import registry động — tự động cập nhật 1500+ mã 3 sàn ──
from ticker_registry import get_sector

# Backward-compat: các set ảo vẫn hoạt động với `ticker in BANK_TICKERS`
from ticker_registry import (
    BANK_TICKERS, FINANCIAL_TICKERS, INSURANCE_TICKERS,
    RETAIL_TICKERS, REAL_ESTATE_TICKERS,
)
"""

# ════════════════════════════════════════════════════════════════
# PATCH B — Thay nội dung detect ngành trong build_financial_table()
# ════════════════════════════════════════════════════════════════
PATCH_B_REMOVE = """
    is_bank = ticker in BANK_TICKERS if ticker else False
    is_financial = ticker in FINANCIAL_TICKERS if ticker else False
    is_retail = ticker in RETAIL_TICKERS if ticker else False
    is_realestate = ticker in REAL_ESTATE_TICKERS if ticker else False
"""

PATCH_B_ADD = """
    # ── Phân loại ngành TỰ ĐỘNG từ registry 1500+ mã ──
    if ticker:
        _sector = get_sector(ticker)
        is_bank      = _sector == 'bank'
        is_financial = _sector in ('insurance', 'securities')
        is_retail    = _sector == 'retail'
        is_realestate = _sector == 'realestate'
    else:
        is_bank = is_financial = is_retail = is_realestate = False
"""

# ════════════════════════════════════════════════════════════════
# PATCH C — build_5y_financial_table() đảm bảo ticker truyền qua
# ════════════════════════════════════════════════════════════════
PATCH_C_REMOVE = """
def build_5y_financial_table(df_income, df_balance, df_ratio=None, ticker=None):
    return build_financial_table(
        df_income, df_balance, df_ratio,
        ticker=ticker,
        period='year'
    )
"""

# Giống hệt → không cần đổi nếu đã áp patch lần trước.
# Chỉ cần đảm bảo build_financial_table nhận ticker và gọi get_sector().

# ════════════════════════════════════════════════════════════════
# MINH HỌA: đoạn code cuối cùng sau khi patch
# ════════════════════════════════════════════════════════════════

FINAL_EXAMPLE = '''
# financial_normalizer.py — AFTER PATCH (chỉ phần detect ngành)

from ticker_registry import get_sector   # <- thêm dòng này ở đầu file

def build_financial_table(df_income, df_balance, df_ratio=None,
                          ticker=None, period="year"):

    # ── Detect ngành từ registry động ──────────────────────────
    if ticker:
        _s = get_sector(ticker)        # gọi 1 lần, O(1) lookup
        is_bank       = _s == "bank"
        is_financial  = _s in ("insurance", "securities")
        is_retail     = _s == "retail"
        is_realestate = _s == "realestate"
    else:
        is_bank = is_financial = is_retail = is_realestate = False

    # ── Revenue theo ngành ──────────────────────────────────────
    if is_bank or is_financial:
        data["revenue"] = _find_revenue_for_bank(df_income, period=period)
    elif is_realestate:
        data["revenue"] = _find_revenue_for_realestate(df_income, period=period)
    elif is_retail:
        data["revenue"] = _find_revenue_for_retail(df_income, period=period)
    else:
        data["revenue"] = find_row_series(df_income, [...], period=period)
        if data["revenue"].empty:
            data["revenue"] = _find_revenue_for_retail(df_income, period=period)

    # ... phần còn lại giữ nguyên
'''

if __name__ == '__main__':
    print("PATCH A — Xóa hardcode, thêm import:")
    print(PATCH_A_ADD)
    print("\nPATCH B — Thay detect sector:")
    print(PATCH_B_ADD)
    print("\nFinal example:")
    print(FINAL_EXAMPLE)
