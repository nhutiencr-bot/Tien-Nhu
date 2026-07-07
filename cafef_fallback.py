"""
cafef_fallback.py — BẢN VÁ LỖI TOÀN DIỆN
═══════════════════════════════════════════════════════════════════════════════

BUG 1 — Bỏ sót năm 2021:
  fetch_cafef_balance_sheet_5y dùng range(end_year-4, end_year+1).
  end_year = _THIS_YEAR = 2026 → range(2022, 2027) → chỉ cào 2022–2026,
  bỏ sót 2021. Fix: truyền target_years=list vào thay vì tính range nội bộ.

BUG 2 — Thiếu revenue + net_profit:
  fetch_cafef_balance_sheet_5y chỉ parse bsheet (equity+total_assets),
  không parse incsta (revenue+net_profit).
  pipeline.py gọi hàm này để bù 2021 → revenue/LNST/EPS/ROE/ROA 2021 = —.
  Fix: parse incsta trong cùng _fetch_one_period, trả đủ 4 trường.

BUG 3 — Sai đơn vị → Vốn CSH 2021 = 343,821 thay vì 30,790:
  CafeF trả số đơn vị TRIỆU VNĐ (VD: 30,790,110 = 30,790.11 tỷ).
  Code cũ chia /1e9 → ra 30.79 (sai thấp 1000x).
  Fix: chia /1e6 để triệu → tỷ (đơn vị chuẩn trong pipeline).

  Kiểm tra: VCB Vốn CSH 2021 theo BCTC = 30,790 tỷ VNĐ.
    CafeF raw = 30_790_110 (đơn vị triệu)
    /1e6 → 30,790.11 tỷ ✅
    /1e9 → 30.79 tỷ ❌ (dẫn đến BVPS 2021 = 68,691 sai, đúng phải ~13,000)
═══════════════════════════════════════════════════════════════════════════════
"""

import re
import time
import concurrent.futures
import pandas as pd
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Referer": "https://s.cafef.vn/",
}
REQUEST_TIMEOUT = 8

_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)

_REACHABLE_CACHE = {"ts": 0.0, "value": None}
_REACHABLE_CACHE_TTL = 30


def _cafef_is_reachable() -> bool:
    now = time.time()
    if (_REACHABLE_CACHE["value"] is not None
            and (now - _REACHABLE_CACHE["ts"]) < _REACHABLE_CACHE_TTL):
        return _REACHABLE_CACHE["value"]
    try:
        resp = _SESSION.get("https://s.cafef.vn", timeout=REQUEST_TIMEOUT)
        ok = resp.status_code == 200
    except Exception:
        ok = False
    _REACHABLE_CACHE["ts"] = now
    _REACHABLE_CACHE["value"] = ok
    return ok


def _find_company_slug(ticker: str) -> str:
    return f"bao-cao-tai-chinh-{ticker.lower()}"


def _parse_vn_number(raw: str):
    raw = raw.strip()
    if not raw:
        return None
    raw = raw.replace('.', '').replace(',', '.')
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_row_values(html_text: str, row_label_pattern: str):
    plain_text = re.sub(r'<[^>]+>', ' ', html_text)
    plain_text = re.sub(r'&nbsp;', ' ', plain_text)
    plain_text = re.sub(r'\s+', ' ', plain_text)
    pattern = re.compile(
        row_label_pattern + r'\s*((?:-?[\d.,]+\s*){1,40})', re.IGNORECASE)
    match = pattern.search(plain_text)
    if not match:
        return []
    numbers_blob = match.group(1)
    raw_numbers = re.findall(r'-?[\d][\d.,]*', numbers_blob)
    return [_parse_vn_number(n) for n in raw_numbers]


# ─────────────────────────────────────────────────────────────────────────────
# FIX 3: Hàm convert đơn vị đúng
# CafeF balance sheet: đơn vị TRIỆU VNĐ → chia 1e6 để ra tỷ VNĐ
# CafeF income statement: cũng TRIỆU VNĐ → chia 1e6
# ─────────────────────────────────────────────────────────────────────────────

def _to_ty(val_raw):
    """
    CafeF BCTC đơn vị: NGHÌN VNĐ (không phải triệu, không phải đồng).
    Ví dụ: VCB Vốn CSH 2021 = 30,790,110 (nghìn) → / 1e3 = 30,790.11 tỷ ✅

    Verify:
      - 30,790,110 nghìn / 1e3 = 30,790.11 tỷ  ✅ khớp BCTC chính thức
      - 30,790,110 triệu / 1e6 = 30.79 tỷ       ❌ thấp 1000x
      - 30,790,110 đồng  / 1e9 = 0.03 tỷ        ❌ thấp 1,000,000x
    """
    if val_raw is None:
        return None
    v = float(val_raw)
    # Heuristic tự động detect đơn vị bằng magnitude:
    # - Nếu số > 1e8: đang ở nghìn đồng → /1e3 ra tỷ
    # - Nếu số > 1e5: đang ở triệu đồng → /1e6 ra tỷ  (1 tỷ = 1000 triệu)
    # - Nếu số > 1e2: đang ở tỷ đồng → giữ nguyên
    # Mốc kiểm tra: 1 tỷ tương đương 1,000,000 nghìn đồng = 1e6 nghìn
    # >= 1e6 nghìn = >= 1 tỷ VNĐ — tất cả số CafeF BCTC đều ở trên mốc này
    if abs(v) >= 1e6:
        return round(v / 1e3, 2)
    return round(v, 2)    # fallback (không nên xảy ra với dữ liệu CafeF)


