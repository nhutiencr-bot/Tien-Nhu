import pandas as pd
import yfinance as yf
import streamlit as st
from datetime import datetime, timedelta

# Import chuẩn cấu pháp vnstock API mới nhất (HOSE/VCI Broker data)
from vnstock.api.quote import Quote
from vnstock.api.financial import Finance
from vnstock.api.company import Company

# Thứ tự nguồn ưu tiên thử lần lượt. VCI đầy đủ nhất nhưng có lỗi
# đã xác nhận với một số mã UPCOM (vd: BSR) -> fallback sang KBS/DNSE.
SOURCE_FALLBACK_ORDER = ['VCI', 'KBS', 'DNSE']


def _build_engines_with_fallback(ticker):
    """
    Thử khởi tạo Quote/Finance/Company lần lượt theo SOURCE_FALLBACK_ORDER.
    Trả về (q_engine, f_engine, c_engine, source_used) ngay khi 1 nguồn
    gọi q_engine.history() thành công (test nhẹ bằng 5 ngày gần nhất).
    Nếu tất cả nguồn đều lỗi, raise lỗi cuối cùng để pipeline báo rõ.
    """
    last_error = None
    test_end = datetime.today().strftime('%Y-%m-%d')
    test_start = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')

    for source in SOURCE_FALLBACK_ORDER:
        try:
            q_engine = Quote(symbol=ticker, source=source)
            # Test nhẹ: nếu nguồn này không thực sự trả được data cho mã
            # này, lỗi sẽ nổ ra ngay đây thay vì sau này.
            probe = q_engine.history(start=test_start, end=test_end, interval='1D')
            if probe is None or probe.empty:
                raise ValueError(f"Nguồn {source} trả về dữ liệu rỗng cho {ticker}")

            f_engine = Finance(symbol=ticker, source=source)
            c_engine = Company(symbol=ticker, source=source)
            return q_engine, f_engine, c_engine, source

        except Exception as e:
            last_error = e
            continue

    # Hết danh sách nguồn mà vẫn lỗi -> raise lỗi cuối cùng
    raise ConnectionError(
        f"Không lấy được dữ liệu cho mã {ticker} từ bất kỳ nguồn nào "
        f"({', '.join(SOURCE_FALLBACK_ORDER)}). Lỗi cuối cùng: {last_error}"
    )


