import pandas as pd
import numpy as np
import streamlit as st

from news_fetcher import fetch_news_google_rss, fetch_news_with_fallback
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
    dupont_decomposition, dcf_fcff_scenarios, reverse_dcf_implied_growth,
    graham_number, ddm_gordon, nine_methods_valuation, summarize_valuation,
)
from cafef_fallback import fetch_cafef_balance_sheet_5y, fetch_cafef_analysis_reports

SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']

# Timeout (giây) cho mỗi future trong thread pool — tránh 1 API bị treo
# kéo chậm toàn bộ pipeline. Nếu quá hạn, coi như fetch thất bại và dùng
# giá trị default (DataFrame rỗng).
TASK_TIMEOUT_SECONDS = 10


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


@st.cache_data(ttl=3600, show_spinner=False)
def _resolve_source(ticker):
    """
    Dò nguồn dữ liệu khả dụng cho ticker (VCI -> KBS -> DNSE) và CACHE kết quả
    trong 1 giờ. Lần fetch sau cho cùng ticker sẽ không phải probe lại từ đầu,
    giúp tiết kiệm vài giây mỗi lần load.
    """
    last_error = None
    test_end = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')

    for source in SOURCE_FALLBACK_ORDER:
        try:
            q_engine = Quote(symbol=ticker, source=source)
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError(f"Nguồn {source} trả về dữ liệu rỗng cho {ticker}")
            return source
        except Exception as e:
            last_error = e
            continue

    raise ConnectionError(
        f"Không lấy được dữ liệu cho mã {ticker} từ bất kỳ nguồn nào "
        f"({', '.join(SOURCE_FALLBACK_ORDER)}). Lỗi cuối cùng: {last_error}"
    )


def _build_engines_with_fallback(ticker):
    """Dùng nguồn đã được cache (hoặc dò mới nếu chưa có) để dựng engines."""
    source_used = _resolve_source(ticker)
    q_engine = Quote(symbol=ticker, source=source_used)
    f_engine = Finance(symbol=ticker, source=source_used, period='year')
    c_engine = Company(symbol=ticker, source=source_used)
    return q_engine, f_engine, c_engine, source_used


def _safe_fetch(fn, default=None):
    """Gọi 1 API call, trả về default nếu lỗi — dùng trong thread pool."""
    try:
        result = fn()
        return result if result is not None else (default if default is not None else pd.DataFrame())
    except Exception:
        return default if default is not None else pd.DataFrame()


def _fetch_balance_sheet(ticker, period='year'):
    """Thử lần lượt các nguồn để lấy balance sheet."""
    for bs_source in SOURCE_FALLBACK_ORDER:
        try:
            f_bs = Finance(symbol=ticker, source=bs_source, period=period)
            df = f_bs.balance_sheet(period=period)
            if df is not None and not df.empty:
                return df
        except Exception:
            continue
    return pd.DataFrame()