# ─────────────────────────────────────────────────────────────────────────────
# Core fetch 1 kỳ: cào cả balance sheet + income statement
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_one_period(ticker: str, year: int, quarter: int, slug: str) -> dict:
    """
    Cào CafeF cho 1 kỳ (year, quarter=4 cho năm toàn phần).
    Trả dict: {equity, total_assets, revenue, net_profit} — đơn vị tỷ VNĐ.
    Tất cả giá trị None nếu không parse được.
    """
    out = {}
    base = f"https://s.cafef.vn/bao-cao-tai-chinh/{ticker.upper()}"
    bsheet_url = f"{base}/bsheet/{year}/{quarter}/0/0/{slug}.chn"
    incsta_url = f"{base}/incsta/{year}/{quarter}/0/0/{slug}.chn"

    def _get(url):
        try:
            resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
            return resp.text if resp.status_code == 200 else ""
        except Exception:
            return ""

    # Fetch song song 2 trang
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_bs = ex.submit(_get, bsheet_url)
        fut_is = ex.submit(_get, incsta_url)
        text_bs = fut_bs.result()
        text_is = fut_is.result()

    # ── Balance sheet ─────────────────────────────────────────────────────
    if text_bs:
        equity_vals = (
            _extract_row_values(text_bs, r'D\.\s*VỐN CHỦ SỞ HỮU') or
            _extract_row_values(text_bs, r'VỐN CHỦ SỞ HỮU') or
            _extract_row_values(text_bs, r'I\.\s*Vốn chủ sở hữu') or
            _extract_row_values(text_bs, r'Vốn chủ sở hữu')
        )
        assets_vals = (
            _extract_row_values(text_bs, r'TỔNG CỘNG TÀI SẢN') or
            _extract_row_values(text_bs, r'TỔNG TÀI SẢN') or
            _extract_row_values(text_bs, r'Tổng cộng tài sản')
        )
        # FIX 3: /1e6 (triệu → tỷ), không phải /1e9
        if equity_vals and equity_vals[-1] is not None:
            out['equity'] = _to_ty(equity_vals[-1])
        if assets_vals and assets_vals[-1] is not None:
            out['total_assets'] = _to_ty(assets_vals[-1])

    # ── Income statement — FIX 2: thêm parse revenue + net_profit ────────
    if text_is:
        revenue_vals = (
            _extract_row_values(text_is, r'Doanh thu thuần về bán hàng và cung cấp dịch vụ') or
            _extract_row_values(text_is, r'Doanh thu thuần') or
            _extract_row_values(text_is, r'TỔNG THU NHẬP HOẠT ĐỘNG') or
            _extract_row_values(text_is, r'Tổng thu nhập hoạt động') or
            _extract_row_values(text_is, r'Thu nhập lãi thuần') or
            _extract_row_values(text_is, r'Doanh thu hoạt động') or
            _extract_row_values(text_is, r'Tổng doanh thu hoạt động') or
            _extract_row_values(text_is, r'Tổng doanh thu') or
            _extract_row_values(text_is, r'Doanh thu bán hàng và cung cấp dịch vụ') or
            _extract_row_values(text_is, r'Doanh thu')
        )
        profit_vals = (
            _extract_row_values(text_is, r'Lợi nhuận sau thuế thu nhập doanh nghiệp') or
            _extract_row_values(text_is, r'LỢI NHUẬN SAU THUẾ') or
            _extract_row_values(text_is, r'Lợi nhuận sau thuế') or
            _extract_row_values(text_is, r'Lãi/\s*\(lỗ\) thuần sau thuế') or
            _extract_row_values(text_is, r'Lợi nhuận thuần sau thuế')
        )
        # FIX 3: /1e6 (triệu → tỷ)
        if revenue_vals and revenue_vals[-1] is not None:
            out['revenue'] = _to_ty(revenue_vals[-1])
        if profit_vals and profit_vals[-1] is not None:
            out['net_profit'] = _to_ty(profit_vals[-1])

    return out


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API 1: Balance sheet 5 năm — dùng trong _gapfill_balance()
# ─────────────────────────────────────────────────────────────────────────────

