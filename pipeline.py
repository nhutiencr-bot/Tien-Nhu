import pandas as pd
import numpy as np
import streamlit as st
from news_fetcher import fetch_news_with_fallback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company
from financial_normalizer import (
    find_row_series, build_5y_financial_table, build_financial_table,
    get_latest, get_latest_n_years, cagr,
)
from valuation import (
    dupont_decomposition,
    dcf_fcff_scenarios,
    reverse_dcf_implied_growth,
    graham_number,
    ddm_gordon,
    nine_methods_valuation,
    summarize_valuation,
    detect_stock_dividend_years,
    normalize_eps_bvps_series,
    dcf_sensitivity_table,
    estimate_wacc,
)
from cafef_fallback import fetch_cafef_balance_sheet_5y

SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']
DEFAULT_YEAR_LIMIT = 5


# ─── Helpers ─────────────────────────────────────────────────────────────────

def normalize_to_billion_vnd(series):
    if series is None or series.empty:
        return series
    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            val = float(val)
            return round(val / 1e9, 2) if abs(val) > 1e11 else round(val, 2)
        except Exception:
            return None
    return series.map(_to_ty).dropna()


def normalize_net_profit_with_anchor(net_profit_raw, equity_series, roe_series):
    base = normalize_to_billion_vnd(net_profit_raw)
    if base is None or base.empty:
        return base
    fixed = {}
    for year, raw_val in base.items():
        if (year not in equity_series.index or year not in roe_series.index
                or pd.isna(equity_series.get(year)) or pd.isna(roe_series.get(year))):
            fixed[year] = raw_val
            continue
        expected = equity_series[year] * roe_series[year] / 100
        if expected == 0 or raw_val == 0:
            fixed[year] = raw_val
            continue
        ratio = raw_val / expected
        if ratio <= 0:
            fixed[year] = raw_val
            continue
        power = round(np.log10(ratio))
        fixed[year] = round(raw_val / (10 ** power), 2)
    return pd.Series(fixed)


def _safe_fetch(fn, default=None):
    try:
        result = fn()
        return result if result is not None else (default if default is not None else pd.DataFrame())
    except Exception:
        return default if default is not None else pd.DataFrame()


def _build_engines_with_fallback(ticker):
    last_error = None
    test_end   = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')
    for source in SOURCE_FALLBACK_ORDER:
        try:
            q_engine = Quote(symbol=ticker, source=source)
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError(f"Nguồn {source} rỗng cho {ticker}")
            f_engine = Finance(symbol=ticker, source=source, period='year')
            c_engine = Company(symbol=ticker, source=source)
            return q_engine, f_engine, c_engine, source
        except Exception as e:
            last_error = e
            continue
    raise ConnectionError(f"Không lấy được dữ liệu cho {ticker}. Lỗi: {last_error}")


def _fetch_statement(ticker, method_name, period='year', limit=DEFAULT_YEAR_LIMIT):
    for source in SOURCE_FALLBACK_ORDER:
        try:
            f  = Finance(symbol=ticker, source=source, period=period)
            fn = getattr(f, method_name)
            try:
                df = fn(period=period, limit=limit)
            except TypeError:
                df = fn(period=period)
            if df is not None and not df.empty:
                return df
        except Exception:
            continue
    return pd.DataFrame()


# ─── Pipeline chính ──────────────────────────────────────────────────────────

