import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company

from financial_normalizer import (
    find_row_series, build_5y_financial_table, get_latest,
    get_latest_n_years, cagr,
)
from valuation import (
    dupont_decomposition, dcf_fcff_scenarios, reverse_dcf_implied_growth,
    graham_number, ddm_gordon, nine_methods_valuation, summarize_valuation,
)
from cafef_fallback import fetch_cafef_balance_sheet_5y

SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']


def normalize_to_billion_vnd(series):
    """Chuẩn hoá Series về đơn vị tỷ VNĐ."""
    if series is None or series.empty:
        return series
    
    # Detect đơn vị dựa vào median của series (tránh bị lệch bởi outlier)
    import numpy as np
    sample = series.dropna()
    if sample.empty:
        return series
    median_val = float(sample.median())
    
    def _to_ty(val):
        try:
            if pd.isna(val):
                return None
            val = float(val)
            # median > 1e11 → đơn vị đồng → chia 1e9
            if abs(median_val) > 1e11:
                return round(val / 1e9, 2)
            # median > 1e8 → đơn vị triệu → chia 1e3
            elif abs(median_val) > 1e8:
                return round(val / 1e3, 2)
            # median < 1e6 → đã ở tỷ → giữ nguyên
            else:
                return round(val, 2)
        except Exception:
            return None
    
    return series.map(_to_ty).dropna()


def _build_engines_with_fallback(ticker):
    last_error = None
    test_end = datetime.today().strftime('%Y-%m-%d')
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


def _safe_call(fn, label, source_used, default=None):
    try:
        result = fn()
        return result if result is not None else (default if default is not None else pd.DataFrame())
    except Exception as e:
        st.warning(f"Không lấy được {label}() từ nguồn {source_used}: {e}")
        return default if default is not None else pd.DataFrame()


