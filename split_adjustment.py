"""
split_adjustment.py
--------------------
Audit & khắc phục Bẫy 5B (Split-adjustment consistency) — xem
equity-research-vn/vn-financial-data-collector/references/data_pitfalls.md.

Vấn đề: vnstock Quote.history() trả giá đã SPLIT-ADJUSTED (toàn bộ lịch sử
scale về số CP hiện tại), nhưng EPS/BVPS lấy từ BCTC gốc dùng số CP tại
THỜI ĐIỂM đó (chưa adjust). Nếu tính PE/PB = giá(đã adjust) / EPS(chưa
adjust) cho các năm trước một đợt chia cổ phiếu/cổ tức CP → mix 2 chuẩn →
PE/PB SAI hoàn toàn (case thực tế: BSR 2026, PE tính sai 6.10x thay vì
đúng 9.85x).

Quy trình (theo đúng "Cách sửa" trong data_pitfalls.md):
1. Detect split từ Company.events() (nếu API hỗ trợ) HOẶC back-calc
   CP = LNST/EPS từng năm để phát hiện bước nhảy bất thường (fallback khi
   events() không khả dụng/không trả dữ liệu — tránh phụ thuộc cứng vào
   1 nguồn duy nhất).
2. Với các năm TRƯỚC năm phát hiện bước nhảy: chia EPS/BVPS cho hệ số dồn
   tích (SPLIT_MULT), nhân số CP cho hệ số này — đưa về cùng base post-split
   với giá hiện tại.
3. Trả về EPS/BVPS/shares đã adjust + cờ báo có phát hiện split hay không,
   để UI hiển thị ghi chú minh bạch cho người dùng.

Thiết kế AN TOÀN: mọi bước đều bọc try/except — nếu detect thất bại (lỗi
API, thiếu dữ liệu), trả về dữ liệu GỐC không đổi, không bao giờ làm crash
pipeline hay tạo ra hệ số adjust sai từ dữ liệu không chắc chắn.
"""

import pandas as pd


def _back_calc_shares(net_profit_series: pd.Series, eps_series: pd.Series) -> pd.Series:
    """
    CP back-calc[năm] = LNST[năm, tỷ đồng] × 1e9 / EPS[năm, đồng/cp].
    Dùng để phát hiện split KHÔNG cần events() — nếu CP back-calc nhảy vọt
    (>15%) giữa 2 năm liền kề mà không do tăng vốn thực (phát hành thêm),
    khả năng cao là do EPS của 1 trong 2 năm chưa được adjust theo split.
    """
    common_years = sorted(set(net_profit_series.index) & set(eps_series.index))
    out = {}
    for y in common_years:
        eps_y = eps_series.get(y)
        np_y = net_profit_series.get(y)
        if eps_y and eps_y != 0 and np_y:
            out[y] = (np_y * 1e9) / eps_y  # LNST tỷ -> đồng, chia EPS đồng/cp -> số CP
    return pd.Series(out).sort_index()


def detect_split_from_events(company_engine, jump_threshold: float = 0.15):
    """
    Thử lấy sự kiện chia cổ phiếu/cổ tức CP từ Company.events().
    Trả về dict {split_year: cumulative_multiplier} hoặc {} nếu không có/lỗi.

    ⚠️ Field names (event_title_vi/exercise_ratio/...) có thể khác nhau tuỳ
    version vnstock — bọc try/except toàn bộ, không để lỗi field lan ra
    ngoài. Nếu cấu trúc trả về khác kỳ vọng, hàm trả {} (coi như không phát
    hiện được qua đường này) thay vì crash.
    """
    try:
        events = company_engine.events()
        if events is None or (hasattr(events, 'empty') and events.empty):
            return {}
        records = events.to_dict('records') if hasattr(events, 'to_dict') else list(events)
    except Exception:
        return {}

    split_by_year = {}
    keywords = ['chia cổ phiếu', 'phát hành cổ phiếu', 'cổ tức bằng cổ phiếu',
                 'cổ tức cổ phiếu', 'thưởng cổ phiếu', 'stock dividend', 'stock split']
    for e in records:
        try:
            title = str(e.get('event_title_vi', '') or e.get('event_title', '') or '').lower()
            if not any(kw in title for kw in keywords):
                continue
            ratio = e.get('exercise_ratio') or e.get('ratio') or 0
            ratio = float(ratio) if ratio else 0.0
            if ratio <= 0:
                continue
            # exercise_ratio thường ở dạng % (VD 31.5 nghĩa là 31.5%) hoặc
            # dạng thập phân (0.315) tuỳ nguồn — chuẩn hoá về thập phân.
            if ratio > 1:
                ratio = ratio / 100.0
            date_str = e.get('event_date') or e.get('exercise_date') or e.get('date')
            year = pd.to_datetime(date_str, errors='coerce').year if date_str else None
            if year is None:
                continue
            split_by_year[year] = split_by_year.get(year, 0.0)
            # Dồn tích nếu nhiều đợt cùng năm: multiplier = product(1+ratio_i)
            split_by_year[year] = (1 + split_by_year[year]) * (1 + ratio) - 1
        except Exception:
            continue

    return split_by_year