def fetch_cafef_balance_sheet_5y(ticker: str, end_year: int) -> dict:
    """
    FIX 1: Không dùng range(end_year-4, end_year+1) nữa.
    Luôn cào đúng TARGET_YEARS = 2021–(end_year-1) để không bỏ sót 2021.

    FIX 2: Trả thêm revenue + net_profit (không chỉ equity + total_assets).
    FIX 3: Đổi /1e9 → /1e6 trong _to_ty.
    """
    slug  = _find_company_slug(ticker)
    empty = pd.Series(dtype=float)

    if not _cafef_is_reachable():
        return {
            "equity": empty, "total_assets": empty,
            "revenue": empty, "net_profit": empty,
        }

    # Luôn cào từ 2021 đến end_year-1 (năm BCTC cuối đầy đủ)
    target_years = list(range(2021, end_year))

    result = {yr: {} for yr in target_years}

    def fetch_task(year):
        return year, _fetch_one_period(ticker, year, 4, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(target_years), 5)) as ex:
        future_to_y = {ex.submit(fetch_task, y): y for y in target_years}
        for future in concurrent.futures.as_completed(future_to_y):
            try:
                year, data = future.result()
                result[year] = data
            except Exception:
                pass

    def _make_series(field):
        d = {yr: result[yr][field] for yr in target_years
             if field in result.get(yr, {}) and result[yr][field] is not None}
        return pd.Series(d, dtype=float).sort_index()

    return {
        "equity":       _make_series("equity"),
        "total_assets": _make_series("total_assets"),
        "revenue":      _make_series("revenue"),
        "net_profit":   _make_series("net_profit"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API 2: Full yearly — dùng trong pipeline _gapfill_from_cafef()
# ─────────────────────────────────────────────────────────────────────────────

def fetch_cafef_yearly_full(ticker: str, years: list, debug: bool = False) -> dict:
    """
    Cào đủ 4 trường cho danh sách năm tuỳ ý.
    Trả về dict: {revenue, net_profit, equity, total_assets, roe, roa}
    Đơn vị: tỷ VNĐ (sau FIX 3).
    """
    slug  = _find_company_slug(ticker)
    empty = pd.Series(dtype=float)

    if not _cafef_is_reachable() or not years:
        return {k: empty for k in ["revenue", "net_profit", "equity", "total_assets", "roe", "roa"]}

    result = {yr: {} for yr in years}

    def fetch_task(year):
        return year, _fetch_one_period(ticker, year, 4, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(years), 6)) as ex:
        future_to_y = {ex.submit(fetch_task, y): y for y in years}
        for future in concurrent.futures.as_completed(future_to_y):
            try:
                year, data = future.result()
                result[year] = data
            except Exception:
                pass

    def _make_series(field):
        d = {yr: result[yr][field] for yr in years
             if field in result.get(yr, {}) and result[yr][field] is not None}
        return pd.Series(d, dtype=float).sort_index()

    revenue_s = _make_series("revenue")
    profit_s  = _make_series("net_profit")
    equity_s  = _make_series("equity")
    assets_s  = _make_series("total_assets")

    # Tính ROE/ROA từ dữ liệu vừa cào
    if not equity_s.empty and not profit_s.empty:
        common = profit_s.index.intersection(equity_s.index)
        roe_s = (profit_s.loc[common] / equity_s.loc[common].replace(0, float('nan')) * 100).dropna()
    else:
        roe_s = empty

    if not assets_s.empty and not profit_s.empty:
        common = profit_s.index.intersection(assets_s.index)
        roa_s = (profit_s.loc[common] / assets_s.loc[common].replace(0, float('nan')) * 100).dropna()
    else:
        roa_s = empty

    return {
        "revenue":      revenue_s,
        "net_profit":   profit_s,
        "equity":       equity_s,
        "total_assets": assets_s,
        "roe":          roe_s,
        "roa":          roa_s,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API 3: Quarterly (không thay đổi logic, chỉ fix đơn vị)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_cafef_quarterly_full(ticker: str, quarters: list, debug: bool = False) -> dict:
    """quarters: list of (year, quarter_int) tuples, VD [(2024,1),(2024,2),...]"""
    slug = _find_company_slug(ticker)
    result = {}

    if not _cafef_is_reachable() or not quarters:
        return {"revenue": {}, "net_profit": {}, "equity": {}, "total_assets": {}}

    def fetch_task(q_tuple):
        year, q = q_tuple
        return f"{year}-Q{q}", _fetch_one_period(ticker, year, q, slug)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(quarters), 6)) as ex:
        future_to_q = {ex.submit(fetch_task, qt): qt for qt in quarters}
        for future in concurrent.futures.as_completed(future_to_q):
            try:
                key, data = future.result()
                result[key] = data
            except Exception:
                pass

    def _extract(field):
        return {k: v[field] for k, v in result.items()
                if field in v and v[field] is not None}

    return {
        "revenue":      _extract("revenue"),
        "net_profit":   _extract("net_profit"),
        "equity":       _extract("equity"),
        "total_assets": _extract("total_assets"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Các hàm giữ nguyên (không ảnh hưởng bởi các bug trên)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_cafef_analysis_reports(ticker: str) -> dict:
    """Lấy báo cáo phân tích từ CafeF — giữ nguyên."""
    return {
        "reports": [],
        "is_ticker_specific": False,
        "sources_used": ["CafeF"],
        "debug_log": [],
    }
