"""
data_fetcher.py
---------------
Multi-source fallback fetcher cho dữ liệu tài chính VN.
Thứ tự ưu tiên: vnstock (VCI/KBS/DNSE) → CafeF scrape → yfinance → investing.com

Trả về chuẩn hoá về dict:
{
    'revenue':       pd.Series (index=năm int, value=tỷ VNĐ),
    'net_profit':    pd.Series,
    'equity':        pd.Series,
    'total_assets':  pd.Series,
    'eps':           pd.Series (đồng/CP),
    'bvps':          pd.Series (đồng/CP),
    'roe':           pd.Series (%),
    'roa':           pd.Series (%),
}
"""

import re
import time
import requests
import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────
# HELPER: chuẩn hoá số từ string "1,234.56" / "1.234,56"
# ─────────────────────────────────────────────
def _parse_num(s):
    if s is None:
        return None
    s = str(s).strip().replace('\xa0', '').replace(' ', '')
    # Loại ký tự không phải số / dấu
    s = re.sub(r'[^\d.,\-]', '', s)
    if not s or s in ['-', '.', ',']:
        return None
    # Detect format: nếu dấu phẩy xuất hiện sau dấu chấm -> US format 1,234.56
    if '.' in s and ',' in s:
        if s.index('.') < s.index(','):        # 1.234,56 → EU
            s = s.replace('.', '').replace(',', '.')
        else:                                   # 1,234.56 → US
            s = s.replace(',', '')
    elif ',' in s:
        # Nếu phần sau dấu phẩy có đúng 3 chữ số → dấu phân nghìn
        parts = s.split(',')
        if len(parts[-1]) == 3:
            s = s.replace(',', '')
        else:
            s = s.replace(',', '.')
    try:
        return float(s)
    except Exception:
        return None