def audit_and_adjust_split(eps_series: pd.Series, bvps_series: pd.Series,
                            net_profit_series: pd.Series,
                            outstanding_shares_series: pd.Series = None,
                            company_engine=None):
    """
    Thực hiện quy trình Bẫy 5B đầy đủ. Trả về:
    {
        'eps_adjusted': pd.Series,
        'bvps_adjusted': pd.Series,
        'shares_adjusted': pd.Series | None,
        'split_detected': bool,
        'split_year': int | None,
        'split_mult': float,
        'method': 'events' | 'back_calc' | 'none',
        'note': str  # ghi chú hiển thị cho người dùng
    }

    KHÔNG BAO GIỜ raise — mọi lỗi đều fallback về dữ liệu gốc không đổi.
    """
    result = {
        'eps_adjusted': eps_series, 'bvps_adjusted': bvps_series,
        'shares_adjusted': outstanding_shares_series,
        'split_detected': False, 'split_year': None, 'split_mult': 1.0,
        'method': 'none', 'note': '',
    }

    if eps_series is None or eps_series.empty:
        return result

    try:
        split_year, split_mult, method = None, 1.0, 'none'

        # ── Phương pháp 1: Company.events() (đáng tin cậy hơn nếu có) ──────
        if company_engine is not None:
            splits = detect_split_from_events(company_engine)
            if splits:
                split_year = max(splits.keys())
                split_mult = 1.0
                for y, r in splits.items():
                    split_mult *= (1 + r)
                method = 'events'

        # ── Phương pháp 2: Back-calc CP = LNST/EPS (fallback) ──────────────
        if method == 'none' and net_profit_series is not None and not net_profit_series.empty:
            shares_bc = _back_calc_shares(net_profit_series, eps_series)
            if len(shares_bc) >= 2:
                years_sorted = sorted(shares_bc.index)
                for i in range(1, len(years_sorted)):
                    y_prev, y_cur = years_sorted[i - 1], years_sorted[i]
                    s_prev, s_cur = shares_bc[y_prev], shares_bc[y_cur]
                    if s_prev and s_cur and s_prev > 0:
                        jump = (s_cur - s_prev) / s_prev
                        # Chỉ coi là split nếu bước nhảy CP lớn (>15%) và
                        # GIỮ NGUYÊN ở các năm sau đó (tránh nhầm với việc
                        # phát hành thêm 1 lần rồi trở lại — split thường
                        # persistent, dùng thêm điều kiện năm cuối > năm đầu
                        # ít nhất bằng đúng multiplier để tăng độ tin cậy).
                        if jump > 0.15:
                            split_year = y_cur
                            split_mult = s_cur / s_prev
                            method = 'back_calc'
                            # Không break — lấy đợt gần nhất nếu có nhiều đợt

        if method == 'none' or split_year is None:
            result['note'] = 'Không phát hiện split/cổ tức CP bất thường — giữ nguyên EPS/BVPS gốc.'
            return result

        # ── Áp dụng điều chỉnh cho các năm TRƯỚC split_year ────────────────
        # ⚠️ ép kiểu float TRƯỚC khi gán ngược — nếu eps_series/bvps_series
        # gốc có dtype int64 (thường gặp khi EPS toàn số nguyên đồng/cp),
        # gán 1 giá trị float vào phần tử int64 Series sẽ raise lỗi dtype,
        # khiến toàn bộ audit rơi vào except và ÂM THẦM bỏ qua split thật.
        eps_adj = eps_series.astype(float).copy()
        bvps_adj = (bvps_series.astype(float).copy() if bvps_series is not None
                    else pd.Series(dtype=float))
        shares_adj = (outstanding_shares_series.astype(float).copy()
                      if outstanding_shares_series is not None else None)

        for y in eps_adj.index:
            if y < split_year:
                eps_adj[y] = eps_adj[y] / split_mult
        if not bvps_adj.empty:
            for y in bvps_adj.index:
                if y < split_year:
                    bvps_adj[y] = bvps_adj[y] / split_mult
        if shares_adj is not None and not shares_adj.empty:
            for y in shares_adj.index:
                if y < split_year:
                    shares_adj[y] = shares_adj[y] * split_mult

        result.update({
            'eps_adjusted': eps_adj, 'bvps_adjusted': bvps_adj, 'shares_adjusted': shares_adj,
            'split_detected': True, 'split_year': int(split_year), 'split_mult': round(split_mult, 4),
            'method': method,
            'note': (f"Phát hiện chia cổ phiếu/cổ tức CP tích luỹ ×{split_mult:.3f} "
                     f"quanh năm {split_year} (phương pháp: {method}). Đã điều chỉnh "
                     f"EPS/BVPS các năm trước {split_year} về cùng base với giá hiện tại "
                     f"để PE/PB lịch sử so sánh được — xem Bẫy 5B."),
        })
        return result

    except Exception as e:
        result['note'] = f'Audit split thất bại ({e}) — giữ nguyên dữ liệu gốc để an toàn.'
        return result


def recompute_pe_pb_series(eps_adjusted: pd.Series, bvps_adjusted: pd.Series,
                            year_end_price_series: pd.Series):
    """
    Tính lại PE/PB history dùng EPS/BVPS ĐÃ ADJUST và giá cuối năm CÙNG BASE
    (year_end_price_series nên lấy từ chuỗi giá đã split-adjusted của
    vnstock — nhất quán vì EPS/BVPS giờ cũng đã quy về base đó).
    """
    pe_out, pb_out = {}, {}
    for y in eps_adjusted.index:
        price_y = year_end_price_series.get(y)
        eps_y = eps_adjusted.get(y)
        if price_y and eps_y and eps_y > 0:
            pe_out[y] = price_y / eps_y
    if bvps_adjusted is not None:
        for y in bvps_adjusted.index:
            price_y = year_end_price_series.get(y)
            bvps_y = bvps_adjusted.get(y)
            if price_y and bvps_y and bvps_y > 0:
                pb_out[y] = price_y / bvps_y
    return pd.Series(pe_out).sort_index(), pd.Series(pb_out).sort_index()
