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

SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']


def normalize_to_billion_vnd(series):
    """Chuẩn hoá Series về đơn vị tỷ VNĐ."""
    if series is None or series.empty:
        return series
    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            val = float(val)
            if abs(val) > 1e11:
                return round(val / 1e9, 2)
            return round(val, 2)
        except Exception:
            return None
    return series.map(_to_ty).dropna()


def normalize_net_profit_with_anchor(net_profit_raw, equity_series, roe_series):
    """Chuẩn hoá net_profit dùng equity * roe% làm điểm neo để detect đơn vị đúng."""
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
        divisor = 10 ** power
        fixed[year] = round(raw_val / divisor, 2)
    return pd.Series(fixed)


def _build_engines_with_fallback(ticker):
    last_error = None
    test_end = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')
    
    for source in SOURCE_FALLBACK_ORDER:
        def test_source(src):
            q_engine = Quote(symbol=ticker, source=src)
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError("Dữ liệu rỗng")
            f_engine = Finance(symbol=ticker, source=src, period='year')
            c_engine = Company(symbol=ticker, source=src)
            return q_engine, f_engine, c_engine, src

        # CẦU DAO: Bắt buộc ngắt sau 4 giây nếu nguồn bị treo
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(test_source, source)
            try:
                return future.result(timeout=4)
            except concurrent.futures.TimeoutError:
                last_error = f"Nguồn {source} bị treo (Timeout 4s)."
                continue
            except Exception as e:
                last_error = e
                continue

    raise ConnectionError(
        f"Không lấy được dữ liệu cho mã {ticker} từ bất kỳ nguồn nào. Lỗi cuối: {last_error}"
    )


def _safe_call(fn, label, source_used, default=None, timeout_sec=4):
    """Gói gọi hàm có ép xung thời gian Timeout"""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            result = future.result(timeout=timeout_sec)
        return result if result is not None else (default if default is not None else pd.DataFrame())
    except concurrent.futures.TimeoutError:
        return default if default is not None else pd.DataFrame()
    except Exception:
        return default if default is not None else pd.DataFrame()