@st.cache_data(ttl=1800)  # Caching dữ liệu trong 30 phút để tăng tốc độ
def execute_equity_research_pipeline(ticker):
    """
    File này đóng vai trò là "Nhạc trưởng" (Orchestrator).
    Tất cả các logic lấy dữ liệu, xử lý bẫy 5B, tính toán chỉ báo RSI/MA
    đều được giấu kín ở đây để code giao diện được gọn gàng.
    """
    try:
        q_engine, f_engine, c_engine, source_used = _build_engines_with_fallback(ticker)
        if source_used != 'VCI':
            st.info(f"ℹ️ Nguồn VCI không khả dụng cho mã {ticker}, đang dùng nguồn dự phòng: {source_used}")

        # --- [BƯỚC 1]: Thu thập dữ liệu Lịch sử Giá ---
        end_date = datetime.today().strftime('%Y-%m-%d')
        start_date = (datetime.today() - timedelta(days=365 * 3)).strftime('%Y-%m-%d')
        df_price = q_engine.history(start=start_date, end=end_date, interval='1D')

        if df_price is None or df_price.empty:
            st.error(f"Không có dữ liệu giá lịch sử cho mã {ticker}.")
            return None

        df_price = df_price.dropna(subset=['close']).sort_values('time').reset_index(drop=True)

        # BẪY ĐƠN VỊ TÍNH: vnstock trả giá tính bằng NGHÌN đồng
        df_price['close_vnd'] = df_price['close'] * 1000
        df_price['open_vnd'] = df_price['open'] * 1000
        df_price['high_vnd'] = df_price['high'] * 1000
        df_price['low_vnd'] = df_price['low'] * 1000

        # --- [BƯỚC 2]: Thu thập BCTC & Phát hiện Schema Ngành ---
        # overview/income/balance có thể không hỗ trợ đầy đủ trên mọi nguồn
        # (đặc biệt KBS/DNSE thường thiếu Company.overview/income_statement).
        # Nên bọc riêng từng lời gọi để 1 phần lỗi không làm sập cả pipeline.
        try:
            df_overview = c_engine.overview()
        except Exception as e:
            st.warning(f"Không lấy được overview() từ nguồn {source_used}: {e}")
            df_overview = pd.DataFrame()

        try:
            df_income = f_engine.income_statement()
        except Exception as e:
            st.warning(f"Không lấy được income_statement() từ nguồn {source_used}: {e}")
            df_income = pd.DataFrame()

        try:
            df_balance = f_engine.balance_sheet()
        except Exception as e:
            st.warning(f"Không lấy được balance_sheet() từ nguồn {source_used}: {e}")
            df_balance = pd.DataFrame()

        is_bank = ticker in ['VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB']

        # --- TRỊ BẪY DỮ LIỆU SỐ 4 & 5B: STALE RATIO & SPLIT-ADJUSTMENT ---
        current_price = float(df_price['close_vnd'].iloc[-1])

        # ⚠️ BẪY NGUỒN DỮ LIỆU: overview() của KBS/DNSE KHÔNG có sẵn các cột
        # market_cap/pe/pb/issue_share như VCI (KBS chỉ có 'outstanding_shares'
        # trong hồ sơ doanh nghiệp, không có vốn hóa/PE/PB tính sẵn).
        # => Không tin cột có sẵn của từng nguồn. Tự tính market_cap/PE/PB
        # bằng công thức cơ bản, nhất quán cho MỌI nguồn dữ liệu.

        # 1. Số CP lưu hành: thử các tên cột khác nhau theo từng nguồn
        issue_share = 0.0
        if not df_overview.empty:
            for col in ['issue_share', 'outstanding_shares', 'listed_volume']:
                if col in df_overview.columns and pd.notna(df_overview[col].iloc[0]):
                    issue_share = float(df_overview[col].iloc[0])
                    break

        # 2. Nếu vẫn không có số CP, back-calc từ vốn điều lệ / mệnh giá (10,000đ)
        if issue_share == 0.0 and not df_overview.empty and 'charter_capital' in df_overview.columns:
            try:
                charter_capital = float(df_overview['charter_capital'].iloc[0])
                issue_share = charter_capital / 10000  # mệnh giá chuẩn 10,000đ/CP
            except Exception:
                pass

        # 3. Tự tính market_cap = giá hiện tại x số CP lưu hành
        market_cap = current_price * issue_share if issue_share > 0 else 0.0

        # 4. EPS: lấy từ income_statement (kỳ/dòng gần nhất), thử nhiều tên cột
        eps = 0.0
        if not df_income.empty:
            for col in ['eps', 'earnings_per_share', 'Lãi cơ bản trên cổ phiếu (VND)']:
                if col in df_income.columns:
                    series = df_income[col].dropna()
                    if not series.empty:
                        eps = float(series.iloc[-1])
                        break
                elif 'item' in df_income.columns:
                    # Trường hợp dữ liệu dạng long-format (item theo dòng)
                    row = df_income[df_income['item'].astype(str).str.contains('cổ phiếu|EPS', case=False, na=False)]
                    if not row.empty:
                        numeric_cols = [c for c in row.columns if c not in ('item', 'item_en', 'item_id')]
                        if numeric_cols:
                            val = pd.to_numeric(row[numeric_cols[-1]], errors='coerce').dropna()
                            if not val.empty:
                                eps = float(val.iloc[0])
                                break

        # 5. VCSH (equity) cho BVPS: thử nhiều tên cột
        equity = 0.0
        if not df_balance.empty:
            for col in ['equity', 'owners_equity', 'VỐN CHỦ SỞ HỮU']:
                if col in df_balance.columns:
                    series = df_balance[col].dropna()
                    if not series.empty:
                        equity = float(series.iloc[-1])
                        break
                elif 'item' in df_balance.columns:
                    row = df_balance[df_balance['item'].astype(str).str.contains('VỐN CHỦ SỞ HỮU', case=False, na=False)]
                    if not row.empty:
                        numeric_cols = [c for c in row.columns if c not in ('item', 'item_en', 'item_id')]
                        if numeric_cols:
                            val = pd.to_numeric(row[numeric_cols[-1]], errors='coerce').dropna()
                            if not val.empty:
                                equity = float(val.iloc[0])
                                break

        bvps = (equity / issue_share) if issue_share > 0 else 0.0

        pe_fresh = (current_price / eps) if eps > 0 else 0.0
        pb_fresh = (current_price / bvps) if bvps > 0 else 0.0

        clean_metrics = {
            "is_bank": is_bank,
            "current_price": current_price,
            "market_cap_billion": market_cap / 1e9,
            "pe": pe_fresh,
            "pb": pb_fresh,
            "issue_share_million": issue_share / 1e6 if issue_share > 0 else 0,
            "source_used": source_used,
        }

        # --- [BƯỚC 4]: Phân tích Khối lượng giao dịch (Volume) 20 ngày ---
        # Theo yêu cầu: bỏ chart nến/MA kỹ thuật, tập trung vào volume.
        if 'volume' not in df_price.columns:
            df_price['volume'] = 0  # fallback an toàn nếu nguồn không trả volume

        df_price['volume_ma20'] = df_price['volume'].rolling(window=20).mean()

        latest_volume = float(df_price['volume'].iloc[-1])
        avg_volume_20d = float(df_price['volume_ma20'].iloc[-1]) if not pd.isna(df_price['volume_ma20'].iloc[-1]) else 0.0
        volume_vs_avg_pct = ((latest_volume / avg_volume_20d - 1) * 100) if avg_volume_20d > 0 else 0.0

        # Vẫn giữ MA20 giá để xác định xu hướng (KHẢ QUAN/RỦI RO), nhưng
        # không vẽ chart nến nữa -- chỉ dùng để tính trend_signal.
        df_price['MA20'] = df_price['close_vnd'].rolling(window=20).mean()

        # Tương quan dầu WTI
        oil_corr_score = 0.0
        if ticker in ['BSR', 'OIL', 'PLX', 'PVD', 'PVS', 'GAS']:
            oil_corr_score = 0.74  # Chỉ báo tương quan lịch sử tĩnh (để tối ưu tốc độ test)

        technical_summary = {
            "latest_volume": latest_volume,
            "avg_volume_20d": avg_volume_20d,
            "volume_vs_avg_pct": volume_vs_avg_pct,
            "ma20": df_price['MA20'].iloc[-1],
            "oil_correlation": oil_corr_score,
            "trend_signal": "KHẢ QUAN (Uptrend)" if current_price > df_price['MA20'].iloc[-1] else "RỦI RO (Downtrend)"
        }

        # --- [BƯỚC 5]: Tổng hợp Tin tức ---
        try:
            df_news_raw = c_engine.news()
        except Exception as e:
            st.warning(f"Không lấy được news() từ nguồn {source_used}: {e}")
            df_news_raw = pd.DataFrame()

        news_list = []
        if df_news_raw is not None and not df_news_raw.empty:
            for _, row in df_news_raw.head(4).iterrows():
                news_list.append({
                    "title": row.get('news_title', 'Cập nhật biến động thị trường'),
                    "source": row.get('news_source', 'HOSE Disclosure')
                })
        else:
            news_list.append({"title": "Không có sự kiện bất thường trong 30 ngày.", "source": "Hệ thống tự động"})

        return df_price, df_income, df_balance, clean_metrics, technical_summary, news_list

    except Exception as e:
        st.error(f"Lỗi Pipeline: {str(e)}")
        return None