@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker):
    try:
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)
        if source_used != 'VCI':
            st.info(f"ℹ️ Nguồn VCI không khả dụng cho mã {ticker}, đang dùng nguồn dự phòng: {source_used}")

        # --- [BƯỚC 1]: Lịch sử Giá ---
        end_date = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')
        df_price = q_engine.history(start=start_date, end=end_date, interval='1D')

        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None

        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        df_price['close_vnd'] = df_price['close'] * 1000
        df_price['open_vnd']  = df_price['open']  * 1000
        df_price['high_vnd']  = df_price['high']  * 1000
        df_price['low_vnd']   = df_price['low']   * 1000

        # --- [BƯỚC 2]: Thu thập BCTC ---
        df_overview  = _safe_call(lambda: c_engine.overview(), 'overview', source_used)
        df_income    = _safe_call(lambda: f_engine.income_statement(period='year'), 'income_statement', source_used)
        df_cashflow  = _safe_call(lambda: f_engine.cash_flow(period='year'), 'cash_flow', source_used)
        df_ratio     = _safe_call(lambda: f_engine.ratio(period='year'), 'ratio', source_used)

        # Balance sheet: thử tất cả nguồn vì KBS thường thiếu cho mã UPCOM
        df_balance = pd.DataFrame()
        for bs_source in ['VCI', 'KBS', 'DNSE']:
            try:
                f_bs = Finance(symbol=ticker, source=bs_source, period='year')
                df_bs = f_bs.balance_sheet(period='year')
                if df_bs is not None and not df_bs.empty:
                    df_balance = df_bs
                    break
            except Exception:
                continue

        is_bank = ticker in ['VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB']
        current_price = float(df_price['close_vnd'].iloc[-1])

        # --- [BƯỚC 3]: Chuẩn hoá BCTC ---
        fin5 = build_5y_financial_table(df_income, df_balance, df_ratio)

        revenue_series       = normalize_to_billion_vnd(fin5['revenue'])
        net_profit_series    = normalize_to_billion_vnd(fin5['net_profit'])
        equity_series        = normalize_to_billion_vnd(fin5['equity'])
        total_assets_series  = normalize_to_billion_vnd(fin5['total_assets'])
        eps_series           = fin5['eps']
        bvps_series          = fin5['bvps']
        roe_series           = fin5['roe']
        roa_series           = fin5['roa']
        pe_series            = fin5['pe']
        pb_series            = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']
        net_margin_series    = fin5['net_margin']
        asset_turnover_series = fin5['asset_turnover']

        # Fallback equity = total_assets - total_liabilities
        if equity_series.empty and not total_assets_series.empty:
            total_liab_series = normalize_to_billion_vnd(find_row_series(
                df_balance,
                ['tổng cộng nợ phải trả', 'tổng nợ phải trả', 'total liabilities'],
                exclude_keywords=['vốn chủ sở hữu']))
            if not total_liab_series.empty:
                common_years = total_assets_series.index.intersection(total_liab_series.index)
                if len(common_years) > 0:
                    equity_series = (total_assets_series.loc[common_years] - total_liab_series.loc[common_years])

        # Fallback CafeF nếu vẫn thiếu
        if equity_series.empty or total_assets_series.empty:
            current_year = datetime.today().year
            cafef_data = fetch_cafef_balance_sheet_5y(ticker, end_year=current_year)
            if equity_series.empty and not cafef_data['equity'].empty:
                equity_series = cafef_data['equity']
                st.info(f"ℹ️ Đã lấy 'Vốn chủ sở hữu' cho {ticker} từ CafeF.")
            if total_assets_series.empty and not cafef_data['total_assets'].empty:
                total_assets_series = cafef_data['total_assets']
                st.info(f"ℹ️ Đã lấy 'Tổng tài sản' cho {ticker} từ CafeF.")

        if equity_series.empty:
            st.warning(f"⚠️ Không dò được 'Vốn chủ sở hữu' cho {ticker} từ vnstock ({source_used}) và cả CafeF. Các chỉ số BVPS/DuPont/9PP liên quan sẽ thiếu hoặc không chính xác.")
        if total_assets_series.empty:
            st.warning(f"⚠️ Không dò được 'Tổng tài sản' cho {ticker} từ vnstock ({source_used}) và cả CafeF. DuPont sẽ không tính được.")

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

        # --- [BƯỚC 4]: Bảng KQKD 5 năm ---
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

        # --- [BƯỚC 5]: DuPont ---
        df_dupont = dupont_decomposition(revenue_series, net_profit_series, total_assets_series, equity_series)

        # --- [BƯỚC 6]: DCF, Graham, DDM ---
        cfo_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['lưu chuyển tiền thuần từ hoạt động kinh doanh', 'net cash flow from operating', 'cash flow from operating activities']))
        capex_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['tiền chi để mua sắm', 'purchase of fixed assets', 'capital expenditure', 'mua sắm tài sản cố định']))

        latest_fcff = None
        if not cfo_series.empty:
            cfo_latest   = get_latest(cfo_series,   default=None)
            capex_latest = get_latest(capex_series, default=0.0) if not capex_series.empty else 0.0
            if cfo_latest is not None:
                latest_fcff = (cfo_latest - abs(capex_latest)) * 1e9

        dcf_results = None
        reverse_g   = None
        if latest_fcff and latest_fcff > 0 and issue_share > 0:
            dcf_results = dcf_fcff_scenarios(latest_fcff=latest_fcff, shares_outstanding=issue_share, net_debt=0.0)
            reverse_g   = reverse_dcf_implied_growth(
                current_price=current_price, shares_outstanding=issue_share,
                latest_fcff=latest_fcff, wacc=0.105, net_debt=0.0)

        graham_value = graham_number(eps_latest, bvps_latest) if eps_latest > 0 and bvps_latest > 0 else None
        dps_latest   = None
        ddm_value    = ddm_gordon(dps_latest) if dps_latest else None

        valuation_methods = nine_methods_valuation(
            eps_latest=eps_latest, bvps_latest=bvps_latest,
            pe_series=pe_series, pb_series=pb_series,
            current_price=current_price,
            dcf_results=dcf_results, graham_value=graham_value, ddm_value=ddm_value,
        )
        valuation_summary = summarize_valuation(valuation_methods, current_price) if valuation_methods else None

        valuation_package = {
            "methods":          valuation_methods,
            "summary":          valuation_summary,
            "dcf_scenarios":    dcf_results,
            "reverse_dcf_g_pct": reverse_g * 100 if reverse_g is not None else None,
            "graham_value":     graham_value,
            "ddm_value":        ddm_value,
            "pe_series":        pe_series,
            "pb_series":        pb_series,
        }

        # --- [BƯỚC 7]: Volume ---
        if 'volume' not in df_price.columns:
            df_price['volume'] = 0
        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()
        latest_volume   = float(df_price['volume'].iloc[-1])
        avg_volume_20d  = float(df_price['volume_ma20'].iloc[-1]) if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        volume_vs_avg_pct = ((latest_volume / avg_volume_20d - 1) * 100) if avg_volume_20d > 0 else 0.0
        df_price['MA20'] = df_price['close_vnd'].rolling(window=20).mean()

        oil_corr_score = 0.74 if ticker in ['BSR', 'OIL', 'PLX', 'PVD', 'PVS', 'GAS'] else 0.0

        technical_summary = {
            "latest_volume":    latest_volume,
            "avg_volume_20d":   avg_volume_20d,
            "volume_vs_avg_pct": volume_vs_avg_pct,
            "ma20":             df_price['MA20'].iloc[-1],
            "oil_correlation":  oil_corr_score,
            "trend_signal": "KHẢ QUAN (Uptrend)" if current_price > df_price['MA20'].iloc[-1] else "RỦI RO (Downtrend)",
        }

        # --- [BƯỚC 8]: Tin tức ---
        df_news_raw = _safe_call(lambda: c_engine.news(), 'news', source_used)
        news_list = []
        if df_news_raw is not None and not df_news_raw.empty:
            for _, row in df_news_raw.head(4).iterrows():
                news_list.append({
                    "title":  row.get('news_title',  'Cập nhật biến động thị trường'),
                    "source": row.get('news_source', 'HOSE Disclosure'),
                })
        else:
            news_list.append({"title": "Không có sự kiện bất thường trong 30 ngày.", "source": "Hệ thống tự động"})

        return (
            df_price, df_5y_table, df_balance, clean_metrics, technical_summary,
            news_list, fundamentals_summary, df_dupont, valuation_package,
        )

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        return None
