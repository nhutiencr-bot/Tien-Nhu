"""
data_fetcher_fixed.py
---------------------
BẢN VÁ LỖI TOÀN DIỆN — phiên bản thay thế data_fetcher.py

Các lỗi đã sửa:
  1. Truyền ticker vào build_5y_financial_table() để detect ngành đúng
  2. _fetch_cafef() parse cột năm chịu được header dạng "Năm 2021", "2021 (tỷ đồng)"
  3. _to_ty() thêm tham số unit_hint để tránh nhận sai đơn vị KBS/DNSE
  4. _fetch_vnstock() filter giữ lại đúng năm 2021–2025
  5. Thêm RETAIL_TICKERS detect + keyword doanh thu bán lẻ đặc thù
  6. VRE/BĐS: fallback keyword "doanh thu cho thuê"
  7. Sàn HNX/UPCoM: thử thêm suffix .HN cho yfinance
  8. _fetch_cafef_v2(): URL chuẩn mới + regex cột linh hoạt
"""

import re
import time
import requests
import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────
# CONSTANTS — ngành đặc thù
# ─────────────────────────────────────────────────────────────

RETAIL_TICKERS = {
    # Bán lẻ điện máy / FMCG / phân phối
    'MWG', 'FRT', 'DGW', 'PNJ', 'HAX', 'SVC', 'MCH', 'PET',
    'PSD', 'HHS', 'HUT', 'VRE', 'AST', 'PTC', 'CEO', 'PDR',
}

REAL_ESTATE_TICKERS = {'VRE', 'NLG', 'DXG', 'KDH', 'PDR', 'CEO', 'BCM'}

BANK_TICKERS = {
    'VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB', 'VPB', 'HDB', 'TPB',
    'MSB', 'OCB', 'VIB', 'SHB', 'EIB', 'LPB', 'SSB', 'NAB', 'ABB', 'BAB',
}

TARGET_YEARS = list(range(2021, 2026))  # 2021–2025

# ─────────────────────────────────────────────────────────────
# HELPER: parse số từ string bất kỳ
# ─────────────────────────────────────────────────────────────

def _parse_num(s):
    if s is None:
        return None
    s = str(s).strip().replace('\xa0', '').replace(' ', '')
    s = re.sub(r'[^\d.,\-]', '', s)
    if not s or s in ['-', '.', ',']:
        return None
    if '.' in s and ',' in s:
        if s.index('.') < s.index(','):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        parts = s.split(',')
        if len(parts[-1]) == 3:
            s = s.replace(',', '')
        else:
            s = s.replace(',', '.')
    try:
        return float(s)
    except Exception:
        return None


