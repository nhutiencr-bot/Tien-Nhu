"""
pipeline_helpers.py
────────────────────
Các hằng số + hàm dùng chung (fetch nhiều nguồn, chuẩn hoá đơn vị, xây dựng
engine vnstock có fallback...) được tách ra từ equity_pipeline.py để file
chính đỡ cồng kềnh. equity_pipeline.py import các tên cần dùng từ đây.
"""
import pandas as pd
import numpy as np
# BYPASS vnai hard-cap 4 kỳ
# QUAN TRỌNG: Import Finance/Quote/Company TRƯỚC, rồi mới unpatch
# Lý do: vnai._ensure_patches_applied() chỉ chạy 1 lần (có guard).
# Nếu unpatch trước khi trigger guard, vnai sẽ re-patch lại sau đó.
from datetime import datetime, timedelta
from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company
# Trigger vnai patch để set guard _patches_initialized = True
try:
    import vnai as _vnai_init
    _vnai_init._ensure_patches_applied()
except Exception:
    pass
# Bây giờ mới unpatch — vnai sẽ không re-patch nữa (guard đã set)
from unpatch_vnai import apply_unpatch
apply_unpatch()

# ════════════════════════════════════════════════════════════════════════
# [ROOT-FIX] KHÔNG hardcode khoảng năm nữa.
# Bản cũ "khoá cứng" TABLE_START_YEAR/TABLE_END_YEAR = 2021/2025 (PATCH 1),
# trong khi equity_pipeline.py lại dùng datetime.today().year (giá trị
# ĐỘNG) để quyết định "năm nào đang là năm hiện tại, chưa có báo cáo đầy
# đủ". Hai nguồn "năm hiện tại" lệch nhau theo thời gian → đây là nguyên
# nhân gốc của lỗi doanh thu năm mới nhất bị sai/thiếu dù financial_normalizer.py
# đã sửa rất nhiều lần. Xem chi tiết trong comment [ROOT-FIX] ở đầu
# financial_normalizer.py.
#
# Từ giờ TARGET_YEAR/TARGET_YEARS chỉ định nghĩa Ở MỘT NƠI DUY NHẤT
# (financial_normalizer.py) và mọi nơi khác import lại từ đó.
# ════════════════════════════════════════════════════════════════════════
from financial_normalizer import TARGET_YEAR, TARGET_YEARS

TABLE_END_YEAR   = TARGET_YEAR
TABLE_START_YEAR = TARGET_YEAR - 4
ALLOWED_YEARS    = set(TARGET_YEARS)  # 5 năm gần nhất, tự cuốn chiếu theo TARGET_YEAR

# PATCH 2 — Fetch 7 năm để dự phòng, sau đó _filter_years() cắt về đúng khoảng
FETCH_LIMIT_YEAR = 7

# SOURCE_FALLBACK_ORDER: thứ tự thử nguồn khi nguồn chính fail
SOURCE_FALLBACK_ORDER = ['DNSE', 'KBS', 'VCI']

# Giữ lại tên cũ để không break code khác dùng DEFAULT_YEAR_LIMIT
DEFAULT_YEAR_LIMIT = 5


# Chuẩn hoá đơn vị: dùng chung một bản duy nhất trong financials_core.py
from financials_core import (  # noqa: E402,F401
    normalize_to_billion_vnd,
    normalize_pct_series as _normalize_pct_series,
    normalize_net_profit_with_anchor,
)

# Mỗi mục: (tên hàm của Finance, period, limit)
_BUNDLE_SPEC = {
    'income_y':   ('income_statement', 'year',    FETCH_LIMIT_YEAR),
    'balance_y':  ('balance_sheet',    'year',    FETCH_LIMIT_YEAR),
    'ratio_y':    ('ratio',            'year',    FETCH_LIMIT_YEAR),
    'cashflow_y': ('cash_flow',        'year',    FETCH_LIMIT_YEAR),
}


def _fetch_one_statement(ticker, source, which, period, limit):
    try:
        f = Finance(symbol=ticker, source=source, period=period)
        fn = getattr(f, which)
        try:
            df = fn(period=period, limit=limit)
        except TypeError:
            df = fn(period=period)
        return df if df is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def fetch_source_bundle(ticker, source):
    """
    Lấy các báo cáo NĂM (KQKD, CĐKT, chỉ số, LCTT) từ MỘT nguồn, song song. Mỗi nguồn là một
    bó nguyên vẹn; việc ghép nguồn (có kiểm chứng) làm ở financials_core.merge_cores.
    """
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(_BUNDLE_SPEC)) as ex:
        futs = {k: ex.submit(_fetch_one_statement, ticker, source, *spec)
                for k, spec in _BUNDLE_SPEC.items()}
        return {k: f.result() for k, f in futs.items()}


def _build_engines_with_fallback(ticker):
    last_error = None
    test_end   = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')
    for source in SOURCE_FALLBACK_ORDER:
        try:
            q_engine = Quote(symbol=ticker, source=source)
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError(f"Nguồn {source} trả về dữ liệu rỗng cho {ticker}")
            f_engine = Finance(symbol=ticker, source=source, period='year')
            c_engine = Company(symbol=ticker, source=source)
            return q_engine, f_engine, c_engine, source
        except Exception as e:
            last_error = e
            continue
    raise ConnectionError(
        f"Không lấy được dữ liệu cho mã {ticker} từ bất kỳ nguồn nào "
        f"({', '.join(SOURCE_FALLBACK_ORDER)}). Lỗi cuối cùng: {last_error}"
    )


def _safe_fetch(fn, default=None):
    try:
        result = fn()
        return result if result is not None else (default if default is not None else pd.DataFrame())
    except Exception:
        return default if default is not None else pd.DataFrame()


def _build_shares_series(outstanding_shares_series, net_profit_series, eps_series):
    """
    PATCH 4 — Trả về Series số CP lưu hành (đơn vị: cổ phiếu lẻ) theo từng năm.
    Tầng 1: từ ratio API (outstanding_shares_series).
    Tầng 2: back-calc LNST(tỷ)*1e9 / EPS(đ) cho từng năm còn thiếu.

    ⚠️ vnstock ratio() trả 'Số CP lưu hành' theo đơn vị TRIỆU CP (vd: 3610 = 3.61 tỷ).
    Guard: nếu median < 1e8 → nhân 1e6 để ra đơn vị cổ phiếu lẻ.
    """
    result = {}
    if outstanding_shares_series is not None and not outstanding_shares_series.empty:
        _sh = outstanding_shares_series.dropna()
        _median_sh = _sh.median() if not _sh.empty else 0
        # Nếu đơn vị triệu (median < 1e8) → nhân 1e6 → đơn vị lẻ
        _sh_multiplier = 1e6 if (0 < _median_sh < 1e8) else 1.0
        for yr, val in outstanding_shares_series.items():
            if pd.notna(val) and val > 0:
                result[yr] = float(val) * _sh_multiplier
    if net_profit_series is not None and not net_profit_series.empty \
            and eps_series is not None and not eps_series.empty:
        for yr in net_profit_series.index:
            if yr in result:
                continue
            np_ty = net_profit_series.get(yr)
            eps_d = eps_series.get(yr)
            if (np_ty is not None and eps_d is not None
                    and pd.notna(np_ty) and pd.notna(eps_d)
                    and eps_d > 0 and np_ty > 0):
                backcalc = (np_ty * 1e9) / eps_d
                if 1e8 < backcalc < 1e11:
                    result[yr] = backcalc
    if not result:
        return pd.Series(dtype=float)
    return pd.Series(result, dtype=float).sort_index()
