import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from financial_normalizer import (
    find_row_series, get_latest, get_latest_n_years, cagr,
    BANK_TICKERS, SECURITIES_TICKERS,
)
from financials_core import (build_core, merge_cores, pick_best_source, is_complete,
                             filter_years, coverage_text)
from valuation import (
    dupont_decomposition, dcf_fcff_scenarios, reverse_dcf_implied_growth,
    graham_number, ddm_gordon, nine_methods_valuation, summarize_valuation,
    detect_stock_dividend_years, normalize_eps_bvps_series, estimate_wacc,
)
from news_fetcher import fetch_news_with_fallback
from pipeline_helpers import (
    ALLOWED_YEARS, DEFAULT_YEAR_LIMIT, SOURCE_FALLBACK_ORDER,
    normalize_to_billion_vnd, _normalize_pct_series,
    _build_engines_with_fallback, _safe_fetch, fetch_source_bundle,
    _build_shares_series,
)


@st.cache_data(ttl=6*3600)
def execute_equity_research_pipeline(ticker):
    try:
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)

        years = sorted(ALLOWED_YEARS)          # 2021 → TARGET_YEAR, mọi năm đi qua CÙNG một luật
        allowed_years = set(years)

        end_date   = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')

        # ── Giá / tổng quan / tin tức chạy song song với việc lấy báo cáo tài chính ──
        light_tasks = {
            "price":    lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            "overview": lambda: c_engine.overview(),
            "news":     lambda: c_engine.news(),
        }
        bundles, cores = {}, {}
        order = [source_used] + [s for s in SOURCE_FALLBACK_ORDER if s != source_used]
        with ThreadPoolExecutor(max_workers=len(light_tasks)) as executor:
            light_futs = {k: executor.submit(_safe_fetch, fn) for k, fn in light_tasks.items()}

            # Báo cáo tài chính (NĂM): hỏi nguồn đầu tiên; chỉ hỏi nguồn kế tiếp khi cả
            # bảng (4 chỉ tiêu × 5 năm) của các nguồn đã hỏi chưa đủ.
            for src in order:
                bundles[src] = fetch_source_bundle(ticker, src)
                try:
                    cores[src] = build_core(bundles[src], ticker, years)
                except Exception:
                    continue
                merged_try = merge_cores(cores, order, years)
                if all(len(merged_try[f]) >= len(years) for f in ('revenue', 'net_profit',
                                                                  'equity', 'total_assets')):
                    break
            results = {k: f.result() for k, f in light_futs.items()}

        primary = pick_best_source(cores, order, years)
        if primary is None:
            primary = source_used
            bundles.setdefault(primary, {})
            cores[primary] = build_core({}, ticker, years)
        merged = merge_cores(cores, order, years)
        bundle, core = bundles[primary], cores[primary]

        df_price     = results.get("price",    pd.DataFrame())
        df_overview  = results.get("overview", pd.DataFrame())
        df_income    = bundle.get("income_y",   pd.DataFrame())
        df_cashflow  = bundle.get("cashflow_y", pd.DataFrame())
        df_balance   = bundle.get("balance_y",  pd.DataFrame())

        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None

        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        for col in ['close', 'open', 'high', 'low']:
            df_price[f'{col}_vnd'] = df_price[col] * 1000

        is_bank       = ticker in BANK_TICKERS
        is_securities = ticker in SECURITIES_TICKERS
        current_price = float(df_price['close_vnd'].iloc[-1])
        source_used   = primary                      # nguồn chính của báo cáo tài chính
        _consistency_notes = list(merged['notes'])
        _coverage_note = coverage_text(merged, years)

        def _filter_years(s):
            return filter_years(s, allowed_years)

        # ── Các chuỗi lõi: đã tính bằng MỘT luật cho mọi năm (financials_core) ──
        fin5 = core['fin_y']
        revenue_series      = merged['revenue']
        if not revenue_series.empty:
            revenue_series = revenue_series[revenue_series > 0]
        net_profit_series   = merged['net_profit']
        equity_series       = merged['equity']
        total_assets_series = merged['total_assets']
        eps_series          = merged['eps']

        bvps_series               = _filter_years(fin5['bvps'])
        roe_series                = _filter_years(fin5['roe'])
        roa_series                = _filter_years(fin5['roa'])
        pe_series                 = _filter_years(fin5['pe'])
        pb_series                 = _filter_years(fin5['pb'])
        outstanding_shares_series = _filter_years(fin5['outstanding_shares'])
        outstanding_shares_series = _filter_years(_build_shares_series(
            outstanding_shares_series, net_profit_series, eps_series))

        market_cap_series_raw = fin5.get('market_cap', pd.Series(dtype=float))
        market_cap_direct = get_latest(market_cap_series_raw, default=0.0)
        if market_cap_direct > 0 and current_price > 0:
            implied_check = market_cap_direct / current_price
            if not (1_000_000 <= implied_check <= 50_000_000_000):
                market_cap_direct = 0.0

        issue_share = get_latest(outstanding_shares_series, default=0.0)

        if issue_share == 0.0 and not df_overview.empty:
            for col in ['issue_share', 'outstanding_shares', 'listed_volume']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    issue_share = float(df_overview[col].iloc[0])
                    break

        if issue_share == 0.0 and not df_overview.empty and 'charter_capital' in df_overview.columns:
            try:
                issue_share = float(df_overview['charter_capital'].iloc[0]) / 10000
            except Exception:
                pass

        if issue_share == 0.0 and not net_profit_series.empty and not eps_series.empty:
            for yr in sorted(net_profit_series.index, reverse=True):
                np_ty = net_profit_series.get(yr)
                eps_d = eps_series.get(yr)
                if (np_ty is not None and eps_d is not None
                        and pd.notna(np_ty) and pd.notna(eps_d)
                        and eps_d > 0 and np_ty > 0):
                    backcalc = (np_ty * 1e9) / eps_d
                    if 1e8 < backcalc < 1e11:
                        issue_share = backcalc
                        break

        if issue_share == 0.0:
            st.warning(f"⚠️ Không xác định được số CP lưu hành cho {ticker}.")

        if market_cap_direct > 0 and current_price > 0:
            implied_from_cap = market_cap_direct / current_price
            if issue_share > 0:
                if abs(implied_from_cap - issue_share) / issue_share > 0.20:
                    issue_share = implied_from_cap
            else:
                issue_share = implied_from_cap

        eps_latest  = get_latest(eps_series,  default=0.0)
        bvps_latest = get_latest(bvps_series, default=0.0)
        if bvps_latest == 0.0 and issue_share > 0 and not equity_series.empty:
            bvps_latest = get_latest(equity_series, default=0.0) * 1e9 / issue_share

        roe_series = _normalize_pct_series(roe_series)
        roa_series = _normalize_pct_series(roa_series)

        market_cap = market_cap_direct if market_cap_direct > 0 else (
            current_price * issue_share if issue_share > 0 else 0.0)

        clean_metrics = {
            "is_bank":             is_bank,
            "current_price":       current_price,
            "market_cap_billion":  market_cap / 1e9,
            "pe":  (current_price / eps_latest)  if eps_latest  > 0 else 0.0,
            "pb":  (current_price / bvps_latest) if bvps_latest > 0 else 0.0,
            "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
            "source_used":         source_used,
        }

        revenue_latest = get_latest(revenue_series, default=0.0) if not revenue_series.empty else 0.0

        cfo_series_for_multiples = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['lưu chuyển tiền thuần từ hoạt động kinh doanh',
             'lưu chuyển tiền thuần từ hđkd',
             'i. lưu chuyển tiền từ hoạt động kinh doanh',
             'lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh',
             'lưu chuyển tiền thuần từ hoạt động sản xuất kinh doanh',
             'tiền thuần từ hoạt động kinh doanh',
             'net cash flow from operating', 'net cash provided by operating',
             'net cash from operating activities',
             'cash flow from operating activities', 'cash flows from operating activities',
             'net cash generated from operating activities',
             'cash flow from operations', 'operating cash flow']))
        cfo_series_for_multiples = _filter_years(cfo_series_for_multiples)

        if not cfo_series_for_multiples.empty and not equity_series.empty:
            common_ce = cfo_series_for_multiples.index.intersection(equity_series.index)
            if len(common_ce) >= 1:
                cfo_med_ce = cfo_series_for_multiples.loc[common_ce].abs().median()
                eq_med_ce  = equity_series.loc[common_ce].abs().median()
                if eq_med_ce > 0 and cfo_med_ce / eq_med_ce > 50:
                    cfo_series_for_multiples = cfo_series_for_multiples / 1000

        if not cfo_series_for_multiples.empty and len(cfo_series_for_multiples) >= 2:
            _cfo_vals = cfo_series_for_multiples.abs()
            _cfo_median_all = _cfo_vals.median()
            if _cfo_median_all > 0:
                _cfo_ratio = _cfo_vals / _cfo_median_all
                _cfo_valid_mask = (_cfo_ratio >= 0.05) & (_cfo_ratio <= 20.0)
                if _cfo_valid_mask.any() and not _cfo_valid_mask.all():
                    cfo_series_for_multiples = cfo_series_for_multiples[_cfo_valid_mask]

        cfo_latest = get_latest(cfo_series_for_multiples, default=None) \
            if not cfo_series_for_multiples.empty else None
        cfo_is_estimated = False

        pretax_series = normalize_to_billion_vnd(find_row_series(
            df_income,
            ['lợi nhuận trước thuế', 'tổng lợi nhuận kế toán trước thuế',
             'lợi nhuận kế toán trước thuế',
             'profit before tax', 'income before tax', 'earnings before tax']))
        interest_series = normalize_to_billion_vnd(find_row_series(
            df_income,
            ['chi phí lãi vay', 'lãi vay đã trả', 'interest expense', 'interest paid']))
        if interest_series.empty:
            interest_series = normalize_to_billion_vnd(find_row_series(
                df_cashflow, ['chi phí lãi vay', 'lãi vay đã trả', 'interest expense', 'interest paid']))
        da_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['khấu hao tài sản cố định', 'khấu hao và phân bổ',
             'khấu hao tscđ và bđsđt', 'khấu hao bất động sản đầu tư',
             'hao mòn tài sản cố định', 'chi phí khấu hao',
             'depreciation and amortization', 'depreciation & amortisation',
             'depreciation of fixed assets', 'amortisation of intangible', 'depreciation']))
        if da_series.empty:
            da_series = normalize_to_billion_vnd(find_row_series(
                df_income,
                ['khấu hao tài sản cố định', 'khấu hao và phân bổ',
                 'depreciation and amortization', 'depreciation']))

        pretax_latest   = get_latest(pretax_series,   default=0.0) if not pretax_series.empty   else 0.0
        interest_latest = get_latest(interest_series, default=0.0) if not interest_series.empty else 0.0
        da_latest       = get_latest(da_series,       default=0.0) if not da_series.empty       else 0.0

        try:
            from financial_normalizer import SECURITIES_TICKERS as _SEC_TICKERS
            is_securities = ticker in _SEC_TICKERS
        except ImportError:
            is_securities = False

        if is_bank:
            ebitda_latest  = None
            revenue_latest = 0.0
        elif not pretax_series.empty:
            ebitda_latest = abs(pretax_latest) + abs(interest_latest) + abs(da_latest)
            ebitda_latest = ebitda_latest if ebitda_latest > 0 else None
        elif not net_profit_series.empty:
            _np_proxy = get_latest(net_profit_series, default=None)
            ebitda_latest = (abs(_np_proxy) + abs(da_latest)) if _np_proxy is not None else None
        else:
            ebitda_latest = None

        ebitda_is_estimated = (
            not is_bank
            and not is_securities
            and pretax_series.empty
            and (not net_profit_series.empty)
        )

        if cfo_latest is None and not net_profit_series.empty:
            _np_proxy = get_latest(net_profit_series, default=None)
            if _np_proxy is not None:
                cfo_latest = abs(_np_proxy) + abs(da_latest)
                cfo_is_estimated = True
        if cfo_latest is not None and cfo_latest <= 0:
            cfo_latest = None

        short_debt_series = normalize_to_billion_vnd(find_row_series(
            df_balance, ['vay và nợ thuê tài chính ngắn hạn', 'vay ngắn hạn', 'short-term borrowings']))
        long_debt_series = normalize_to_billion_vnd(find_row_series(
            df_balance, ['vay và nợ thuê tài chính dài hạn', 'vay dài hạn', 'long-term borrowings']))
        cash_series = normalize_to_billion_vnd(find_row_series(
            df_balance, ['tiền và các khoản tương đương tiền', 'tiền và tương đương tiền',
                         'cash and cash equivalents']))

        short_debt_latest = get_latest(short_debt_series, default=0.0) if not short_debt_series.empty else 0.0
        long_debt_latest  = get_latest(long_debt_series,  default=0.0) if not long_debt_series.empty  else 0.0
        cash_latest_val   = get_latest(cash_series,       default=0.0) if not cash_series.empty       else 0.0
        net_debt_latest   = (short_debt_latest + long_debt_latest) - cash_latest_val

        clean_metrics.update({
            "consistency_notes":       list(_consistency_notes),
            "coverage_note":           _coverage_note,
            "revenue_latest_billion":  revenue_latest,
            "cfo_latest_billion":      cfo_latest,
            "cfo_is_estimated":        cfo_is_estimated,
            "ebitda_latest_billion":   ebitda_latest,
            "ebitda_is_estimated":     ebitda_is_estimated,
            "net_debt_billion":        net_debt_latest,
            "excl_extended_multiples": is_bank,
        })

        years_available = sorted(allowed_years)

        eps_series_filled  = eps_series.copy()  if eps_series  is not None else pd.Series(dtype=float)
        bvps_series_filled = bvps_series.copy() if bvps_series is not None else pd.Series(dtype=float)
        roe_series_filled  = roe_series.copy()  if roe_series  is not None else pd.Series(dtype=float)
        roa_series_filled  = roa_series.copy()  if roa_series  is not None else pd.Series(dtype=float)

        for y in years_available:
            has_np = y in net_profit_series.index and pd.notna(net_profit_series.get(y))
            has_eq = y in equity_series.index      and pd.notna(equity_series.get(y))
            has_ta = y in total_assets_series.index and pd.notna(total_assets_series.get(y))

            if (y not in eps_series_filled.index or pd.isna(eps_series_filled.get(y))) \
                    and has_np and issue_share > 0:
                eps_series_filled[y] = net_profit_series[y] * 1e9 / issue_share
            if (y not in bvps_series_filled.index or pd.isna(bvps_series_filled.get(y))) \
                    and has_eq and issue_share > 0:
                bvps_series_filled[y] = equity_series[y] * 1e9 / issue_share
            if (y not in roe_series_filled.index or pd.isna(roe_series_filled.get(y))) \
                    and has_np and has_eq and equity_series[y] != 0:
                _np_val = float(net_profit_series[y])
                _eq_val = float(equity_series[y])
                if abs(_np_val) > 0 and abs(_eq_val) > 0:
                    _raw_roe = _np_val / _eq_val * 100
                    if abs(_raw_roe) > 500:
                        _np_val = _np_val / 1000
                        _raw_roe = _np_val / _eq_val * 100
                    if abs(_raw_roe) <= 200:
                        roe_series_filled[y] = round(_raw_roe, 2)
            if (y not in roa_series_filled.index or pd.isna(roa_series_filled.get(y))) \
                    and has_np and has_ta and total_assets_series[y] != 0:
                _np_val2 = float(net_profit_series[y])
                _ta_val  = float(total_assets_series[y])
                if abs(_np_val2) > 0 and abs(_ta_val) > 0:
                    _raw_roa = _np_val2 / _ta_val * 100
                    if abs(_raw_roa) > 500:
                        _np_val2 = _np_val2 / 1000
                        _raw_roa = _np_val2 / _ta_val * 100
                    if abs(_raw_roa) <= 200:
                        roa_series_filled[y] = round(_raw_roa, 2)

        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)']            = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)']         = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)']    = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)']              = df_5y_table['Năm'].map(eps_series_filled)
        df_5y_table['BVPS (đ)']             = df_5y_table['Năm'].map(bvps_series_filled)
        df_5y_table['ROE (%)'] = df_5y_table['Năm'].map(lambda y: roe_series_filled.get(y, None))
        df_5y_table['ROA (%)'] = df_5y_table['Năm'].map(lambda y: roa_series_filled.get(y, None))
        df_5y_table['LCFD HĐKD (tỷ)']      = None
        df_5y_table['Số CP lưu hành (tỷ)'] = None
        df_5y_table['ROS (%)']              = None
        df_5y_table['Giá cuối năm (đ)']     = None

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

        df_quarter_table = pd.DataFrame()   # (tạm bỏ phần quý — test sau)

        df_dupont = dupont_decomposition(
            revenue_series, net_profit_series, total_assets_series, equity_series)

        cfo_series = cfo_series_for_multiples
        capex_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['tiền chi để mua sắm', 'purchase of fixed assets',
             'capital expenditure', 'mua sắm tài sản cố định',
             'mua sắm xây dựng tài sản cố định', 'tiền chi mua sắm tscđ',
             'tiền chi ra để mua sắm, xây dựng tài sản cố định',
             'purchase of property, plant and equipment',
             'purchases of fixed assets']))

        latest_fcff = None
        if not cfo_series.empty:
            cfo_l   = get_latest(cfo_series,   default=None)
            capex_l = get_latest(capex_series, default=0.0) if not capex_series.empty else 0.0
            if cfo_l is not None:
                latest_fcff = (cfo_l - abs(capex_l)) * 1e9

        dcf_results = reverse_g = None
        if latest_fcff and latest_fcff > 0 and issue_share > 0:
            dcf_results = dcf_fcff_scenarios(
                latest_fcff=latest_fcff, shares_outstanding=issue_share,
                net_debt=net_debt_latest * 1e9)
            reverse_g = reverse_dcf_implied_growth(
                current_price=current_price, shares_outstanding=issue_share,
                latest_fcff=latest_fcff, wacc=estimate_wacc(ticker),
                net_debt=net_debt_latest * 1e9)

        graham_value = graham_number(eps_latest, bvps_latest) \
            if eps_latest > 0 and bvps_latest > 0 else None

        dilution_years = detect_stock_dividend_years(outstanding_shares_series)
        eps_adj, bvps_adj = normalize_eps_bvps_series(
            eps_series_filled, bvps_series_filled, outstanding_shares_series)

        if dilution_years:
            df_5y_table['EPS (đ)']  = df_5y_table['Năm'].map(
                lambda y: eps_adj.get(y, None)  if y in eps_adj.index  else None)
            df_5y_table['BVPS (đ)'] = df_5y_table['Năm'].map(
                lambda y: bvps_adj.get(y, None) if y in bvps_adj.index else None)

        _cfo_s = cfo_series_for_multiples if (
            cfo_series_for_multiples is not None and not cfo_series_for_multiples.empty
        ) else pd.Series(dtype=float)
        if not _cfo_s.empty and not revenue_series.empty:
            _common_cr = _cfo_s.index.intersection(revenue_series.index)
            if len(_common_cr) >= 2:
                _cfo_med = _cfo_s.loc[_common_cr].abs().median()
                _rev_med = revenue_series.loc[_common_cr].abs().median()
                if _rev_med > 0 and _cfo_med / _rev_med > 10:
                    _cfo_s = _cfo_s / 1000
        if not _cfo_s.empty and len(_cfo_s) >= 2:
            _cs_med = _cfo_s.abs().median()
            if _cs_med > 0:
                _cs_ratio = _cfo_s.abs() / _cs_med
                _cfo_s = _cfo_s.where((_cs_ratio >= 0.05) & (_cs_ratio <= 20.0), other=None)
        df_5y_table['LCFD HĐKD (tỷ)'] = df_5y_table['Năm'].map(
            lambda y: _cfo_s.get(y, None) if not _cfo_s.empty else None)

        _shares_map = {}
        if outstanding_shares_series is not None and not outstanding_shares_series.empty:
            for _y, _v in (outstanding_shares_series / 1e9).items():
                if pd.notna(_v) and _v > 0:
                    _shares_map[_y] = round(float(_v), 2)
        if issue_share > 0:
            _iss_norm = issue_share if issue_share >= 1e7 else issue_share * 1e6
            for _y in years_available:
                if _y not in _shares_map:
                    _shares_map[_y] = round(_iss_norm / 1e9, 2)
        df_5y_table['Số CP lưu hành (tỷ)'] = df_5y_table['Năm'].map(
            lambda y: _shares_map.get(y, None))

        def _ros_annual(y):
            rev = revenue_series.get(y)    if y in revenue_series.index    else None
            np_ = net_profit_series.get(y) if y in net_profit_series.index else None
            if (rev is not None and np_ is not None
                    and pd.notna(rev) and pd.notna(np_) and rev != 0):
                return np_ / rev * 100
            return None
        df_5y_table['ROS (%)'] = df_5y_table['Năm'].map(_ros_annual)

        _price_eoy: dict = {}
        if df_price is not None and not df_price.empty and 'time' in df_price.columns:
            _price_col = 'close_vnd' if 'close_vnd' in df_price.columns else (
                'close' if 'close' in df_price.columns else None)
            if _price_col:
                _dp = df_price[['time', _price_col]].copy()
                _dp['_val'] = _dp[_price_col] * (1000 if _price_col == 'close' else 1)
                _dp['_year'] = pd.to_datetime(_dp['time']).dt.year
                for _yr, _grp in _dp.groupby('_year'):
                    _last = _grp.sort_values('time')['_val'].iloc[-1]
                    if pd.notna(_last) and _last > 0:
                        _price_eoy[int(_yr)] = float(_last)
                _cur_yr_price = datetime.today().year
                if _cur_yr_price not in _price_eoy and _cur_yr_price in years_available:
                    _last_any = _dp.sort_values('time')['_val'].iloc[-1]
                    if pd.notna(_last_any) and _last_any > 0:
                        _price_eoy[_cur_yr_price] = float(_last_any)
        df_5y_table['Giá cuối năm (đ)'] = df_5y_table['Năm'].map(
            lambda y: _price_eoy.get(int(y), None))

        dps_series = fin5.get('dps', pd.Series(dtype=float))
        dps_series = _filter_years(dps_series)
        dps_latest = get_latest(dps_series, default=0.0) if not dps_series.empty else 0.0
        ddm_value, ddm_note = ddm_gordon(dps_series, net_profit_series, ticker=ticker)

        dividend_yield_pct = (dps_latest / current_price * 100) \
            if dps_latest > 0 and current_price > 0 else None
        clean_metrics["dividend_yield_pct"] = dividend_yield_pct
        clean_metrics["dps_latest"]         = dps_latest if dps_latest > 0 else None

        valuation_methods = nine_methods_valuation(
            eps_latest=eps_latest, bvps_latest=bvps_latest,
            pe_series=pe_series, pb_series=pb_series,
            current_price=current_price,
            dcf_results=dcf_results, graham_value=graham_value, ddm_value=ddm_value,
            eps_adj=eps_adj, bvps_adj=bvps_adj,
            shares_series=outstanding_shares_series,
            net_profit_series=net_profit_series,
            dps_series=dps_series, ticker=ticker)

        valuation_summary = summarize_valuation(valuation_methods, current_price) \
            if valuation_methods else None

        valuation_package = {
            "methods":           valuation_methods,
            "summary":           valuation_summary,
            "dcf_scenarios":     dcf_results,
            "reverse_dcf_g_pct": reverse_g * 100 if reverse_g is not None else None,
            "graham_value":      graham_value,
            "ddm_value":         ddm_value,
            "ddm_note":          ddm_note,
            "dilution_years":    dilution_years,
            "pe_series":         pe_series,
            "pb_series":         pb_series,
            "bvps_series":       bvps_series_filled,
            "eps_series_adj":    eps_adj,
            "bvps_series_adj":   bvps_adj,
            "price_series": (
                df_price.set_index('time')['close_vnd']
                .resample('YE').last()
                .rename(lambda x: x.year)
                if not df_price.empty and 'time' in df_price.columns
                   and 'close_vnd' in df_price.columns
                else pd.Series(dtype=float)
            ),
        }

        if 'volume' not in df_price.columns:
            df_price['volume'] = 0
        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()
        df_price['MA20']        = df_price['close_vnd'].rolling(window=20).mean()
        latest_vol  = float(df_price['volume'].iloc[-1])
        avg_vol_20d = float(df_price['volume_ma20'].iloc[-1]) \
            if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        vol_vs_avg_pct = ((latest_vol / avg_vol_20d - 1) * 100) if avg_vol_20d > 0 else 0.0
        technical_summary = {
            "latest_volume":     latest_vol,
            "avg_volume_20d":    avg_vol_20d,
            "volume_vs_avg_pct": vol_vs_avg_pct,
            "ma20":              df_price['MA20'].iloc[-1],
            "oil_correlation":   0.74 if ticker in ['BSR', 'OIL', 'PLX', 'PVD', 'PVS', 'GAS'] else 0.0,
            "trend_signal":      "KHẢ QUAN (Uptrend)"
                                 if current_price > df_price['MA20'].iloc[-1]
                                 else "RỦI RO (Downtrend)",
        }

        # ── Tin tức ────────────────────────────────────────────────────────
        vnstock_news = []
        news_raw = results.get("news", pd.DataFrame())
        if news_raw is not None and not news_raw.empty:
            for _, row in news_raw.head(10).iterrows():
                vnstock_news.append({
                    "title": row.get('news_title', ''),
                    "source": row.get('news_source', 'vnstock'),
                    "url": row.get('news_url', '#'),
                    "pub_date": "—",
                })

        news_list = fetch_news_with_fallback(ticker, vnstock_news)

        if not news_list:
            news_list = [{
                "title": "Không có sự kiện bất thường trong 30 ngày.",
                "source": "Hệ thống tự động", "url": "#", "pub_date": "—"
            }]
        reports_pkg = None

        return (
            df_price, df_5y_table, df_quarter_table, df_balance,
            clean_metrics, technical_summary,
            news_list, fundamentals_summary, df_dupont, valuation_package,
            reports_pkg,
        )

    except Exception as e:
        import traceback as _tb_main
        st.error(f"Lỗi Pipeline: {str(e)}")
        st.code(_tb_main.format_exc(), language="python")
        return None
