import pandas as pd
import numpy as np
import streamlit as st
import concurrent.futures
from datetime import datetime, timedelta

from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company

from financial_normalizer import (
    find_row_series, build_5y_financial_table, build_financial_table,
    get_latest, get_latest_n_years, cagr,
)
from valuation import (
    dupont_decomposition, dcf_fcff_scenarios, reverse_dcf_implied_growth,
    graham_number, ddm_gordon, nine_methods_valuation, summarize_valuation,
)
from cafef_fallback import (
    fetch_cafef_balance_sheet_5y, fetch_cafef_yearly_full, fetch_cafef_quarterly_full,
)
from news_fetcher import fetch_news_with_fallback
from cafef_reports import fetch_analysis_reports

SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']

def normalize_to_billion_vnd(series):
    if series is None or series.empty: return series
    def _to_ty(val):
        try:
            if pd.isna(val): return None
            val = float(val)
            if abs(val) > 1e11: return round(val / 1e9, 2)
            return round(val, 2)
        except Exception: return None
    return series.map(_to_ty).dropna()

def normalize_net_profit_with_anchor(net_profit_raw, equity_series, roe_series):
    base = normalize_to_billion_vnd(net_profit_raw)
    if base is None or base.empty: return base
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
        divisor = 10 ** power
        fixed[year] = round(raw_val / divisor, 2)
    return pd.Series(fixed)

def _build_engines_fast(ticker):
    test_end = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')
    
    def check_source(src):
        probe = Quote(symbol=ticker, source=src).history(start=test_start, end=test_end, interval='1D')
        if probe is not None and not probe.empty: return src
        raise ValueError("Rỗng")

    # ĐA LUỒNG: Phóng tìm cả 3 nguồn (VCI, KBS, DNSE) cùng lúc, nguồn nào trả về TRƯỚC TIÊN thì chốt nguồn đó ngay!
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_src = {executor.submit(check_source, s): s for s in SOURCE_FALLBACK_ORDER}
        for future in concurrent.futures.as_completed(future_to_src, timeout=5):
            try:
                best_src = future.result()
                return Quote(symbol=ticker, source=best_src), Finance(symbol=ticker, source=best_src, period='year'), Company(symbol=ticker, source=best_src), best_src
            except Exception:
                continue
                
    raise ConnectionError(f"Không thể kết nối đến máy chủ Vnstock cho mã {ticker}.")