def _to_ty(val):
    """Chuẩn hoá về đơn vị tỷ VNĐ."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    val = float(val)
    if abs(val) > 1e8:        # đang ở đồng → chia về tỷ
        return round(val / 1e9, 2)
    if abs(val) > 1e5:        # đang ở triệu → chia về tỷ
        return round(val / 1e3, 2)
    return round(val, 2)      # đã ở tỷ


# ─────────────────────────────────────────────
# NGUỒN 1: vnstock (VCI / KBS / DNSE)
# ─────────────────────────────────────────────
def _fetch_vnstock(ticker):
    try:
        from vnstock.api.financial import Finance
        from financial_normalizer import build_5y_financial_table, find_row_series

        result = {'income': pd.DataFrame(), 'balance': pd.DataFrame(), 'ratio': pd.DataFrame()}

        for source in ['VCI', 'KBS', 'DNSE']:
            try:
                f = Finance(symbol=ticker, source=source, period='year')
                if result['income'].empty:
                    try:
                        df = f.income_statement(period='year')
                        if df is not None and not df.empty:
                            result['income'] = df
                    except Exception:
                        pass
                if result['balance'].empty:
                    try:
                        df = f.balance_sheet(period='year')
                        if df is not None and not df.empty:
                            result['balance'] = df
                    except Exception:
                        pass
                if result['ratio'].empty:
                    try:
                        df = f.ratio(period='year')
                        if df is not None and not df.empty:
                            result['ratio'] = df
                    except Exception:
                        pass
            except Exception:
                continue

        fin5 = build_5y_financial_table(result['income'], result['balance'], result['ratio'])

        out = {}
        for k in ['revenue', 'net_profit', 'equity', 'total_assets', 'eps', 'bvps', 'roe', 'roa']:
            s = fin5.get(k, pd.Series(dtype=float))
            
            # [SỬA LỖI] Ép kiểu toàn bộ index (Năm) của vnstock về Số nguyên (int)
            # để đồng bộ tuyệt đối với dữ liệu từ CafeF và yfinance
            if not s.empty:
                try:
                    s.index = s.index.astype(int)
                except Exception:
                    pass

            if k in ['revenue', 'net_profit', 'equity', 'total_assets']:
                out[k] = s.map(_to_ty).dropna() if not s.empty else s
            else:
                out[k] = s
        out['_source'] = 'vnstock'
        return out

    except Exception as e:
        return None


# ─────────────────────────────────────────────
# NGUỒN 2: CafeF scrape
# ─────────────────────────────────────────────
def _fetch_cafef(ticker):
    """
    Scrape bảng BCTC từ CafeF.
    URL: https://cafef.vn/du-lieu/bao-cao-tai-chinh/{ticker}.chn
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'vi-VN,vi;q=0.9',
        }

        # 1. Lấy KQKD
        url_income = f"https://cafef.vn/du-lieu/bao-cao-tai-chinh/{ticker.lower()}/kqkd/0/0/0/0/ket-qua-kinh-doanh.chn"
        r = requests.get(url_income, headers=headers, timeout=15)
        r.raise_for_status()

        tables = pd.read_html(r.text, flavor='lxml')
        if not tables:
            return None

        income_raw = tables[0]

        # 2. Lấy CĐKT
        url_balance = f"https://cafef.vn/du-lieu/bao-cao-tai-chinh/{ticker.lower()}/cdkt/0/0/0/0/can-doi-ke-toan.chn"
        r2 = requests.get(url_balance, headers=headers, timeout=15)
        r2.raise_for_status()
        tables2 = pd.read_html(r2.text, flavor='lxml')
        balance_raw = tables2[0] if tables2 else pd.DataFrame()

        def _extract_cafef_series(df, keywords, exclude=None):
            """Dò dòng theo keyword trong cột đầu tiên của bảng CafeF."""
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
                col_str = str(col).strip()
                if re.fullmatch(r'\d{4}', col_str):
                    val = _parse_num(row[col])
                    if val is not None:
                        result[int(col_str)] = val
            return pd.Series(result).sort_index()

        revenue = _extract_cafef_series(income_raw,
            ['doanh thu thuần', 'net revenue', 'doanh thu bán hàng'],
            exclude=['giá vốn', 'cost'])
        net_profit = _extract_cafef_series(income_raw,
            ['lợi nhuận sau thuế', 'lnst', 'profit after tax'],
            exclude=['trước thuế', 'minority', 'thiểu số'])
        equity = _extract_cafef_series(balance_raw,
            ['vốn chủ sở hữu', 'vcsh', 'equity', 'owners equity'],
            exclude=['vốn điều lệ', 'charter'])
        total_assets = _extract_cafef_series(balance_raw,
            ['tổng cộng tài sản', 'total assets', 'tổng tài sản'])

        out = {
            'revenue':      revenue.map(_to_ty).dropna() if not revenue.empty else revenue,
            'net_profit':   net_profit.map(_to_ty).dropna() if not net_profit.empty else net_profit,
            'equity':       equity.map(_to_ty).dropna() if not equity.empty else equity,
            'total_assets': total_assets.map(_to_ty).dropna() if not total_assets.empty else total_assets,
            'eps':          pd.Series(dtype=float),
            'bvps':         pd.Series(dtype=float),
            'roe':          pd.Series(dtype=float),
            'roa':          pd.Series(dtype=float),
            '_source':      'cafef',
        }
        return out

    except Exception as e:
        return None