@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker, debug_cafef=False):
    try:
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)
        
        # MẸO TỐI ƯU: Đưa nguồn đang sống lên đầu danh sách để thử trước, tránh đập đầu vào nguồn đã chết
        bs_sources = [source_used] + [s for s in ['VCI', 'KBS', 'DNSE'] if s != source_used]

        # --- [BƯỚC 1]: Lịch sử Giá ---
        end_date = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')
        
        # Cắt lỗ ở 5s để đảm bảo bảng giá (dữ liệu cốt lõi) có thêm một chút thời gian kéo
        df_price = _safe_call(lambda: q_engine.history(start=start_date, end=end_date, interval='1D'), 'price', source_used, timeout_sec=5)
        
        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None
            
        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        df_price['close_vnd'] = df_price['close'] * 1000
        df_price['open_vnd']  = df_price['open']  * 1000
        df_price['high_vnd']  = df_price['high']  * 1000
        df_price['low_vnd']   = df_price['low']   * 1000

        # --- [BƯỚC 2]: Thu thập BCTC (Ép ngắt hết trong 4s) ---
        df_overview = _safe_call(lambda: c_engine.overview(), 'overview', source_used)
        df_income   = _safe_call(lambda: f_engine.income_statement(period='year'), 'income', source_used)
        df_cashflow = _safe_call(lambda: f_engine.cash_flow(period='year'), 'cash_flow', source_used)
        df_ratio    = _safe_call(lambda: f_engine.ratio(period='year'), 'ratio', source_used)

        df_income_q = _safe_call(lambda: f_engine.income_statement(period='quarter'), 'income_q', source_used)
        df_ratio_q  = _safe_call(lambda: f_engine.ratio(period='quarter'), 'ratio_q', source_used)

        # Balance sheet năm: Chỉ thử từ danh sách bs_sources đã ưu tiên
        df_balance = pd.DataFrame()
        for bs_source in bs_sources:
            def fetch_bs(s):
                return Finance(symbol=ticker, source=s, period='year').balance_sheet(period='year')
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fetch_bs, bs_source)
                try:
                    df_bs = future.result(timeout=4)
                    if df_bs is not None and not df_bs.empty:
                        df_balance = df_bs
                        break
                except Exception:
                    continue

        # Balance sheet quý
        df_balance_q = pd.DataFrame()
        for bs_source in bs_sources:
            def fetch_bs_q(s):
                return Finance(symbol=ticker, source=s, period='quarter').balance_sheet(period='quarter')
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fetch_bs_q, bs_source)
                try:
                    df_bs_q = future.result(timeout=4)
                    if df_bs_q is not None and not df_bs_q.empty:
                        df_balance_q = df_bs_q
                        break
                except Exception:
                    continue

        is_bank = ticker in ['VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB']
        current_price = float(df_price['close_vnd'].iloc[-1])

        # --- [BƯỚC 3]: Chuẩn hoá BCTC ---
        fin5 = build_5y_financial_table(df_income, df_balance, df_ratio, ticker=ticker)

        revenue_series      = normalize_to_billion_vnd(fin5['revenue'])
        equity_series       = normalize_to_billion_vnd(fin5['equity'])
        total_assets_series = normalize_to_billion_vnd(fin5['total_assets'])
        net_profit_series   = normalize_net_profit_with_anchor(
            fin5['net_profit'], equity_series, fin5['roe'])

        eps_series                = fin5['eps']
        bvps_series               = fin5['bvps']
        roe_series                = fin5['roe']
        roa_series                = fin5['roa']
        pe_series                 = fin5['pe']
        pb_series                 = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']
        net_margin_series         = fin5['net_margin']
        asset_turnover_series     = fin5['asset_turnover']

        # Fallback equity
        if equity_series.empty and not total_assets_series.empty:
            total_liab_series = normalize_to_billion_vnd(find_row_series(
                df_balance,
                ['tổng cộng nợ phải trả', 'tổng nợ phải trả', 'total liabilities'],
                exclude_keywords=['vốn chủ sở hữu']))
            if not total_liab_series.empty:
                common_years = total_assets_series.index.intersection(total_liab_series.index)
                if len(common_years) > 0:
                    equity_series = (total_assets_series.loc[common_years] - total_liab_series.loc[common_years])

        # Fallback CafeF (Nhanh, vì file cafef_fallback đã có Timeout riêng)
        if equity_series.empty or total_assets_series.empty:
            current_year = datetime.today().year
            cafef_data = fetch_cafef_balance_sheet_5y(ticker, end_year=current_year)
            if equity_series.empty and not cafef_data['equity'].empty:
                equity_series = cafef_data['equity']
            if total_assets_series.empty and not cafef_data['total_assets'].empty:
                total_assets_series = cafef_data['total_assets']

        # Số CP lưu hành
        market_cap_series_raw = fin5.get('market_cap', pd.Series(dtype=float))
        market_cap_direct = get_latest(market_cap_series_raw, default=0.0)
        if market_cap_direct > 0 and current_price > 0:
            implied_shares_check = market_cap_direct / current_price
            if not (1_000_000 <= implied_shares_check <= 50_000_000_000):
                market_cap_direct = 0.0

        issue_share = get_latest(outstanding_shares_series, default=0.0)
        if issue_share == 0.0 and not df_overview.empty:
            for col in ['issue_share', 'outstanding_shares', 'listed_volume']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    issue_share = float(df_overview[col].iloc[0])
                    break
        if issue_share == 0.0 and not df_overview.empty and 'charter_capital' in df_overview.columns:
            try:
                charter_capital = float(df_overview['charter_capital'].iloc[0])
                issue_share = charter_capital / 10000
            except Exception:
                pass
        if market_cap_direct > 0 and current_price > 0:
            implied_shares_from_cap = market_cap_direct / current_price
            if issue_share > 0:
                diff_pct = abs(implied_shares_from_cap - issue_share) / issue_share
                if diff_pct > 0.20:
                    issue_share = implied_shares_from_cap
            else:
                issue_share = implied_shares_from_cap

        eps_latest  = get_latest(eps_series,  default=0.0)
        bvps_latest = get_latest(bvps_series, default=0.0)
        if bvps_latest == 0.0 and issue_share > 0 and not equity_series.empty:
            bvps_latest = get_latest(equity_series, default=0.0) / issue_share

        def _normalize_pct(series):
            if series is None or series.empty:
                return series
            latest_val = series.iloc[-1]
            if abs(latest_val) < 1:
                return series * 100
            return series

        roe_series = _normalize_pct(roe_series)
        roa_series = _normalize_pct(roa_series)

        if market_cap_direct > 0:
            market_cap = market_cap_direct
        else:
            market_cap = current_price * issue_share if issue_share > 0 else 0.0

        pe_fresh = (current_price / eps_latest)  if eps_latest  > 0 else 0.0
        pb_fresh = (current_price / bvps_latest) if bvps_latest > 0 else 0.0

        clean_metrics = {
            "is_bank": is_bank,
            "current_price": current_price,
            "market_cap_billion": market_cap / 1e9,
            "pe": pe_fresh,
            "pb": pb_fresh,
            "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
            "source_used": source_used,
        }

        # --- [BƯỚC 4]: Bảng KQKD Năm ---
        current_year = datetime.today().year
        existing_years = set(revenue_series.index) | set(net_profit_series.index) | \
                          set(equity_series.index) | set(total_assets_series.index)
        target_years = set(range(2021, min(current_year, 2025) + 1))
        missing_years = sorted(target_years - existing_years)
        
        if missing_years:
            try:
                cafef_full = fetch_cafef_yearly_full(ticker, missing_years, debug=debug_cafef)
                for yr, val in cafef_full['revenue'].items():
                    revenue_series.loc[yr] = val
                for yr, val in cafef_full['net_profit'].items():
                    net_profit_series.loc[yr] = val
                for yr, val in cafef_full['equity'].items():
                    equity_series.loc[yr] = val
                for yr, val in cafef_full['total_assets'].items():
                    total_assets_series.loc[yr] = val
                for yr, val in cafef_full['roe'].items():
                    roe_series.loc[yr] = val
                for yr, val in cafef_full['roa'].items():
                    roa_series.loc[yr] = val
                revenue_series, net_profit_series = revenue_series.sort_index(), net_profit_series.sort_index()
                equity_series, total_assets_series = equity_series.sort_index(), total_assets_series.sort_index()
                roe_series, roa_series = roe_series.sort_index(), roa_series.sort_index()
            except Exception:
                pass

        years_available = sorted(
            set(revenue_series.index) | set(net_profit_series.index) |
            set(equity_series.index)  | set(total_assets_series.index)
        )
        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)']            = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)']         = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)']    = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)']              = df_5y_table['Năm'].map(eps_series)
        df_5y_table['BVPS (đ)']             = df_5y_table['Năm'].map(bvps_series)
        df_5y_table['ROE (%)']              = df_5y_table['Năm'].map(lambda y: roe_series.get(y, None))
        df_5y_table['ROA (%)']              = df_5y_table['Năm'].map(lambda y: roa_series.get(y, None))

        revenue_cagr    = cagr(get_latest_n_years(revenue_series,    5))
        net_profit_cagr = cagr(get_latest_n_years(net_profit_series, 5))

        fundamentals_summary = {
            "revenue_cagr_pct":    revenue_cagr    * 100 if revenue_cagr    is not None else None,
            "net_profit_cagr_pct": net_profit_cagr * 100 if net_profit_cagr is not None else None,
            "eps_latest":  eps_latest,
            "bvps_latest": bvps_latest,
            "roe_latest":  get_latest(roe_series, default=None),
            "roa_latest":  get_latest(roa_series, default=None),
        }

        # --- [BƯỚC 4b]: Bảng KQKD theo Quý ---
        df_quarter_table = pd.DataFrame()
        try:
            fin_q = build_financial_table(df_income_q, df_balance_q, df_ratio_q, ticker=ticker, period='quarter')

            revenue_series_q      = normalize_to_billion_vnd(fin_q['revenue'])
            equity_series_q       = normalize_to_billion_vnd(fin_q['equity'])
            total_assets_series_q = normalize_to_billion_vnd(fin_q['total_assets'])
            net_profit_series_q   = normalize_net_profit_with_anchor(
                fin_q['net_profit'], equity_series_q, fin_q['roe'])

            eps_series_q  = fin_q['eps']
            bvps_series_q = fin_q['bvps']

            def _normalize_pct_q(series):
                if series is None or series.empty:
                    return series
                latest_val = series.iloc[-1]
                if abs(latest_val) < 1:
                    return series * 100
                return series

            roe_series_q = _normalize_pct_q(fin_q['roe'])
            roa_series_q = _normalize_pct_q(fin_q['roa'])

            def _quarter_range(start_y, start_q, end_y, end_q):
                out = []
                y, q = start_y, start_q
                while (y, q) <= (end_y, end_q):
                    out.append((y, q))
                    q += 1
                    if q > 4:
                        q = 1
                        y += 1
                return out

            existing_q_keys = set(revenue_series_q.index) | set(net_profit_series_q.index) | \
                               set(equity_series_q.index) | set(total_assets_series_q.index)
            today = datetime.today()
            cur_q = (today.month - 1) // 3 + 1
            all_target_quarters = _quarter_range(2022, 1, today.year, cur_q)
            missing_quarters = [
                (y, q) for (y, q) in all_target_quarters
                if f"{y}-Q{q}" not in existing_q_keys
            ]
            if missing_quarters:
                try:
                    cafef_q = fetch_cafef_quarterly_full(ticker, missing_quarters, debug=debug_cafef)
                    for key, val in cafef_q['revenue'].items():
                        revenue_series_q.loc[key] = val
                    for key, val in cafef_q['net_profit'].items():
                        net_profit_series_q.loc[key] = val
                    for key, val in cafef_q['equity'].items():
                        equity_series_q.loc[key] = val
                    for key, val in cafef_q['total_assets'].items():
                        total_assets_series_q.loc[key] = val
                except Exception:
                    pass

            quarters_available = sorted(
                set(revenue_series_q.index) | set(net_profit_series_q.index) |
                set(equity_series_q.index)  | set(total_assets_series_q.index),
                key=lambda c: (int(str(c).split('-Q')[0]), int(str(c).split('-Q')[1]))
            )

            df_quarter_table = pd.DataFrame({'_period': quarters_available})
            df_quarter_table['Quý'] = df_quarter_table['_period'].apply(
                lambda c: f"Q{str(c).split('-Q')[1]}/{str(c).split('-Q')[0]}")
            df_quarter_table['Doanh thu thuần (tỷ)'] = df_quarter_table['_period'].map(revenue_series_q)
            df_quarter_table['LNST (tỷ)']            = df_quarter_table['_period'].map(net_profit_series_q)
            df_quarter_table['Vốn CSH (tỷ)']         = df_quarter_table['_period'].map(equity_series_q)
            df_quarter_table['Tổng tài sản (tỷ)']    = df_quarter_table['_period'].map(total_assets_series_q)
            df_quarter_table['EPS (đ)']              = df_quarter_table['_period'].map(eps_series_q)
            df_quarter_table['BVPS (đ)']