@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker):
    try:
        # 1. Chọn nguồn
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)

        end_date   = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')

        # 2. Fetch song song — limit=5 cho year
        tasks = {
            "price":      lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            "overview":   lambda: c_engine.overview(),
            "income_y":   lambda: _fetch_statement(ticker, 'income_statement', 'year',    DEFAULT_YEAR_LIMIT),
            "cashflow_y": lambda: _fetch_statement(ticker, 'cash_flow',        'year',    DEFAULT_YEAR_LIMIT),
            "ratio_y":    lambda: _fetch_statement(ticker, 'ratio',            'year',    DEFAULT_YEAR_LIMIT),
            "balance_y":  lambda: _fetch_statement(ticker, 'balance_sheet',    'year',    DEFAULT_YEAR_LIMIT),
            "income_q":   lambda: _fetch_statement(ticker, 'income_statement', 'quarter', 20),
            "ratio_q":    lambda: _fetch_statement(ticker, 'ratio',            'quarter', 20),
            "balance_q":  lambda: _fetch_statement(ticker, 'balance_sheet',    'quarter', 20),
        }
        results = {}
        with ThreadPoolExecutor(max_workers=9) as executor:
            future_to_key = {executor.submit(_safe_fetch, fn): key for key, fn in tasks.items()}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = pd.DataFrame()

        df_price     = results.get("price",      pd.DataFrame())
        df_overview  = results.get("overview",   pd.DataFrame())
        df_income    = results.get("income_y",   pd.DataFrame())
        df_cashflow  = results.get("cashflow_y", pd.DataFrame())
        df_ratio     = results.get("ratio_y",    pd.DataFrame())
        df_balance   = results.get("balance_y",  pd.DataFrame())
        df_income_q  = results.get("income_q",   pd.DataFrame())
        df_ratio_q   = results.get("ratio_q",    pd.DataFrame())
        df_balance_q = results.get("balance_q",  pd.DataFrame())

        # 3. Giá
        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None
        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        for col in ['close', 'open', 'high', 'low']:
            df_price[f'{col}_vnd'] = df_price[col] * 1000
        is_bank = ticker in ['VCB','BID','CTG','TCB','MBB','ACB','STB','VPB','HDB','SHB']
        current_price = float(df_price['close_vnd'].iloc[-1])

        # 4. Chuẩn hoá BCTC
        fin5 = build_5y_financial_table(df_income, df_balance, df_ratio, ticker=ticker)
        revenue_series        = normalize_to_billion_vnd(fin5['revenue'])
        equity_series         = normalize_to_billion_vnd(fin5['equity'])
        total_assets_series   = normalize_to_billion_vnd(fin5['total_assets'])
        net_profit_series     = normalize_net_profit_with_anchor(fin5['net_profit'], equity_series, fin5['roe'])
        eps_series            = fin5['eps']
        bvps_series           = fin5['bvps']
        roe_series            = fin5['roe']
        roa_series            = fin5['roa']
        pe_series             = fin5['pe']
        pb_series             = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']
        dps_series            = fin5.get('dps', pd.Series(dtype=float))

        # Fallback equity
        if equity_series.empty and not total_assets_series.empty:
            tl = normalize_to_billion_vnd(find_row_series(
                df_balance,
                ['tổng cộng nợ phải trả','tổng nợ phải trả','total liabilities'],
                exclude_keywords=['vốn chủ sở hữu']))
            if not tl.empty:
                common_y = total_assets_series.index.intersection(tl.index)
                if len(common_y) > 0:
                    equity_series = total_assets_series.loc[common_y] - tl.loc[common_y]

        if equity_series.empty or total_assets_series.empty:
            cafef = fetch_cafef_balance_sheet_5y(ticker, end_year=datetime.today().year)
            if equity_series.empty and not cafef['equity'].empty:
                equity_series = cafef['equity']
            if total_assets_series.empty and not cafef['total_assets'].empty:
                total_assets_series = cafef['total_assets']

        # 5. Số CP lưu hành
        market_cap_series_raw = fin5.get('market_cap', pd.Series(dtype=float))
        market_cap_direct = get_latest(market_cap_series_raw, default=0.0)
        if market_cap_direct > 0 and current_price > 0:
            if not (1_000_000 <= market_cap_direct / current_price <= 50_000_000_000):
                market_cap_direct = 0.0

        issue_share = get_latest(outstanding_shares_series, default=0.0)
        if issue_share == 0.0 and not df_overview.empty:
            for col in ['issue_share','outstanding_shares','listed_volume']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    issue_share = float(df_overview[col].iloc[0])
                    break
        if issue_share == 0.0 and not df_overview.empty and 'charter_capital' in df_overview.columns:
            try:
                issue_share = float(df_overview['charter_capital'].iloc[0]) / 10000
            except Exception:
                pass
        if market_cap_direct > 0 and current_price > 0:
            implied = market_cap_direct / current_price
            if issue_share > 0:
                if abs(implied - issue_share) / issue_share > 0.20:
                    issue_share = implied
            else:
                issue_share = implied

        eps_latest  = get_latest(eps_series,  default=0.0)
        bvps_latest = get_latest(bvps_series, default=0.0)
        if bvps_latest == 0.0 and issue_share > 0 and not equity_series.empty:
            bvps_latest = get_latest(equity_series, default=0.0) / issue_share

        def _normalize_pct(s):
            if s is None or s.empty:
                return s
            return s * 100 if abs(s.iloc[-1]) < 1 else s

        roe_series = _normalize_pct(roe_series)
        roa_series = _normalize_pct(roa_series)

        market_cap = market_cap_direct if market_cap_direct > 0 else (
            current_price * issue_share if issue_share > 0 else 0.0)

        # ── FIX: Điều chỉnh EPS/BVPS theo pha loãng từ cổ tức cổ phiếu ──
        eps_adj, bvps_adj = normalize_eps_bvps_series(
            eps_series, bvps_series, outstanding_shares_series)

        dilution_years = detect_stock_dividend_years(outstanding_shares_series)

        clean_metrics = {
            "is_bank":             is_bank,
            "current_price":       current_price,
            "market_cap_billion":  market_cap / 1e9,
            "pe":  (current_price / eps_latest)  if eps_latest  > 0 else 0.0,
            "pb":  (current_price / bvps_latest) if bvps_latest > 0 else 0.0,
            "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
            "source_used":         source_used,
            "dilution_years":      dilution_years,
            # Dividend yield
            "dividend_yield_pct": (
                (get_latest(dps_series, default=0.0) / current_price * 100)
                if not dps_series.empty and get_latest(dps_series, default=0.0) > 0
                else None
            ),
            "dps_latest": get_latest(dps_series, default=None) if not dps_series.empty else None,
        }

        # 6. Bảng theo Năm — union 8 series để không mất 2021
        years_available = sorted(
            set(revenue_series.index)      | set(net_profit_series.index) |
            set(equity_series.index)       | set(total_assets_series.index) |
            set(eps_series.index)          | set(bvps_series.index) |
            set(roe_series.index)          | set(roa_series.index)
        )
        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)']            = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)']         = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)']    = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)']              = df_5y_table['Năm'].map(eps_series)
        df_5y_table['BVPS (đ)']             = df_5y_table['Năm'].map(bvps_series)
        df_5y_table['ROE (%)'] = df_5y_table['Năm'].map(lambda y: roe_series.get(y, None))
        df_5y_table['ROA (%)'] = df_5y_table['Năm'].map(lambda y: roa_series.get(y, None))

        revenue_cagr    = cagr(get_latest_n_years(revenue_series,    DEFAULT_YEAR_LIMIT))
        net_profit_cagr = cagr(get_latest_n_years(net_profit_series, DEFAULT_YEAR_LIMIT))
        fundamentals_summary = {
            "revenue_cagr_pct":    revenue_cagr    * 100 if revenue_cagr    is not None else None,
            "net_profit_cagr_pct": net_profit_cagr * 100 if net_profit_cagr is not None else None,
            "eps_latest":          eps_latest,
            "bvps_latest":         bvps_latest,
            "roe_latest":          get_latest(roe_series, default=None),
            "roa_latest":          get_latest(roa_series, default=None),
        }

        # 7. Bảng theo Quý
        df_quarter_table = pd.DataFrame()
        try:
            fin_q  = build_financial_table(df_income_q, df_balance_q, df_ratio_q,
                                           ticker=ticker, period='quarter')
            rev_q  = normalize_to_billion_vnd(fin_q['revenue'])
            eq_q   = normalize_to_billion_vnd(fin_q['equity'])
            ta_q   = normalize_to_billion_vnd(fin_q['total_assets'])
            np_q   = normalize_net_profit_with_anchor(fin_q['net_profit'], eq_q, fin_q['roe'])
            eps_q  = fin_q['eps']
            bvps_q = fin_q['bvps']
            roe_q  = _normalize_pct(fin_q['roe'])
            roa_q  = _normalize_pct(fin_q['roa'])
            quarters = sorted(
                set(rev_q.index) | set(np_q.index) | set(eq_q.index) | set(ta_q.index),
                key=lambda c: (int(str(c).split('-Q')[0]), int(str(c).split('-Q')[1])))
            df_quarter_table = pd.DataFrame({'_p': quarters})
            df_quarter_table['Quý'] = df_quarter_table['_p'].apply(
                lambda c: f"Q{str(c).split('-Q')[1]}/{str(c).split('-Q')[0]}")
            df_quarter_table['Doanh thu thuần (tỷ)'] = df_quarter_table['_p'].map(rev_q)
            df_quarter_table['LNST (tỷ)']            = df_quarter_table['_p'].map(np_q)
            df_quarter_table['Vốn CSH (tỷ)']         = df_quarter_table['_p'].map(eq_q)
            df_quarter_table['Tổng tài sản (tỷ)']    = df_quarter_table['_p'].map(ta_q)
            df_quarter_table['EPS (đ)']              = df_quarter_table['_p'].map(eps_q)
            df_quarter_table['BVPS (đ)']             = df_quarter_table['_p'].map(bvps_q)
            df_quarter_table['ROE (%)'] = df_quarter_table['_p'].map(lambda y: roe_q.get(y, None))
            df_quarter_table['ROA (%)'] = df_quarter_table['_p'].map(lambda y: roa_q.get(y, None))
            df_quarter_table = df_quarter_table.drop(columns=['_p'])
        except Exception:
            pass  # Bảng quý không bắt buộc

        # 8. DuPont
        df_dupont = dupont_decomposition(
            revenue_series, net_profit_series, total_assets_series, equity_series)

        # 9. DCF / Graham / DDM
        cfo_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['lưu chuyển tiền thuần từ hoạt động kinh doanh',
             'net cash flow from operating','cash flow from operating activities']))
        capex_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['tiền chi để mua sắm','purchase of fixed assets',
             'capital expenditure','mua sắm tài sản cố định']))

        latest_fcff = None
        if not cfo_series.empty:
            cfo_l   = get_latest(cfo_series,   default=None)
            capex_l = get_latest(capex_series, default=0.0) if not capex_series.empty else 0.0
            if cfo_l is not None:
                latest_fcff = (cfo_l - abs(capex_l)) * 1e9

        dcf_results = reverse_g = None
        base_wacc   = estimate_wacc(ticker)
        if latest_fcff and latest_fcff > 0 and issue_share > 0:
            dcf_results = dcf_fcff_scenarios(
                latest_fcff=latest_fcff, shares_outstanding=issue_share,
                net_debt=0.0, ticker=ticker)
            reverse_g = reverse_dcf_implied_growth(
                current_price=current_price, shares_outstanding=issue_share,
                latest_fcff=latest_fcff, wacc=base_wacc, net_debt=0.0)

        graham_value = graham_number(eps_latest, bvps_latest) \
            if eps_latest > 0 and bvps_latest > 0 else None

        # DDM — FIX: truyền dps_series + net_profit để kiểm tra payout ratio
        ddm_fair, ddm_note = ddm_gordon(
            dps_series=dps_series,
            net_profit_series=net_profit_series,
            ticker=ticker)
        ddm_value = ddm_fair  # None nếu không áp dụng được

        # Sensitivity table cho DCF
        sensitivity_df = pd.DataFrame()
        if latest_fcff and latest_fcff > 0 and issue_share > 0:
            sensitivity_df = dcf_sensitivity_table(
                latest_fcff=latest_fcff,
                shares_outstanding=issue_share,
                net_debt=0.0)

        # nine_methods — truyền thêm eps_adj/bvps_adj
        valuation_methods = nine_methods_valuation(
            eps_latest=eps_latest,
            bvps_latest=bvps_latest,
            pe_series=pe_series,
            pb_series=pb_series,
            current_price=current_price,
            dcf_results=dcf_results,
            graham_value=graham_value,
            ddm_value=ddm_value,
            eps_adj=eps_adj,
            bvps_adj=bvps_adj,
            shares_series=outstanding_shares_series,
            net_profit_series=net_profit_series,
            dps_series=dps_series,
            ticker=ticker,
        )
        valuation_summary = summarize_valuation(valuation_methods, current_price) \
            if valuation_methods else None

        # BVPS series đã fill để vẽ biểu đồ PB
        bvps_series_filled = bvps_adj if bvps_adj is not None and not bvps_adj.empty else bvps_series

        valuation_package = {
            "methods":             valuation_methods,
            "summary":             valuation_summary,
            "dcf_scenarios":       dcf_results,
            "reverse_dcf_g_pct":   reverse_g * 100 if reverse_g is not None else None,
            "graham_value":        graham_value,
            "ddm_value":           ddm_value,
            "ddm_note":            ddm_note,      # lý do DDM không áp dụng
            "pe_series":           pe_series,
            "pb_series":           pb_series,
            "bvps_series":         bvps_series_filled,
            "sensitivity_df":      sensitivity_df,
            "base_wacc":           base_wacc,
            "dilution_years":      dilution_years,
            "price_series": (
                df_price.set_index('time')['close_vnd']
                .resample('YE').last()
                .rename(lambda x: x.year)
                if not df_price.empty and 'time' in df_price.columns
                else pd.Series(dtype=float)
            ),
        }

        # 10. Technical
        if 'volume' not in df_price.columns:
            df_price['volume'] = 0
        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()
        df_price['MA20']        = df_price['close_vnd'].rolling(window=20).mean()
        latest_vol  = float(df_price['volume'].iloc[-1])
        avg_vol_20d = float(df_price['volume_ma20'].iloc[-1]) \
            if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        technical_summary = {
            "latest_volume":     latest_vol,
            "avg_volume_20d":    avg_vol_20d,
            "volume_vs_avg_pct": ((latest_vol / avg_vol_20d - 1) * 100) if avg_vol_20d > 0 else 0.0,
            "ma20":              df_price['MA20'].iloc[-1],
            "oil_correlation":   0.74 if ticker in ['BSR','OIL','PLX','PVD','PVS','GAS'] else 0.0,
            "trend_signal":      "KHẢ QUAN (Uptrend)" if current_price > df_price['MA20'].iloc[-1]
                                 else "RỦI RO (Downtrend)",
        }

        # 11. Tin tức — FIX: dùng _safe_fetch thay _safe_call
        vnstock_news = []
        news_raw = _safe_fetch(lambda: c_engine.news(), default=pd.DataFrame())
        if news_raw is not None and not news_raw.empty:
            for _, row in news_raw.head(10).iterrows():
                vnstock_news.append({
                    "title":    row.get('news_title',  ''),
                    "source":   row.get('news_source', 'vnstock'),
                    "url":      row.get('news_url',    '#'),
                    "pub_date": "—",
                })
        news_list = fetch_news_with_fallback(ticker, vnstock_news)
        if not news_list:
            news_list = [{"title": "Không có sự kiện bất thường trong 30 ngày.",
                          "source": "Hệ thống tự động", "url": "#", "pub_date": "—"}]

        # reports_pkg = None: placeholder để khớp với app.py unpack 11 giá trị
        reports_pkg = None

        return (
            df_price, df_5y_table, df_quarter_table, df_balance,
            clean_metrics, technical_summary,
            news_list, fundamentals_summary, df_dupont, valuation_package,
            reports_pkg,
        )

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        return None