@st.cache_data(ttl=1800)
def execute_equity_research_pipeline(ticker):
    try:
        # ── 1. Chọn nguồn dữ liệu (đã cache theo ticker, xem _resolve_source) ──
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)

        end_date = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')

        # ── 2. Tất cả API calls chạy SONG SONG (concurrent) ───────────────
        # Bao gồm cả tin tức RSS (trước đây bị gọi tuần tự SAU thread pool,
        # cộng dồn thêm 5-15s do urlopen timeout 15s đồng bộ). Giờ chạy
        # chung 1 lượt với mọi API call khác.
        tasks = {
            "price": lambda: q_engine.history(start=start_date, end=end_date, interval='1D'),
            "overview": lambda: c_engine.overview(),
            "income_y": lambda: f_engine.income_statement(period='year'),
            "cashflow_y": lambda: f_engine.cash_flow(period='year'),
            "ratio_y": lambda: f_engine.ratio(period='year'),
            "income_q": lambda: f_engine.income_statement(period='quarter'),
            "ratio_q": lambda: f_engine.ratio(period='quarter'),
            "balance_y": lambda: _fetch_balance_sheet(ticker, period='year'),
            "balance_q": lambda: _fetch_balance_sheet(ticker, period='quarter'),
            "news_vnstock": lambda: c_engine.news(),
            "news_rss": lambda: fetch_news_google_rss(ticker),
            "reports": lambda: fetch_cafef_analysis_reports(ticker),
        }

        results = {}
        with ThreadPoolExecutor(max_workers=11) as executor:
            future_to_key = {
                executor.submit(_safe_fetch, fn): key
                for key, fn in tasks.items()
            }
            for future in as_completed(future_to_key, timeout=TASK_TIMEOUT_SECONDS * 2):
                key = future_to_key[future]
                try:
                    results[key] = future.result(timeout=TASK_TIMEOUT_SECONDS)
                except Exception:
                    if key == "news_rss":
                        results[key] = []
                    elif key == "reports":
                        results[key] = {"reports": [], "is_ticker_specific": False,
                                         "sources_used": ["CafeF"], "debug_log": ["Timeout/lỗi khi fetch."]}
                    else:
                        results[key] = pd.DataFrame()

        df_price = results.get("price", pd.DataFrame())
        df_overview = results.get("overview", pd.DataFrame())
        df_income = results.get("income_y", pd.DataFrame())
        df_cashflow = results.get("cashflow_y", pd.DataFrame())
        df_ratio = results.get("ratio_y", pd.DataFrame())
        df_income_q = results.get("income_q", pd.DataFrame())
        df_ratio_q = results.get("ratio_q", pd.DataFrame())
        df_balance = results.get("balance_y", pd.DataFrame())
        df_balance_q = results.get("balance_q", pd.DataFrame())
        df_news_raw = results.get("news_vnstock", pd.DataFrame())
        rss_news_raw = results.get("news_rss", [])
        reports_pkg = results.get("reports", {"reports": [], "is_ticker_specific": False,
                                               "sources_used": ["CafeF"], "debug_log": []})

        # ── 3. Xử lý giá ──────────────────────────────────────────────────
        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None

        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)
        for col in ['close', 'open', 'high', 'low']:
            df_price[f'{col}_vnd'] = df_price[col] * 1000

        is_bank = ticker in ['VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB']
        current_price = float(df_price['close_vnd'].iloc[-1])

        # ── 4. Chuẩn hoá BCTC ─────────────────────────────────────────────
        fin5 = build_5y_financial_table(df_income, df_balance, df_ratio, ticker=ticker)

        revenue_series = normalize_to_billion_vnd(fin5['revenue'])
        equity_series = normalize_to_billion_vnd(fin5['equity'])
        total_assets_series = normalize_to_billion_vnd(fin5['total_assets'])
        net_profit_series = normalize_net_profit_with_anchor(
            fin5['net_profit'], equity_series, fin5['roe'])

        eps_series = fin5['eps']
        bvps_series = fin5['bvps']
        roe_series = fin5['roe']
        roa_series = fin5['roa']
        pe_series = fin5['pe']
        pb_series = fin5['pb']
        outstanding_shares_series = fin5['outstanding_shares']
        net_margin_series = fin5['net_margin']
        asset_turnover_series = fin5['asset_turnover']

        # Fallback equity
        if equity_series.empty and not total_assets_series.empty:
            total_liab_series = normalize_to_billion_vnd(find_row_series(
                df_balance,
                ['tổng cộng nợ phải trả', 'tổng nợ phải trả', 'total liabilities'],
                exclude_keywords=['vốn chủ sở hữu']))
            if not total_liab_series.empty:
                common_years = total_assets_series.index.intersection(total_liab_series.index)
                if len(common_years) > 0:
                    equity_series = (total_assets_series.loc[common_years]
                                      - total_liab_series.loc[common_years])

        # Fallback CafeF
        if equity_series.empty or total_assets_series.empty:
            cafef_data = fetch_cafef_balance_sheet_5y(ticker, end_year=datetime.today().year)
            if equity_series.empty and not cafef_data['equity'].empty:
                equity_series = cafef_data['equity']
                st.info(f"ℹ️ Đã lấy 'Vốn chủ sở hữu' cho {ticker} từ CafeF.")
            if total_assets_series.empty and not cafef_data['total_assets'].empty:
                total_assets_series = cafef_data['total_assets']
                st.info(f"ℹ️ Đã lấy 'Tổng tài sản' cho {ticker} từ CafeF.")

        if equity_series.empty:
            st.warning(f"⚠️ Không dò được 'Vốn chủ sở hữu' cho {ticker}.")
        if total_assets_series.empty:
            st.warning(f"⚠️ Không dò được 'Tổng tài sản' cho {ticker}.")

        # ── 5. Số CP lưu hành ─────────────────────────────────────────────
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

        if market_cap_direct > 0 and current_price > 0:
            implied_from_cap = market_cap_direct / current_price
            if issue_share > 0:
                if abs(implied_from_cap - issue_share) / issue_share > 0.20:
                    issue_share = implied_from_cap
            else:
                issue_share = implied_from_cap

        eps_latest = get_latest(eps_series, default=0.0)
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

        clean_metrics = {
            "is_bank": is_bank,
            "current_price": current_price,
            "market_cap_billion": market_cap / 1e9,
            "pe": (current_price / eps_latest) if eps_latest > 0 else 0.0,
            "pb": (current_price / bvps_latest) if bvps_latest > 0 else 0.0,
            "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
            "source_used": source_used,
        }

        # ── 6. Bảng KQKD theo Năm ─────────────────────────────────────────
        years_available = sorted(
            set(revenue_series.index) | set(net_profit_series.index) |
            set(equity_series.index) | set(total_assets_series.index))

        df_5y_table = pd.DataFrame({'Năm': years_available})
        df_5y_table['Doanh thu thuần (tỷ)'] = df_5y_table['Năm'].map(revenue_series)
        df_5y_table['LNST (tỷ)'] = df_5y_table['Năm'].map(net_profit_series)
        df_5y_table['Vốn CSH (tỷ)'] = df_5y_table['Năm'].map(equity_series)
        df_5y_table['Tổng tài sản (tỷ)'] = df_5y_table['Năm'].map(total_assets_series)
        df_5y_table['EPS (đ)'] = df_5y_table['Năm'].map(eps_series)
        df_5y_table['BVPS (đ)'] = df_5y_table['Năm'].map(bvps_series)
        df_5y_table['ROE (%)'] = df_5y_table['Năm'].map(lambda y: roe_series.get(y, None))
        df_5y_table['ROA (%)'] = df_5y_table['Năm'].map(lambda y: roa_series.get(y, None))

        revenue_cagr = cagr(get_latest_n_years(revenue_series, 5))
        net_profit_cagr = cagr(get_latest_n_years(net_profit_series, 5))

        fundamentals_summary = {
            "revenue_cagr_pct": revenue_cagr * 100 if revenue_cagr is not None else None,
            "net_profit_cagr_pct": net_profit_cagr * 100 if net_profit_cagr is not None else None,
            "eps_latest": eps_latest,
            "bvps_latest": bvps_latest,
            "roe_latest": get_latest(roe_series, default=None),
            "roa_latest": get_latest(roa_series, default=None),
        }

        # ── 7. Bảng KQKD theo Quý ─────────────────────────────────────────
        df_quarter_table = pd.DataFrame()
        try:
            fin_q = build_financial_table(df_income_q, df_balance_q, df_ratio_q,
                                           ticker=ticker, period='quarter')

            rev_q = normalize_to_billion_vnd(fin_q['revenue'])
            eq_q = normalize_to_billion_vnd(fin_q['equity'])
            ta_q = normalize_to_billion_vnd(fin_q['total_assets'])
            np_q = normalize_net_profit_with_anchor(fin_q['net_profit'], eq_q, fin_q['roe'])
            eps_q = fin_q['eps']
            bvps_q = fin_q['bvps']
            roe_q = _normalize_pct(fin_q['roe'])
            roa_q = _normalize_pct(fin_q['roa'])

            quarters = sorted(
                set(rev_q.index) | set(np_q.index) | set(eq_q.index) | set(ta_q.index),
                key=lambda c: (int(str(c).split('-Q')[0]), int(str(c).split('-Q')[1])))

            df_quarter_table = pd.DataFrame({'_p': quarters})
            df_quarter_table['Quý'] = df_quarter_table['_p'].apply(
                lambda c: f"Q{str(c).split('-Q')[1]}/{str(c).split('-Q')[0]}")
            df_quarter_table['Doanh thu thuần (tỷ)'] = df_quarter_table['_p'].map(rev_q)
            df_quarter_table['LNST (tỷ)'] = df_quarter_table['_p'].map(np_q)
            df_quarter_table['Vốn CSH (tỷ)'] = df_quarter_table['_p'].map(eq_q)
            df_quarter_table['Tổng tài sản (tỷ)'] = df_quarter_table['_p'].map(ta_q)
            df_quarter_table['EPS (đ)'] = df_quarter_table['_p'].map(eps_q)
            df_quarter_table['BVPS (đ)'] = df_quarter_table['_p'].map(bvps_q)
            df_quarter_table['ROE (%)'] = df_quarter_table['_p'].map(lambda y: roe_q.get(y, None))
            df_quarter_table['ROA (%)'] = df_quarter_table['_p'].map(lambda y: roa_q.get(y, None))
            df_quarter_table = df_quarter_table.drop(columns=['_p'])
        except Exception as e:
            st.warning(f"Không dựng được bảng theo Quý: {e}")

        # ── 8. DuPont ──────────────────────────────────────────────────────
        df_dupont = dupont_decomposition(
            revenue_series, net_profit_series, total_assets_series, equity_series)

        # ── 9. DCF / Graham / DDM ──────────────────────────────────────────
        cfo_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['lưu chuyển tiền thuần từ hoạt động kinh doanh',
             'net cash flow from operating', 'cash flow from operating activities']))

        capex_series = normalize_to_billion_vnd(find_row_series(
            df_cashflow,
            ['tiền chi để mua sắm', 'purchase of fixed assets',
             'capital expenditure', 'mua sắm tài sản cố định']))

        latest_fcff = None
        if not cfo_series.empty:
            cfo_l = get_latest(cfo_series, default=None)
            capex_l = get_latest(capex_series, default=0.0) if not capex_series.empty else 0.0
            if cfo_l is not None:
                latest_fcff = (cfo_l - abs(capex_l)) * 1e9

        dcf_results = reverse_g = None
        if latest_fcff and latest_fcff > 0 and issue_share > 0:
            dcf_results = dcf_fcff_scenarios(
                latest_fcff=latest_fcff, shares_outstanding=issue_share, net_debt=0.0)
            reverse_g = reverse_dcf_implied_growth(
                current_price=current_price, shares_outstanding=issue_share,
                latest_fcff=latest_fcff, wacc=0.105, net_debt=0.0)

        graham_value = graham_number(eps_latest, bvps_latest) if eps_latest > 0 and bvps_latest > 0 else None
        ddm_value = None

        valuation_methods = nine_methods_valuation(
            eps_latest=eps_latest, bvps_latest=bvps_latest,
            pe_series=pe_series, pb_series=pb_series,
            current_price=current_price,
            dcf_results=dcf_results, graham_value=graham_value, ddm_value=ddm_value)

        valuation_summary = summarize_valuation(valuation_methods, current_price) if valuation_methods else None

        valuation_package = {
            "methods": valuation_methods,
            "summary": valuation_summary,
            "dcf_scenarios": dcf_results,
            "reverse_dcf_g_pct": reverse_g * 100 if reverse_g is not None else None,
            "graham_value": graham_value,
            "ddm_value": ddm_value,
            "pe_series": pe_series,
            "pb_series": pb_series,
        }

        # ── 10. Volume + Technical ─────────────────────────────────────────
        if 'volume' not in df_price.columns:
            df_price['volume'] = 0

        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()
        df_price['MA20'] = df_price['close_vnd'].rolling(window=20).mean()

        latest_vol = float(df_price['volume'].iloc[-1])
        avg_vol_20d = float(df_price['volume_ma20'].iloc[-1]) if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        vol_vs_avg_pct = ((latest_vol / avg_vol_20d - 1) * 100) if avg_vol_20d > 0 else 0.0

        technical_summary = {
            "latest_volume": latest_vol,
            "avg_volume_20d": avg_vol_20d,
            "volume_vs_avg_pct": vol_vs_avg_pct,
            "ma20": df_price['MA20'].iloc[-1],
            "oil_correlation": 0.74 if ticker in ['BSR', 'OIL', 'PLX', 'PVD', 'PVS', 'GAS'] else 0.0,
            "trend_signal": "KHẢ QUAN (Uptrend)" if current_price > df_price['MA20'].iloc[-1] else "RỦI RO (Downtrend)",
        }

        # ── 11. Tin tức ────────────────────────────────────────────────────
        # RSS và vnstock news đã được fetch SONG SONG trong thread pool ở
        # bước 2 (results["news_rss"] / results["news_vnstock"]) — không gọi
        # network lần nữa ở đây. (Trước đây có 1 lệnh gọi `_safe_call(...)`
        # bị lỗi NameError vì hàm đó không tồn tại trong file — đã xoá.)
        vnstock_news = []
        if df_news_raw is not None and not df_news_raw.empty:
            for _, row in df_news_raw.head(10).iterrows():
                vnstock_news.append({
                    "title": row.get('news_title', ''),
                    "source": row.get('news_source', 'vnstock'),
                    "url": row.get('news_url', '#'),
                    "pub_date": "—",
                })

        news_list = fetch_news_with_fallback(ticker, vnstock_news, rss_news=rss_news_raw)

        return (
            df_price, df_5y_table, df_quarter_table, df_balance,
            clean_metrics, technical_summary,
            news_list, fundamentals_summary, df_dupont, valuation_package,
            reports_pkg,
        )

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        return None