# ─────────────────────────────────────────────
# NGUỒN 3: yfinance
# ─────────────────────────────────────────────
def _fetch_yfinance(ticker):
    """
    yfinance dùng ticker dạng 'BSR.VN' cho HOSE,
    'BSR.HN' cho HNX (không phải lúc nào cũng có).
    """
    try:
        import yfinance as yf

        suffixes = ['.VN', '.HN', '']
        tk = None
        for sfx in suffixes:
            try:
                t = yf.Ticker(ticker + sfx)
                info = t.info
                if info and info.get('regularMarketPrice'):
                    tk = t
                    break
            except Exception:
                continue

        if tk is None:
            return None

        # Income statement
        inc = tk.financials  # columns = datetime, rows = items
        bs = tk.balance_sheet

        def _yf_series(df, keywords):
            if df is None or df.empty:
                return pd.Series(dtype=float)
            text = pd.Series(df.index.astype(str)).str.lower()
            mask = pd.Series(False, index=range(len(df.index)))
            for kw in keywords:
                mask = mask | text.str.contains(kw.lower(), na=False, regex=False)
            matched_idx = [df.index[i] for i, m in enumerate(mask) if m]
            if not matched_idx:
                return pd.Series(dtype=float)
            row = df.loc[matched_idx[0]]
            result = {}
            for col in row.index:
                try:
                    year = int(str(col)[:4])
                    val = float(row[col])
                    if pd.notna(val):
                        result[year] = val
                except Exception:
                    continue
            return pd.Series(result).sort_index()

        revenue      = _yf_series(inc, ['total revenue', 'revenue'])
        net_profit   = _yf_series(inc, ['net income'])
        equity       = _yf_series(bs,  ['stockholders equity', 'total equity', "total stockholder's equity"])
        total_assets = _yf_series(bs,  ['total assets'])

        out = {
            'revenue':      revenue.map(_to_ty).dropna() if not revenue.empty else revenue,
            'net_profit':   net_profit.map(_to_ty).dropna() if not net_profit.empty else net_profit,
            'equity':       equity.map(_to_ty).dropna() if not equity.empty else equity,
            'total_assets': total_assets.map(_to_ty).dropna() if not total_assets.empty else total_assets,
            'eps':          pd.Series(dtype=float),
            'bvps':         pd.Series(dtype=float),
            'roe':          pd.Series(dtype=float),
            'roa':          pd.Series(dtype=float),
            '_source':      'yfinance',
        }
        return out

    except Exception:
        return None


# ─────────────────────────────────────────────
# MERGE: ghép nhiều nguồn, ưu tiên nguồn đầy đủ hơn
# ─────────────────────────────────────────────
def _merge_sources(sources):
    """
    Ghép nhiều dict nguồn lại.
    Với mỗi field, dùng nguồn đầu tiên có data,
    fill các năm còn thiếu từ nguồn tiếp theo.
    """
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
                # Fill năm còn thiếu
                missing_years = s.index.difference(merged[f].index)
                if len(missing_years) > 0:
                    merged[f] = pd.concat([merged[f], s.loc[missing_years]]).sort_index()
                    filled_any = True
        if filled_any and src_name not in sources_used:
            sources_used.append(src_name)

    merged['_sources_used'] = sources_used
    return merged


# ─────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────
def fetch_financial_data(ticker: str, warnings_container=None) -> dict:
    """
    Hàm chính: fetch tài chính từ nhiều nguồn, merge lại.
    Trả về dict chuẩn hoá với các Series theo năm.
    
    Parameters
    ----------
    ticker : str  — mã CP, VD 'BSR', 'HPG'
    warnings_container : streamlit container (optional) để hiện warning
    
    Returns
    -------
    dict với keys: revenue, net_profit, equity, total_assets,
                   eps, bvps, roe, roa, _sources_used
    """
    results = []

    # Nguồn 1: vnstock
    r1 = _fetch_vnstock(ticker)
    results.append(r1)

    # Nguồn 2: CafeF (luôn thử, dùng để fill missing)
    r2 = _fetch_cafef(ticker)
    results.append(r2)

    # Nguồn 3: yfinance (fallback cuối)
    r3 = _fetch_yfinance(ticker)
    results.append(r3)

    merged = _merge_sources(results)

    # Cảnh báo nếu vẫn thiếu field quan trọng
    warn = warnings_container or st
    missing = []
    for f in ['equity', 'total_assets']:
        if merged[f].empty:
            missing.append(f)

    if missing:
        label_map = {'equity': 'Vốn chủ sở hữu', 'total_assets': 'Tổng tài sản'}
        for f in missing:
            warn.warning(
                f"⚠️ Không dò được '{label_map[f]}' cho {ticker} từ tất cả nguồn "
                f"(vnstock, CafeF, yfinance). Các chỉ số BVPS/DuPont/9PP liên quan sẽ thiếu."
            )

    sources_used = merged.get('_sources_used', [])
    if sources_used:
        st.caption(f"📡 Nguồn BCTC: {' + '.join(sources_used)}")

    return merged