def _to_ty(val, unit_hint=None):
    """
    Chuẩn hoá về đơn vị tỷ VNĐ.
    unit_hint: 'dong', 'trieu', 'ty' — nếu biết trước thì dùng, không cần guess.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    val = float(val)
    if unit_hint == 'dong':
        return round(val / 1e9, 2)
    if unit_hint == 'trieu':
        return round(val / 1e3, 2)
    if unit_hint == 'ty':
        return round(val, 2)
    # Auto-detect (heuristic)
    if abs(val) > 1e10:          # > 10 tỷ đồng → đang ở đồng
        return round(val / 1e9, 2)
    if abs(val) > 1e7:           # > 10 triệu đồng → đang ở triệu
        return round(val / 1e3, 2)
    return round(val, 2)         # đã ở tỷ


def _filter_years(series: pd.Series, years=None) -> pd.Series:
    """Lọc chỉ giữ các năm trong target (default 2021–2025)."""
    if series is None or series.empty:
        return series
    target = years or TARGET_YEARS
    keep = [y for y in series.index if y in target]
    return series.loc[keep].sort_index() if keep else pd.Series(dtype=float)


# ─────────────────────────────────────────────────────────────
# NGUỒN 1: vnstock (VCI / KBS / DNSE)
# ─────────────────────────────────────────────────────────────

def _fetch_vnstock(ticker):
    try:
        from vnstock.api.financial import Finance
        from financial_normalizer import build_financial_table

        YEARS = list(range(2021, 2026))  # 2021-2025

        inc_y, bal_y, rat_y = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        for source in ['VCI', 'KBS', 'DNSE']:
            try:
                f = Finance(symbol=ticker, source=source)

                # ── Tầng 1: Annual (nhẹ nhất, 1 request/bảng) ──
                if inc_y.empty:
                    try:
                        df = f.income_statement(period='year', lang='vi')
                        if df is not None and not df.empty:
                            inc_y = df
                    except Exception:
                        pass

                if bal_y.empty:
                    try:
                        df = f.balance_sheet(period='year', lang='vi')
                        if df is not None and not df.empty:
                            bal_y = df
                    except Exception:
                        pass

                if rat_y.empty:
                    try:
                        df = f.ratio(period='year', lang='vi')
                        if df is not None and not df.empty:
                            rat_y = df
                    except Exception:
                        pass

                # Nếu đủ cả 3 thì dừng, không thử source tiếp
                if not inc_y.empty and not bal_y.empty and not rat_y.empty:
                    break

            except Exception:
                continue

        # ── Tầng 2: Quarterly fallback — chỉ dùng nếu annual thiếu năm ──
        # vnstock quarterly có gap 2023-2024, nhưng có 2025-Q3/Q4
        # → dùng để ghép 2025 mà annual chưa có
        def _agg_quarterly_to_annual(df_q, needed_years):
            """Cộng 4 quý thành năm. Chỉ cho income/cashflow (flow items)."""
            if df_q is None or df_q.empty:
                return pd.DataFrame()
            q_cols = [c for c in df_q.columns
                      if re.fullmatch(r'\d{4}-Q[1-4]', str(c).strip())]
            result_cols = {}
            for yr in needed_years:
                yr_q_cols = [c for c in q_cols if str(c).startswith(str(yr))]
                if len(yr_q_cols) == 4:  # đủ 4 quý mới cộng
                    result_cols[yr] = df_q[yr_q_cols].sum(axis=1, skipna=True)
                elif len(yr_q_cols) >= 1:
                    # Partial year — lấy Q4 làm proxy nếu có (balance sheet)
                    q4_col = f"{yr}-Q4"
                    if q4_col in yr_q_cols:
                        result_cols[yr] = df_q[q4_col]
            if not result_cols:
                return pd.DataFrame()
            meta = df_q[[c for c in ['item', 'item_en', 'item_id'] if c in df_q.columns]]
            return pd.concat([meta, pd.DataFrame(result_cols, index=df_q.index)], axis=1)

        # Kiểm tra năm nào còn thiếu trong annual
        def _missing_years(df, years):
            if df is None or df.empty:
                return years
            year_cols = [c for c in df.columns
                         if re.fullmatch(r'\d{4}', str(c).strip())]
            have = {int(c) for c in year_cols}
            return [y for y in years if y not in have]

        missing_inc = _missing_years(inc_y, YEARS)
        missing_bal = _missing_years(bal_y, YEARS)

        if missing_inc or missing_bal:
            for source in ['VCI', 'KBS']:
                try:
                    f = Finance(symbol=ticker, source=source)
                    if missing_inc:
                        try:
                            df_q = f.income_statement(period='quarter', lang='vi')
                            if df_q is not None and not df_q.empty:
                                df_agg = _agg_quarterly_to_annual(df_q, missing_inc)
                                if not df_agg.empty:
                                    # Merge vào inc_y
                                    new_cols = [c for c in df_agg.columns
                                                if str(c).strip().isdigit()]
                                    for col in new_cols:
                                        if str(col) not in [str(c) for c in inc_y.columns]:
                                            inc_y = pd.concat([inc_y, df_agg[[col]]], axis=1) \
                                                if not inc_y.empty else df_agg
                        except Exception:
                            pass
                    if missing_bal:
                        try:
                            df_q = f.balance_sheet(period='quarter', lang='vi')
                            if df_q is not None and not df_q.empty:
                                # Balance sheet: dùng Q4 (snapshot cuối năm)
                                df_agg = _agg_quarterly_to_annual(df_q, missing_bal)
                                if not df_agg.empty:
                                    new_cols = [c for c in df_agg.columns
                                                if str(c).strip().isdigit()]
                                    for col in new_cols:
                                        if str(col) not in [str(c) for c in bal_y.columns]:
                                            bal_y = pd.concat([bal_y, df_agg[[col]]], axis=1) \
                                                if not bal_y.empty else df_agg
                        except Exception:
                            pass
                    break
                except Exception:
                    continue

        # ── Build bảng tài chính ──
        fin = build_financial_table(inc_y, bal_y, rat_y, ticker=ticker, period='year')

        def _to_ty_series(s):
            if s is None or s.empty:
                return s
            return s.map(_to_ty).dropna()

        out = {}
        for k in ['revenue', 'net_profit', 'equity', 'total_assets']:
            raw = fin.get(k, pd.Series(dtype=float))
            out[k] = _to_ty_series(raw) if raw is not None else pd.Series(dtype=float)
            # Lọc chỉ 2021-2025
            if not out[k].empty:
                out[k] = out[k][out[k].index.isin(YEARS)]

        for k in ['eps', 'bvps', 'roe', 'roa']:
            raw = fin.get(k, pd.Series(dtype=float))
            out[k] = raw[raw.index.isin(YEARS)] if raw is not None and not raw.empty \
                     else pd.Series(dtype=float)

        out['_source'] = 'vnstock'
        return out

    except Exception as e:
        return None

# ─────────────────────────────────────────────────────────────
# NGUỒN 2: CafeF scrape — phiên bản vá lỗi
# ─────────────────────────────────────────────────────────────

def _parse_year_from_col(col_str: str) -> int | None:
    """
    ✅ FIX 3: Nhận diện linh hoạt cột năm từ CafeF.
    Xử lý các dạng: '2021', 'Năm 2021', '2021 (tỷ đồng)', 'FY2021', '31/12/2021'
    """
    col_str = str(col_str).strip()
    m = re.search(r'(20\d{2})', col_str)
    if m:
        return int(m.group(1))
    return None


def _extract_cafef_series(df: pd.DataFrame, keywords, exclude=None) -> pd.Series:
    """
    Dò dòng theo keyword trong cột đầu tiên của bảng CafeF.
    Cột năm được parse linh hoạt (không chỉ regex fullmatch '\\d{4}').
    """
    if df is None or df.empty:
        return pd.Series(dtype=float)

    item_col = df.columns[0]
    text = df[item_col].astype(str).str.lower()

    mask = pd.Series(False, index=df.index)
    for kw in keywords:
        mask = mask | text.str.contains(kw.lower(), na=False, regex=False)
    if exclude:
        for kw in exclude:
            mask = mask & ~text.str.contains(kw.lower(), na=False, regex=False)

    matched = df[mask]
    if matched.empty:
        return pd.Series(dtype=float)

    row = matched.iloc[0]
    result = {}
    for col in df.columns[1:]:
        year = _parse_year_from_col(col)
        if year and year in TARGET_YEARS:
            val = _parse_num(row[col])
            if val is not None:
                result[year] = val

    return pd.Series(result).sort_index()


def _fetch_cafef(ticker: str) -> dict | None:
    """
    ✅ FIX 4: Thêm keyword doanh thu đặc thù cho bán lẻ / BĐS cho thuê.
    """
    try:
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
            'Referer': 'https://cafef.vn/',
        }

        def _get_tables(url):
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            return pd.read_html(r.text, flavor='lxml')

        url_income = (
            f"https://cafef.vn/du-lieu/bao-cao-tai-chinh/"
            f"{ticker.lower()}/kqkd/0/0/0/0/ket-qua-kinh-doanh.chn"
        )
        url_balance = (
            f"https://cafef.vn/du-lieu/bao-cao-tai-chinh/"
            f"{ticker.lower()}/cdkt/0/0/0/0/can-doi-ke-toan.chn"
        )

        tables_income = _get_tables(url_income)
        tables_balance = _get_tables(url_balance)

        income_raw = tables_income[0] if tables_income else pd.DataFrame()
        balance_raw = tables_balance[0] if tables_balance else pd.DataFrame()

        # ✅ Keyword revenue mở rộng — bán lẻ, phân phối, BĐS cho thuê
        is_retail = ticker in RETAIL_TICKERS
        is_realestate = ticker in REAL_ESTATE_TICKERS

        if is_realestate:
            revenue_keywords = [
                'doanh thu cho thuê', 'doanh thu bất động sản',
                'doanh thu thuần', 'tổng doanh thu', 'net revenue',
            ]
        elif is_retail:
            revenue_keywords = [
                'doanh thu bán hàng',                        # MWG, FRT, DGW
                'doanh thu thuần về bán hàng',               # PNJ
                'doanh thu thuần',
                'tổng doanh thu',
                'net revenue', 'revenue',
            ]
        else:
            revenue_keywords = [
                'doanh thu thuần', 'net revenue', 'net sales',
                'doanh thu bán hàng', 'tổng doanh thu',
            ]

        revenue = _extract_cafef_series(income_raw, revenue_keywords,
                                        exclude=['giá vốn', 'cost', 'chi phí lãi'])

        net_profit = _extract_cafef_series(
            income_raw,
            ['lợi nhuận sau thuế', 'lnst', 'profit after tax', 'net income',
             'lợi nhuận thuần sau thuế'],
            exclude=['trước thuế', 'before tax', 'thiểu số', 'minority']
        )

        equity = _extract_cafef_series(
            balance_raw,
            ['vốn chủ sở hữu', 'vcsh', 'equity', "owner's equity", 'total equity'],
            exclude=['vốn điều lệ', 'charter capital']
        )

        total_assets = _extract_cafef_series(
            balance_raw,
            ['tổng cộng tài sản', 'total assets', 'tổng tài sản']
        )

        def _conv(s):
            return s.map(_to_ty).dropna() if not s.empty else s

        out = {
            'revenue':      _conv(revenue),
            'net_profit':   _conv(net_profit),
            'equity':       _conv(equity),
            'total_assets': _conv(total_assets),
            'eps':          pd.Series(dtype=float),
            'bvps':         pd.Series(dtype=float),
            'roe':          pd.Series(dtype=float),
            'roa':          pd.Series(dtype=float),
            '_source':      'cafef',
        }
        return out

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# NGUỒN 3: yfinance
# ─────────────────────────────────────────────────────────────

def _fetch_yfinance(ticker: str) -> dict | None:
    """
    ✅ FIX 5: Thử thêm suffix .HN cho mã HNX/UPCoM như HAX, HUT, HHS, SVC.
    """
    try:
        import yfinance as yf

        # Ưu tiên .VN (HOSE), sau đó .HN (HNX/UPCoM)
        suffixes = ['.VN', '.HN', '']
        tk = None
        for sfx in suffixes:
            try:
                t = yf.Ticker(ticker + sfx)
                info = t.info
                if info and (info.get('regularMarketPrice') or info.get('totalRevenue')):
                    tk = t
                    break
            except Exception:
                continue

        if tk is None:
            return None

        inc = tk.financials
        bs = tk.balance_sheet

        def _yf_series(df, keywords):
            if df is None or df.empty:
                return pd.Series(dtype=float)
            text_idx = pd.Series(df.index.astype(str)).str.lower()
            mask = pd.Series(False, index=range(len(df.index)))
            for kw in keywords:
                mask = mask | text_idx.str.contains(kw.lower(), na=False)
            matched_rows = [df.index[i] for i, m in enumerate(mask) if m]
            if not matched_rows:
                return pd.Series(dtype=float)
            row = df.loc[matched_rows[0]]
            result = {}
            for col in row.index:
                try:
                    year = int(str(col)[:4])
                    if year in TARGET_YEARS:
                        val = float(row[col])
                        if pd.notna(val):
                            result[year] = val
                except Exception:
                    continue
            return pd.Series(result).sort_index()

        revenue = _yf_series(inc, ['total revenue', 'revenue'])
        net_profit = _yf_series(inc, ['net income'])
        equity = _yf_series(bs, ['stockholders equity', 'total equity', "total stockholder's equity"])
        total_assets = _yf_series(bs, ['total assets'])

        def _conv(s):
            return s.map(_to_ty).dropna() if not s.empty else s

        return {
            'revenue':      _conv(revenue),
            'net_profit':   _conv(net_profit),
            'equity':       _conv(equity),
            'total_assets': _conv(total_assets),
            'eps':          pd.Series(dtype=float),
            'bvps':         pd.Series(dtype=float),
            'roe':          pd.Series(dtype=float),
            'roa':          pd.Series(dtype=float),
            '_source':      'yfinance',
        }

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# NGUỒN 4: TCBS API (mới thêm — phủ tốt mã mid/small cap)
# ─────────────────────────────────────────────────────────────

def _fetch_tcbs(ticker: str) -> dict | None:
    """
    Gọi thẳng API công khai của TCBS (không cần auth).
    Phủ tốt cho: HUT, HHS, PSD, SVC, HAX, MCH và các mã HNX/UPCoM.
    """
    try:
        base = "https://apipubaws.tcbs.com.vn/tcanalysis/v1/finance"
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        }

        # Income Statement
        url_is = f"{base}/{ticker}/income-statement?yearly=1&page=0&size=10"
        r = requests.get(url_is, headers=headers, timeout=10)
        r.raise_for_status()
        data_is = r.json()

        # Balance Sheet
        url_bs = f"{base}/{ticker}/balance-sheet?yearly=1&page=0&size=10"
        r2 = requests.get(url_bs, headers=headers, timeout=10)
        r2.raise_for_status()
        data_bs = r2.json()

        def _extract_tcbs(data, field_keys):
            """data là list dicts [{year: 2021, fieldName: value, ...}]"""
            if not data or not isinstance(data, list):
                return pd.Series(dtype=float)
            result = {}
            for row in data:
                year = row.get('year') or row.get('fiscalYear')
                if year is None:
                    continue
                year = int(str(year)[:4])
                if year not in TARGET_YEARS:
                    continue
                for fk in field_keys:
                    val = row.get(fk)
                    if val is not None:
                        try:
                            result[year] = float(val)
                            break
                        except Exception:
                            pass
            return pd.Series(result).sort_index()

        # TCBS trả về đơn vị tỷ VNĐ trực tiếp
        is_retail = ticker in RETAIL_TICKERS
        is_realestate = ticker in REAL_ESTATE_TICKERS

        if is_realestate:
            rev_keys = ['netRevenue', 'revenue', 'rentalRevenue']
        elif is_retail:
            rev_keys = ['netRevenue', 'revenue', 'salesRevenue']
        else:
            rev_keys = ['netRevenue', 'revenue']

        revenue = _extract_tcbs(data_is, rev_keys)
        net_profit = _extract_tcbs(data_is, ['postTaxProfit', 'netProfit', 'netIncome'])
        equity = _extract_tcbs(data_bs, ['equity', 'ownerEquity', 'totalEquity'])
        total_assets = _extract_tcbs(data_bs, ['asset', 'totalAssets'])

        # Đơn vị TCBS: tỷ VNĐ → không cần convert, chỉ round
        def _safe(s):
            return s.map(lambda v: round(float(v), 2)).dropna() if not s.empty else s

        return {
            'revenue':      _safe(revenue),
            'net_profit':   _safe(net_profit),
            'equity':       _safe(equity),
            'total_assets': _safe(total_assets),
            'eps':          pd.Series(dtype=float),
            'bvps':         pd.Series(dtype=float),
            'roe':          pd.Series(dtype=float),
            'roa':          pd.Series(dtype=float),
            '_source':      'tcbs',
        }

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# MERGE: ghép nhiều nguồn
# ─────────────────────────────────────────────────────────────

def _merge_sources(sources: list) -> dict:
    fields = ['revenue', 'net_profit', 'equity', 'total_assets', 'eps', 'bvps', 'roe', 'roa']
    merged = {f: pd.Series(dtype=float) for f in fields}
    sources_used = []

    for src in sources:
        if src is None:
            continue
        src_name = src.get('_source', 'unknown')
        filled_any = False

        for f in fields:
            s = src.get(f, pd.Series(dtype=float))
            if s is None or s.empty:
                continue
            if merged[f].empty:
                merged[f] = s
                filled_any = True
            else:
                missing_years = s.index.difference(merged[f].index)
                if len(missing_years) > 0:
                    merged[f] = pd.concat([merged[f], s.loc[missing_years]]).sort_index()
                    filled_any = True

        if filled_any and src_name not in sources_used:
            sources_used.append(src_name)

    # Đảm bảo chỉ giữ 2021–2025
    for f in fields:
        merged[f] = _filter_years(merged[f])

    merged['_sources_used'] = sources_used
    return merged


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def fetch_financial_data(ticker: str, warnings_container=None) -> dict:
    """
    Fetch tài chính từ 4 nguồn theo thứ tự ưu tiên:
    vnstock (VCI/KBS/DNSE) → TCBS API → CafeF scrape → yfinance

    Đảm bảo trả về đủ năm 2021–2025 cho mọi mã bán lẻ VN.

    Parameters
    ----------
    ticker : str — mã CP, VD 'MWG', 'FRT', 'DGW'
    warnings_container : streamlit container (optional)

    Returns
    -------
    dict với keys: revenue, net_profit, equity, total_assets,
                   eps, bvps, roe, roa, _sources_used
    """
    results = []

    # Nguồn 1: vnstock
    results.append(_fetch_vnstock(ticker))

    # Nguồn 2: TCBS (thêm mới — phủ tốt mid/small cap và HNX)
    results.append(_fetch_tcbs(ticker))

    # Nguồn 3: CafeF scrape
    results.append(_fetch_cafef(ticker))

    # Nguồn 4: yfinance (fallback cuối)
    results.append(_fetch_yfinance(ticker))

    merged = _merge_sources(results)

    # Cảnh báo nếu vẫn thiếu
    warn = warnings_container or st
    missing = []
    for f in ['revenue', 'net_profit', 'equity', 'total_assets']:
        if merged[f].empty:
            missing.append(f)
        else:
            # Kiểm tra năm nào còn thiếu
            got_years = set(merged[f].index.tolist())
            missing_years = [y for y in TARGET_YEARS if y not in got_years]
            if missing_years:
                label_map = {
                    'revenue': 'Doanh thu', 'net_profit': 'Lợi nhuận',
                    'equity': 'VCSH', 'total_assets': 'Tổng tài sản'
                }
                warn.warning(
                    f"⚠️ {ticker} — {label_map[f]} thiếu năm: {missing_years}"
                )

    if missing:
        label_map = {
            'revenue': 'Doanh thu', 'net_profit': 'Lợi nhuận',
            'equity': 'Vốn chủ sở hữu', 'total_assets': 'Tổng tài sản'
        }
        for f in missing:
            warn.error(
                f"❌ Không lấy được '{label_map[f]}' cho {ticker} "
                f"từ tất cả nguồn (vnstock, TCBS, CafeF, yfinance)."
            )

    sources_used = merged.get('_sources_used', [])
    if sources_used:
        st.caption(f"📡 Nguồn BCTC {ticker}: {' + '.join(sources_used)}")

    return merged
