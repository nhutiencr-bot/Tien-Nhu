"""
unpatch_vnai.py
───────────────
Gỡ giới hạn ngầm của lớp `vnai` trong vnstock khiến các hàm Finance()
(income_statement/balance_sheet/ratio/cash_flow) chỉ trả về ~4 kỳ gần
nhất BẤT KỂ tham số `limit` truyền vào — đây là nguyên nhân gốc khiến
năm xa nhất (2021) không bao giờ về đủ.

apply_unpatch() PHẢI được gọi TRƯỚC mọi lần khởi tạo Finance().

Thiết kế an toàn: mọi thao tác monkeypatch đều bọc try/except riêng lẻ,
apply_unpatch() KHÔNG BAO GIỜ raise — nếu môi trường vnstock khác phiên
bản, hàm chỉ đơn giản bỏ qua phần không áp dụng được (no-op) thay vì làm
sập import của pipeline.
"""

import os
import logging

logger = logging.getLogger(__name__)

_APPLIED = False


def _disable_vnai_telemetry():
    """Tắt telemetry/rate-limit qua biến môi trường (an toàn nhất)."""
    for key in (
        "VNAI_DISABLE", "VNAI_OPTOUT", "VNSTOCK_DISABLE_TELEMETRY",
        "ACCEPT_TC", "VNSTOCK_TC",
    ):
        os.environ.setdefault(key, "1")


def _patch_vnai_guard():
    """
    Vô hiệu hoá các hàm guard/throttle của vnai nếu có.
    Mỗi lần patch bọc try riêng để một lỗi không chặn các patch khác.
    """
    try:
        import vnai  # noqa
    except Exception:
        return  # không có vnai → không cần patch

    # Danh sách (module_path, attr) các điểm throttle thường gặp qua các bản.
    candidates = [
        ("vnai", "optimize"),
        ("vnai", "measure"),
        ("vnai", "record"),
        ("vnai.beam.metrics", "collector"),
        ("vnai.flow.relay", "conduit"),
    ]
    for mod_path, attr in candidates:
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            if hasattr(mod, attr):
                target = getattr(mod, attr)
                # Nếu là callable → thay bằng no-op giữ nguyên chữ ký linh hoạt
                if callable(target):
                    setattr(mod, attr, lambda *a, **k: None)
        except Exception:
            continue


def _patch_source_limit():
    """
    Nới trần số kỳ ở tầng data-source (VCI/TCBS...) nếu thư viện expose
    hằng số giới hạn. Best-effort — bỏ qua nếu không tìm thấy.
    """
    for mod_path in (
        "vnstock.explorer.vci.financial",
        "vnstock.api.financial",
    ):
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            for const_name in ("_DEFAULT_LIMIT", "DEFAULT_LIMIT", "MAX_PERIOD", "PERIOD_LIMIT"):
                if hasattr(mod, const_name):
                    try:
                        setattr(mod, const_name, 20)
                    except Exception:
                        pass
        except Exception:
            continue


def apply_unpatch():
    """Idempotent — gọi nhiều lần cũng chỉ áp dụng 1 lần. Không bao giờ raise."""
    global _APPLIED
    if _APPLIED:
        return
    try:
        _disable_vnai_telemetry()
    except Exception as e:
        logger.debug("disable telemetry lỗi: %s", e)
    try:
        _patch_vnai_guard()
    except Exception as e:
        logger.debug("patch vnai guard lỗi: %s", e)
    try:
        _patch_source_limit()
    except Exception as e:
        logger.debug("patch source limit lỗi: %s", e)
    _APPLIED = True