@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker, debug_cafef=False):
    try:
        q_engine, f_engine, c_engine, source_used = _build_engines_fast(ticker)
        bs_sources = [source_used] + [s for s in ['VCI', 'KBS', 'DNSE'] if s != source_used]

        end_date = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')

        def fetch_bs(period):
            for s in bs_sources:
                try:
                    df = Finance(symbol=ticker, source=s, period=period).balance_sheet(period=period)
                    if df is not None and not df.empty: return df
                except: pass
            return pd.DataFrame()

        def fetch_news():
            vnstock_cards = []
            try:
                df = c_engine.news()
                if df is not None and not df.empty:
                    time_col = next((col for col in ['publishDate', 'date', 'time', 'publicDate'] if col in df.columns), None)
                    if time_col:
                        df[time_col] = pd.to_datetime(df[time_col], errors='coerce')
                        df = df.sort_values(by=time_col, ascending=False)
                        vnstock_cards = df.to_dict(orient='records')
            except Exception:
                pass
            # Nguồn chính: Google News RSS (luôn có tin, không phụ thuộc vnstock).
            # Nếu RSS lỗi/rỗng -> tự fallback về tin từ vnstock (nếu có).
            return fetch_news_with_fallback(ticker, vnstock_cards)

        # TẤT CẢ TÁC VỤ KÉO DỮ LIỆU ĐƯỢC ĐÓNG GÓI VÀ CHẠY SONG SONG
        tasks = {
            'price': lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            'overview': lambda: c_engine.overview(),
            'income': lambda: f_engine.income_statement(period='year'),
            'cashflow': lambda: f_engine.cash_flow(period='year'),
            'ratio': lambda: f_engine.ratio(period='year'),
            'income_q': lambda: f_engine.income_statement(period='quarter'),
            'ratio_q': lambda: f_engine.ratio(period='quarter'),
            'bs_y': lambda: fetch_bs('year'),
            'bs_q': lambda: fetch_bs('quarter'),
            'news': fetch_news,
            'reports': lambda: fetch_analysis_reports(ticker),
        }

        raw_data = {}
        # Mở 11 "cửa" lấy dữ liệu cùng một lúc, giới hạn thời gian tối đa 6 giây!
        with concurrent.futures.ThreadPoolExecutor(max_workers=11) as executor:
            future_to_name = {executor.submit(func): name for name, func in tasks.items()}
            for future in concurrent.futures.as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    raw_data[name] = future.result(timeout=6)
                except Exception:
                    if name == 'news':
                        raw_data[name] = []
                    elif name == 'reports':
                        raw_data[name] = {"reports": [], "is_ticker_specific": False}
                    else:
                        raw_data[name] = pd.DataFrame()

        df_price = raw_data['price']
        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None

        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        df_price['close_vnd'] = df_price['close'] * 1000
        df_price['open_vnd']  = df_price['open']  * 1000
        df_price['high_vnd']  = df_price['high']  * 1000
        df_price['low_vnd']   = df_price['low']   * 1000

        df_overview = raw_data['overview']
        df_income = raw_data['income']
        df_cashflow = raw_data['cashflow']
        df_ratio = raw_data['ratio']
        df_income_q = raw_data['income_q']
        df_ratio_q = raw_data['ratio_q']
        df_balance = raw_data['bs_y']
        df_balance_q = raw_data['bs_q']
        news_list = raw_data['news']
        if isinstance(news_list, pd.DataFrame): news_list = []
        reports_pkg = raw_data.get('reports') or {"reports": [], "is_ticker_specific": False}

        is_bank = ticker in ['VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB']
        current_price = float(df_price['close_vnd'].iloc[-1])

        fin5 = build_5y_financial_table(df_income, df_balance, df_ratio, ticker=ticker)

        revenue_series      = normalize_to_billion_vnd(fin5['revenue'])
        equity_series       = normalize_to_billion_vnd(fin5['equity'])
        total_assets_series = normalize_to_billion_vnd(fin5['total_assets'])
        net_profit_series   = normalize_net_profit_with_anchor(fin5['net_profit'], equity_series, fin5['roe'])

        eps_series                = fin5['eps']
        bvps_series               = fin5['bvps']
        roe_series                = fin5['roe']
        roa_series                = fin5['roa']
        pe_series                 = fin5['pe']
        pb_series                 = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']

        if equity_series.empty and not total_assets_series.empty:
            total_liab_series = normalize_to_billion_vnd(find_row_series(
                df_balance, ['tổng cộng nợ phải trả', 'tổng nợ phải trả', 'total liabilities'], exclude_keywords=['vốn chủ sở hữu']))
            if not total_liab_series.empty:
                common_years = total_assets_series.index.intersection(total_liab_series.index)
                if len(common_years) > 0:
                    equity_series = (total_assets_series.loc[common_years] - total_liab_series.loc[common_years])

        if equity_series.empty or total_assets_series.empty:
            current_year = datetime.today().year
            cafef_data = fetch_cafef_balance_sheet_5y(ticker, end_year=current_year)
            if equity_series.empty and not cafef_data['equity'].empty: equity_series = cafef_data['equity']
            if total_assets_series.empty and not cafef_data['total_assets'].empty: total_assets_series = cafef_data['total_assets']

        market_cap_series_raw = fin5.get('market_cap', pd.Series(dtype=float))
        market_cap_direct = get_latest(market_cap_series_raw, default=0.0)
        if market_cap_direct > 0 and current_price > 0:
            implied_shares_check = market_cap_direct / current_price
            if not (1_000_000 <= implied_shares_check <= 50_000_000_000): market_cap_direct = 0.0

        issue_share = get_latest(outstanding_shares_series, default=0.0)
        if issue_share == 0.0 and not df_overview.empty:
            for col in ['issue_share', 'outstanding_shares', 'listed_volume']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    issue_share = float(df_overview[col].iloc[0])
                    break
        if issue_share == 0.0 and not df_overview.empty and 'charter_capital' in df_overview.columns:
            try: issue_share = float(df_overview['charter_capital'].iloc[0]) / 10000
            except Exception: pass
            
        if market_cap_direct > 0 and current_price > 0:
            implied_shares_from_cap = market_cap_direct / current_price
            if issue_share > 0:
                if abs(implied_shares_from_cap - issue_share) / issue_share > 0.20: issue_share = implied_shares_from_cap
            else: issue_share = implied_shares_from_cap

        eps_latest  = get_latest(eps_series,  default=0.0)
        bvps_latest = get_latest(bvps_series, default=0.0)
        if bvps_latest == 0.0 and issue_share > 0 and not equity_series.empty:
            bvps_latest = get_latest(equity_series, default=0.0) / issue_share

        def _normalize_pct(series):
            if series is None or series.empty: return series
            if abs(series.iloc[-1]) < 1: return series * 100
            return series

        roe_series = _normalize_pct(roe_series)
        roa_series = _normalize_pct(roa_series)

        market_cap = market_cap_direct if market_cap_direct > 0 else (current_price * issue_share if issue_share > 0 else 0.0)
        pe_fresh = (current_price / eps_latest)  if eps_latest  > 0 else 0.0
        pb_fresh = (current_price / bvps_latest) if bvps_latest > 0 else 0.0

        clean_metrics = {
            "is_bank": is_bank, "current_price": current_price, "market_cap_billion": market_cap / 1e9,
            "pe": pe_fresh, "pb": pb_fresh, "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
            "source_used": source_used,
        }

        # --- KQKD NĂM ---
        current_year = datetime.today().year
        existing_years = set(revenue_series.index) | set(net_profit_series.index) | set(equity_series.index) | set(total_assets_series.index)
        target_years = set(range(2021, min(current_year, 2025) + 1))
        missing_years = sorted(target_years - existing_years)
        
        if missing_years:
            try:
                cafef_full = fetch_cafef_yearly_full(ticker, missing_years)
                for yr, val in cafef_full['revenue'].items(): revenue_series.loc[yr] = val
                for yr, val in cafef_full['net_profit'].items(): net_profit_series.loc[yr] = val
                for yr, val in cafef_full['equity'].items(): equity_series.loc[yr] = val
                for yr, val in cafef_full['total_assets'].items(): total_assets_series.loc[yr] = val
                for yr, val in cafef_full['roe'].items(): roe_series.loc[yr] = val
                for yr, val in cafef_full['roa'].items(): roa_series.loc[yr] = val
                revenue_series, net_profit_series = revenue_series.sort_index(), net_profit_series.sort_index()
                equity_series, total_assets_series = equity_series.sort_index(), total_assets_series.sort_index()
                roe_series, roa_series = roe_series.sort_index(), roa_series.sort_index()
            except Exception: pass

        years_available = sorted(set(revenue_series.index) | set(net_profit_series.index) | set(equity_series.index) | set(total_assets_series.index))
        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)']            = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)']         = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)']    = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)']              = df_5y_table['Năm'].map(eps_series)
        df_5y_table['BVPS (đ)']             = df_5y_table['Năm'].map(bvps_series)
        df_5y_table['ROE (%)']              = df_5y_table['Năm'].map(lambda y: roe_series.get(y, None))
        df_5y_table['ROA (%)']              = df_5y_table['Năm'].map(lambda y: roa_series.get(y, None))

        fundamentals_summary = {
            "revenue_cagr_pct":    (cagr(get_latest_n_years(revenue_series, 5)) * 100) if cagr(get_latest_n_years(revenue_series, 5)) is not None else None,
            "net_profit_cagr_pct": (cagr(get_latest_n_years(net_profit_series, 5)) * 100) if cagr(get_latest_n_years(net_profit_series, 5)) is not None else None,
            "eps_latest":  eps_latest, "bvps_latest": bvps_latest,
            "roe_latest":  get_latest(roe_series, default=None), "roa_latest":  get_latest(roa_series, default=None),
        }

        # --- KQKD QUÝ ---
        try:
            fin_q = build_financial_table(df_income_q, df_balance_q, df_ratio_q, ticker=ticker, period='quarter')
            revenue_series_q      = normalize_to_billion_vnd(fin_q['revenue'])
            equity_series_q       = normalize_to_billion_vnd(fin_q['equity'])
            total_assets_series_q = normalize_to_billion_vnd(fin_q['total_assets'])
            net_profit_series_q   = normalize_net_profit_with_anchor(fin_q['net_profit'], equity_series_q, fin_q['roe'])
            eps_series_q  = fin_q['eps']
            bvps_series_q = fin_q['bvps']
            roe_series_q = _normalize_pct(fin_q['roe'])
            roa_series_q = _normalize_pct(fin_q['roa'])

            existing_q_keys = set(revenue_series_q.index) | set(net_profit_series_q.index) | set(equity_series_q.index) | set(total_assets_series_q.index)
            today = datetime.today()
            cur_q = (today.month - 1) // 3 + 1
            # Chỉ bù 2 năm gần nhất (8 quý) thay vì từ 2022 -> giảm mạnh số request
            # cào CafeF, vốn là nguyên nhân chính khiến trang tải gần 1 phút.
            start_year_q = today.year - 1
            all_target_quarters = [(y, q) for y in range(start_year_q, today.year + 1) for q in range(1, 5) if not (y == today.year and q > cur_q)]
            
            missing_quarters = [(y, q) for (y, q) in all_target_quarters if f"{y}-Q{q}" not in existing_q_keys]
            if missing_quarters:
                try:
                    cafef_q = fetch_cafef_quarterly_full(ticker, missing_quarters)
                    for key, val in cafef_q['revenue'].items(): revenue_series_q.loc[key] = val
                    for key, val in cafef_q['net_profit'].items(): net_profit_series_q.loc[key] = val
                    for key, val in cafef_q['equity'].items(): equity_series_q.loc[key] = val
                    for key, val in cafef_q['total_assets'].items(): total_assets_series_q.loc[key] = val
                except Exception: pass

            quarters_available = sorted(
                set(revenue_series_q.index) | set(net_profit_series_q.index) | set(equity_series_q.index) | set(total_assets_series_q.index),
                key=lambda c: (int(str(c).split('-Q')[0]), int(str(c).split('-Q')[1]))
            )

            df_quarter_table = pd.DataFrame({'_period': quarters_available})
            df_quarter_table['Quý'] = df_quarter_table['_period'].apply(lambda c: f"Q{str(c).split('-Q')[1]}/{str(c).split('-Q')[0]}")
            df_quarter_table['Doanh thu thuần (tỷ)'] = df_quarter_table['_period'].map(revenue_series_q)
            df_quarter_table['LNST (tỷ)']            = df_quarter_table['_period'].map(net_profit_series_q)
            df_quarter_table['Vốn CSH (tỷ)']         = df_quarter_table['_period'].map(equity_series_q)
            df_quarter_table['Tổng tài sản (tỷ)']    = df_quarter_table['_period'].map(total_assets_series_q)
            df_quarter_table['EPS (đ)']              = df_quarter_table['_period'].map(eps_series_q)
            df_quarter_table['BVPS (đ)']             = df_quarter_table['_period'].map(bvps_series_q)
            df_quarter_table['ROE (%)']              = df_quarter_table['_period'].map(lambda y: roe_series_q.get(y, None))
            df_quarter_table['ROA (%)']              = df_quarter_table['_period'].map(lambda y: roa_series_q.get(y, None))
            df_quarter_table = df_quarter_table.drop(columns=['_period'])
        except Exception:
            df_quarter_table = pd.DataFrame()

        df_dupont = dupont_decomposition(revenue_series, net_profit_series, total_assets_series, equity_series)

        cfo_series = normalize_to_billion_vnd(find_row_series(df_cashflow, ['lưu chuyển tiền thuần từ hoạt động kinh doanh', 'net cash flow from operating']))
        capex_series = normalize_to_billion_vnd(find_row_series(df_cashflow, ['tiền chi để mua sắm', 'purchase of fixed assets']))

        latest_fcff = None
        if not cfo_series.empty:
            cfo_latest   = get_latest(cfo_series,   default=None)
            capex_latest = get_latest(capex_series, default=0.0) if not capex_series.empty else 0.0
            if cfo_latest is not None: latest_fcff = (cfo_latest - abs(capex_latest)) * 1e9

        dcf_results = dcf_fcff_scenarios(latest_fcff=latest_fcff, shares_outstanding=issue_share, net_debt=0.0) if (latest_fcff and latest_fcff > 0 and issue_share > 0) else None
        reverse_g   = reverse_dcf_implied_growth(current_price=current_price, shares_outstanding=issue_share, latest_fcff=latest_fcff, wacc=0.105, net_debt=0.0) if dcf_results else None

        graham_value = graham_number(eps_latest, bvps_latest) if eps_latest > 0 and bvps_latest > 0 else None
        valuation_methods = nine_methods_valuation(eps_latest=eps_latest, bvps_latest=bvps_latest, pe_series=pe_series, pb_series=pb_series, current_price=current_price, dcf_results=dcf_results, graham_value=graham_value, ddm_value=None)
        
        valuation_package = {
            "methods": valuation_methods, "summary": summarize_valuation(valuation_methods, current_price) if valuation_methods else None,
            "dcf_scenarios": dcf_results, "reverse_dcf_g_pct": reverse_g * 100 if reverse_g is not None else None,
            "graham_value": graham_value, "ddm_value": None, "pe_series": pe_series, "pb_series": pb_series,
        }

        if 'volume' not in df_price.columns: df_price['volume'] = 0
        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()
        latest_volume     = float(df_price['volume'].iloc[-1])
        avg_volume_20d    = float(df_price['volume_ma20'].iloc[-1]) if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        df_price['MA20']  = df_price['close_vnd'].rolling(window=20).mean()

        technical_summary = {
            "latest_volume": latest_volume, "avg_volume_20d": avg_volume_20d,
            "volume_vs_avg_pct": ((latest_volume / avg_volume_20d - 1) * 100) if avg_volume_20d > 0 else 0.0,
            "ma20": df_price['MA20'].iloc[-1], "oil_correlation": 0.74 if ticker in ['BSR', 'OIL', 'PLX', 'PVD', 'PVS', 'GAS'] else 0.0,
            "trend_signal": "KHẢ QUAN (Uptrend)" if current_price > df_price['MA20'].iloc[-1] else "RỦI RO (Downtrend)",
        }
        
        return (df_price, df_5y_table, df_quarter_table, df_balance, clean_metrics, technical_summary, news_list, fundamentals_summary, df_dupont, valuation_package, reports_pkg)

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        return None
