"""
unpatch_vnai.py
---------------
Bypass vnai beam patching — khôi phục VCI/KBS Finance về không bị limit 4 kỳ.

ROOT CAUSE đã xác nhận:
  vnai.beam.patching.py (guest tier) hard-cap 4 kỳ gần nhất = chỉ 2022-2025.
  Năm 2021 bị cắt hoàn toàn trước khi pipeline nhận DataFrame.

CÁCH DÙNG trong pipeline.py:
  # Thêm vào đầu file pipeline.py, trước mọi import vnstock khác:
  from unpatch_vnai import apply_unpatch
  apply_unpatch()

Hàm apply_unpatch() phải được gọi SAU khi vnstock đã import lần đầu
(vì vnai patch xảy ra lúc import). Pipeline.py có thể gọi lần 2 mà không sao
(idempotent — đã có guard _unpatched).
"""

from __future__ import annotations
from typing import Optional
import pandas as pd

_unpatched = False


def apply_unpatch() -> bool:
    """
    Override VCI Finance methods để bỏ hard-cap 4 kỳ của vnai.
    Gọi 1 lần; lần sau là no-op (idempotent).
    Returns True nếu thành công.
    """
    global _unpatched
    if _unpatched:
        return True

    success = False

    # ── VCI ────────────────────────────────────────────────────────────────
    try:
        from vnstock.explorer.vci.financial import Finance as VCI_Finance

        def _make_method(report_name: str):
            def _unlimited(
                self,
                period: Optional[str] = None,
                lang: Optional[str] = 'vi',
                dropna: Optional[bool] = True,
                show_log: Optional[bool] = False,
                limit: Optional[int] = None,
            ) -> pd.DataFrame:
                # Gọi thẳng _get_financial_report — bỏ qua wrapper limit của vnai
                eff_limit = limit if (limit and limit > 4) else 10
                try:
                    df = self._get_financial_report(
                        report_name,
                        period=period or getattr(self, 'period', 'year'),
                        lang=lang,
                        dropna=dropna,
                        show_log=show_log,
                        limit=eff_limit,
                    )
                    return df if df is not None else pd.DataFrame()
                except Exception:
                    return pd.DataFrame()
            _unlimited.__name__ = report_name
            _unlimited.__qualname__ = f'Finance.{report_name}_unlimited'
            return _unlimited

        VCI_Finance.balance_sheet    = _make_method('balance_sheet')
        VCI_Finance.income_statement = _make_method('income_statement')
        VCI_Finance.cash_flow        = _make_method('cash_flow')
        VCI_Finance.ratio            = _make_method('ratio')
        success = True
        print("[unpatch_vnai] ✅ VCI Finance: đã bỏ cap 4 kỳ.")

    except Exception as e:
        print(f"[unpatch_vnai] ⚠️ VCI unpatch failed: {e}")

    # ── KBS ────────────────────────────────────────────────────────────────
    # KBS dùng pagination nội bộ — chỉ cần đảm bảo wrapper limit bị remove
    try:
        from vnstock.explorer.kbs.financial import Finance as KBS_Finance
        from vnai.beam.patching import limit_periods_by_columns

        # Lưu reference method gốc (chưa bị wrap lần 2)
        _kbs_bs_orig = KBS_Finance.__dict__.get('balance_sheet')
        _kbs_is_orig = KBS_Finance.__dict__.get('income_statement')
        _kbs_cf_orig = KBS_Finance.__dict__.get('cash_flow')

        # Nếu method đã bị patch (tên có 'with_limit') thì restore bằng cách
        # gọi original method mà không apply limit_periods_by_columns
        def _make_kbs_method(orig_method):
            def _unlimited_kbs(self, period=None, show_log=False):
                try:
                    df = orig_method(self, period=period, show_log=show_log)
                    # Trả về TOÀN BỘ — không cắt limit
                    return df if df is not None else pd.DataFrame()
                except Exception:
                    return pd.DataFrame()
            return _unlimited_kbs

        # Chỉ override nếu method đang bị wrap bởi vnai
        for attr, orig in [
            ('balance_sheet',    _kbs_bs_orig),
            ('income_statement', _kbs_is_orig),
            ('cash_flow',        _kbs_cf_orig),
        ]:
            if orig is not None and 'with_limit' in getattr(orig, '__name__', ''):
                setattr(KBS_Finance, attr, _make_kbs_method(orig))

        print("[unpatch_vnai] ✅ KBS Finance: đã bỏ cap 4 kỳ.")

    except Exception as e:
        print(f"[unpatch_vnai] ⚠️ KBS unpatch failed (non-critical): {e}")

    _unpatched = True
    return success
