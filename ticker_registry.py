"""
ticker_registry.py
═══════════════════════════════════════════════════════════════════
Module duy nhất thay thế toàn bộ các dict BANK_TICKERS, FINANCIAL_TICKERS,
RETAIL_TICKERS, REAL_ESTATE_TICKERS cứng-code trong codebase cũ.

CÁCH HOẠT ĐỘNG:
  1. Gọi API vnstock (Listing + Screener) lấy ~1500+ mã từ HOSE/HNX/UPCOM
  2. TCBS API làm nguồn fallback với thông tin ngành ICB
  3. Phân loại tự động theo ICB industry code + tên công ty
  4. Cache kết quả 24h vào file JSON để tránh gọi lại liên tục
  5. Cung cấp hàm get_sector(ticker) → 'bank'|'insurance'|'securities'|
     'retail'|'realestate'|'industrial'|'general' cho financial_normalizer

TÍCH HỢP VÀO financial_normalizer.py:
  Thay toàn bộ các BANK_TICKERS = {...} bằng:
    from ticker_registry import get_sector, ALL_TICKERS, get_tickers_by_exchange

═══════════════════════════════════════════════════════════════════
"""

import json
import time
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

CACHE_FILE = Path(__file__).parent / ".ticker_registry_cache.json"
CACHE_TTL_HOURS = 24          # làm mới mỗi 24h
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 0.3           # giây giữa mỗi request để tránh bị block

# ─────────────────────────────────────────────────────────────
# ICB INDUSTRY CODE → SECTOR MAPPING
# (chuẩn HOSE/HNX theo ICB Level 3 / 4)
# ─────────────────────────────────────────────────────────────

ICB_SECTOR_MAP = {
    # ── Ngân hàng ─────────────────────────────────────────────
    '8350': 'bank',          # Banks
    '8355': 'bank',          # Banks (HOSE code)
    '8360': 'bank',          # Banks - Sub
    '8300': 'bank',          # Financial Services (catch-all for bank holding)

    # ── Bảo hiểm ──────────────────────────────────────────────
    '8530': 'insurance',     # Life Insurance
    '8570': 'insurance',     # Nonlife Insurance
    '8500': 'insurance',     # Insurance (generic)

    # ── Chứng khoán / Dịch vụ tài chính ──────────────────────
    '8770': 'securities',    # Investment Services
    '8775': 'securities',    # Specialty Finance
    '8700': 'securities',    # Financial Services
    '8730': 'securities',    # Investment Management

    # ── Bất động sản ──────────────────────────────────────────
    '8630': 'realestate',    # Real Estate
    '8633': 'realestate',    # Real Estate Holding & Development
    '8636': 'realestate',    # Real Estate Services

    # ── Bán lẻ ────────────────────────────────────────────────
    '5370': 'retail',        # Broadline Retailers
    '5371': 'retail',        # Specialty Retailers
    '5375': 'retail',        # Food & Drug Retailers
    '5300': 'retail',        # Retail (generic)
    '5330': 'retail',        # Food & Beverage Retail

    # ── Hàng tiêu dùng / phân phối ────────────────────────────
    '5750': 'consumer',      # Food Producers
    '5760': 'consumer',      # Beverages
    '5700': 'consumer',      # Consumer Goods
    '5720': 'consumer',      # Personal Goods
    '5255': 'consumer',      # Drug Retailers / Pharmacy

    # ── Công nghiệp ───────────────────────────────────────────
    '2700': 'industrial',
    '2710': 'industrial',    # Construction & Materials
    '2720': 'industrial',    # Industrial Goods
    '2750': 'industrial',    # Industrial Services
    '2770': 'industrial',    # Industrial Transportation
    '2350': 'industrial',    # Aerospace & Defense
}

# ─────────────────────────────────────────────────────────────
# KEYWORD-BASED SECTOR DETECTION (tên công ty / organName)
# dùng khi ICB code không có hoặc không khớp
# ─────────────────────────────────────────────────────────────

_SECTOR_KEYWORDS = {
    'bank': [
        'ngân hàng', 'bank', 'vietcombank', 'vietinbank', 'bidv',
        'agribank', 'techcombank', 'mbbank', 'acb', 'vpbank',
        'sacombank', 'hdbank', 'tpbank', 'msb', 'ocb', 'vib',
        'shb', 'eib', 'lpb', 'ssb', 'bvb', 'klb', 'pvcombank',
        'abbank', 'ncb', 'sgb', 'navibank', 'bacabank',
    ],
    'insurance': [
        'bảo hiểm', 'insurance', 'bao viet', 'bảo việt',
        'pvi', 'pti', 'mic', 'aaas', 'bic', 'pre', 'vbi',
    ],
    'securities': [
        'chứng khoán', 'securities', 'chung khoan',
        'ssi', 'vnd', 'vci', 'hcm', 'mbs', 'fts', 'agr',
        'sbs', 'bsi', 'ors', 'tvs', 'tvb', 'vds', 'bvs',
        'evs', 'hbs', 'ivs', 'psi', 'shs', 'vfs', 'vig',
        'aps', 'cts', 'apg',
    ],
    'realestate': [
        'bất động sản', 'real estate', 'land', 'đất', 'địa ốc',
        'vinhomes', 'novaland', 'khang điền', 'đất xanh',
        'nam long', 'kinh bắc', 'phát đạt', 'becamex',
        'cho thuê', 'khu công nghiệp', 'kcn', 'hạ tầng khu',
    ],
    'retail': [
        'bán lẻ', 'retail', 'siêu thị', 'thế giới di động',
        'fpt retail', 'pharmacity', 'guardian',
        'phân phối', 'distribution', 'thương mại', 'trading',
        'xuất nhập khẩu', 'import export', 'xăng dầu',
        'petrolimex', 'pv oil', 'comeco',
    ],
}

# ─────────────────────────────────────────────────────────────
# HARDCODED SEED (bảo đảm luôn có dù API fail)
# Cập nhật lần cuối: 2025-07
# ─────────────────────────────────────────────────────────────

_SEED_BANK = {
    'VCB', 'BID', 'CTG', 'TCB', 'MBB', 'ACB', 'STB', 'VPB', 'HDB', 'TPB',
    'MSB', 'OCB', 'VIB', 'SHB', 'EIB', 'LPB', 'SSB', 'NAB', 'ABB', 'BAB',
    'BVB', 'KLB', 'PGB', 'VAB', 'VBB', 'NVB', 'SGB', 'CBB', 'PVC', 'NCB',
    'ABB', 'BacABank'.upper(), 'MSB', 'SEAB', 'VietABank'.upper(),
    'PVcomBank'.upper(), 'BIDV'.upper(),
}

_SEED_INSURANCE = {
    'BVH', 'PVI', 'PTI', 'MIG', 'BMI', 'VNR', 'BIC', 'PRE', 'PGI',
    'MIC', 'ABI', 'VBI', 'PTI', 'BHN', 'GIC',
}

_SEED_SECURITIES = {
    'SSI', 'VND', 'HCM', 'MBS', 'VCI', 'FTS', 'AGR', 'SBS', 'BSI',
    'ORS', 'TVS', 'TVB', 'VDS', 'BVS', 'EVS', 'HBS', 'IVS', 'PSI',
    'SHS', 'VFS', 'VIG', 'APS', 'CTS', 'APG',
}

_SEED_RETAIL = {
    'MWG', 'FRT', 'DGW', 'PNJ', 'HAX', 'SVC', 'MCH', 'PET',
    'PSD', 'HHS', 'HUT', 'AST', 'PTC', 'DGW', 'WRP', 'GMD',
    'PGD', 'CNG', 'GAS', 'PLX', 'COM', 'MPC', 'TMT',
}

_SEED_REALESTATE = {
    'VRE', 'NLG', 'DXG', 'KDH', 'PDR', 'CEO', 'BCM', 'VHM', 'NVL',
    'HDG', 'ITA', 'LDG', 'D2D', 'NRC', 'TDH', 'SJS', 'VPI', 'DIG',
    'PTL', 'HQC', 'CII', 'LHG', 'SCR', 'TTC', 'IDJ', 'IDC', 'CIG',
    'VCR', 'NBB', 'DRH', 'TNI', 'HAR', 'BII', 'GLT', 'ROS', 'FDC',
}

# ─────────────────────────────────────────────────────────────
# INTERNAL CACHE
# ─────────────────────────────────────────────────────────────

_registry: dict = {}      # ticker → {sector, exchange, name, icb}
_lock = threading.Lock()
_initialized = False


# ─────────────────────────────────────────────────────────────
# STEP 1: Lấy danh sách mã từ vnstock Listing API (VCI source)
# ─────────────────────────────────────────────────────────────

def _fetch_listing_vci() -> list[dict]:
    """
    Gọi API listing của VCI (backend của vnstock).
    Trả về list dict: {ticker, exchange, organName, icbCode, ...}
    """
    url = "https://trading.vci.com.vn/api/stock/getListedStock"
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; research-bot/1.0)',
        'Accept': 'application/json',
        'Origin': 'https://trading.vci.com.vn',
        'Referer': 'https://trading.vci.com.vn/',
    }
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else data.get('data', [])
        result = []
        for row in items:
            ticker = (row.get('ticker') or row.get('symbol') or '').strip().upper()
            if not ticker or not re.match(r'^[A-Z]{2,5}$', ticker):
                continue
            exchange = (row.get('comGroupCode') or row.get('exchange') or '').upper()
            exchange = exchange.replace('UPCOMINDEX', 'UPCOM').replace('HNXINDEX', 'HNX')
            result.append({
                'ticker': ticker,
                'exchange': exchange,
                'name': row.get('organName') or row.get('companyName') or '',
                'icb': str(row.get('icbCode') or row.get('industryIDv2') or ''),
            })
        logger.info(f"[VCI listing] {len(result)} mã")
        return result
    except Exception as e:
        logger.warning(f"[VCI listing] lỗi: {e}")
        return []


def _fetch_listing_ssi() -> list[dict]:
    """
    Gọi API iboard.ssi.com.vn — nguồn thứ 2, phủ UPCOM tốt hơn VCI.
    """
    url = "https://iboard.ssi.com.vn/dchart/api/1.1/defaultAllStocks"
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json',
        'Referer': 'https://iboard.ssi.com.vn/',
    }
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else (
            data.get('data') or data.get('items') or []
        )
        result = []
        for row in items:
            ticker = (row.get('s') or row.get('ticker') or '').strip().upper()
            if not ticker or not re.match(r'^[A-Z]{2,5}$', ticker):
                continue
            exchange_raw = (row.get('ex') or row.get('exchange') or '').upper()
            exchange = 'HOSE' if exchange_raw in ('HOSE', 'HSX') else \
                       'HNX' if exchange_raw == 'HNX' else \
                       'UPCOM' if exchange_raw in ('UPCOM', 'UPC') else exchange_raw
            result.append({
                'ticker': ticker,
                'exchange': exchange,
                'name': row.get('fn') or row.get('name') or '',
                'icb': str(row.get('indu') or row.get('icbCode') or ''),
            })
        logger.info(f"[SSI listing] {len(result)} mã")
        return result
    except Exception as e:
        logger.warning(f"[SSI listing] lỗi: {e}")
        return []


def _fetch_listing_tcbs() -> list[dict]:
    """
    TCBS public API — đặc biệt tốt cho HNX/UPCOM, có icbCode chuẩn.
    """
    url = "https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/ticker-list"
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else (
            data.get('data') or data.get('tickers') or []
        )
        result = []
        for row in items:
            ticker = (row.get('ticker') or row.get('symbol') or '').strip().upper()
            if not ticker or not re.match(r'^[A-Z]{2,5}$', ticker):
                continue
            exchange_raw = (row.get('exchange') or '').upper()
            exchange = 'HOSE' if 'HOSE' in exchange_raw or 'HSX' in exchange_raw else \
                       'HNX' if 'HNX' in exchange_raw else \
                       'UPCOM' if 'UPCOM' in exchange_raw else 'UPCOM'
            result.append({
                'ticker': ticker,
                'exchange': exchange,
                'name': row.get('companyName') or row.get('shortName') or '',
                'icb': str(row.get('icbCode') or row.get('industryCode') or ''),
            })
        logger.info(f"[TCBS listing] {len(result)} mã")
        return result
    except Exception as e:
        logger.warning(f"[TCBS listing] lỗi: {e}")
        return []


def _fetch_listing_vnstock_screener() -> list[dict]:
    """
    Gọi VCI screener API — tương đương `Screener.stock(exchangeName='HOSE,HNX,UPCOM', limit=1700)`.
    Không cần import vnstock package.
    """
    url = "https://api.vietstock.vn/ta/stockscreenersearch"
    params = {
        "exchangeName": "HOSE,HNX,UPCOM",
        "orderBy": "TotalVol",
        "orderDir": "DESC",
        "page": 1,
        "pageSize": 2000,
    }
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json',
        'Referer': 'https://finance.vietstock.vn/',
        'Origin': 'https://finance.vietstock.vn',
    }
    result = []
    try:
        r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else (
            data.get('data') or data.get('items') or []
        )
        for row in items:
            ticker = (row.get('Code') or row.get('StockCode') or '').strip().upper()
            if not ticker or not re.match(r'^[A-Z]{2,5}$', ticker):
                continue
            exchange = (row.get('Exchange') or row.get('Floor') or '').upper()
            result.append({
                'ticker': ticker,
                'exchange': exchange,
                'name': row.get('CompanyName') or '',
                'icb': '',
            })
        logger.info(f"[Vietstock screener] {len(result)} mã")
    except Exception as e:
        logger.warning(f"[Vietstock screener] lỗi: {e}")
    return result


# ─────────────────────────────────────────────────────────────
# STEP 2: Phân loại ngành tự động
# ─────────────────────────────────────────────────────────────

def _detect_sector_by_seed(ticker: str) -> Optional[str]:
    t = ticker.upper()
    if t in _SEED_BANK:        return 'bank'
    if t in _SEED_INSURANCE:   return 'insurance'
    if t in _SEED_SECURITIES:  return 'securities'
    if t in _SEED_RETAIL:      return 'retail'
    if t in _SEED_REALESTATE:  return 'realestate'
    return None


def _detect_sector_by_icb(icb_code: str) -> Optional[str]:
    if not icb_code:
        return None
    # Khớp chính xác trước
    s = ICB_SECTOR_MAP.get(icb_code)
    if s:
        return s
    # Khớp prefix (lấy 4 ký tự đầu)
    prefix4 = icb_code[:4]
    s = ICB_SECTOR_MAP.get(prefix4)
    if s:
        return s
    # Khớp prefix 2 ký tự
    prefix2 = icb_code[:2]
    for k, v in ICB_SECTOR_MAP.items():
        if k.startswith(prefix2):
            return v
    return None


def _detect_sector_by_name(name: str) -> Optional[str]:
    if not name:
        return None
    name_lower = name.lower()
    for sector, kws in _SECTOR_KEYWORDS.items():
        for kw in kws:
            if kw in name_lower:
                return sector
    return None


def _classify(ticker: str, name: str, icb: str) -> str:
    """
    Thứ tự ưu tiên:
    1. Seed (hardcoded chắc chắn)
    2. ICB code
    3. Tên công ty (keyword)
    4. Fallback 'general'
    """
    s = _detect_sector_by_seed(ticker)
    if s: return s
    s = _detect_sector_by_icb(icb)
    if s: return s
    s = _detect_sector_by_name(name)
    if s: return s
    return 'general'


# ─────────────────────────────────────────────────────────────
# STEP 3: Build & cache registry
# ─────────────────────────────────────────────────────────────

def _build_registry() -> dict:
    """
    Gọi nhiều nguồn, merge, phân loại. Trả về dict:
    { 'MWG': {'sector': 'retail', 'exchange': 'HOSE', 'name': '...', 'icb': '5370'}, ... }
    """
    raw: dict[str, dict] = {}   # ticker → best info

    sources = [
        _fetch_listing_vci,
        _fetch_listing_ssi,
        _fetch_listing_tcbs,
        _fetch_listing_vnstock_screener,
    ]

    for fetch_fn in sources:
        try:
            rows = fetch_fn()
            time.sleep(REQUEST_DELAY)
            for row in rows:
                t = row['ticker']
                if t not in raw:
                    raw[t] = row
                else:
                    # Enrich: ưu tiên giữ exchange và icb nếu chưa có
                    if not raw[t].get('exchange') and row.get('exchange'):
                        raw[t]['exchange'] = row['exchange']
                    if not raw[t].get('icb') and row.get('icb'):
                        raw[t]['icb'] = row['icb']
                    if not raw[t].get('name') and row.get('name'):
                        raw[t]['name'] = row['name']
        except Exception as e:
            logger.error(f"[build_registry] {fetch_fn.__name__} error: {e}")
            continue

    # Đảm bảo tất cả seed tickers đều có mặt dù API fail
    for t_set, exch, sector_name in [
        (_SEED_BANK, 'HOSE', 'bank'),
        (_SEED_INSURANCE, 'HOSE', 'insurance'),
        (_SEED_SECURITIES, 'HOSE', 'securities'),
        (_SEED_RETAIL, 'HOSE', 'retail'),
        (_SEED_REALESTATE, 'HOSE', 'realestate'),
    ]:
        for t in t_set:
            if t and re.match(r'^[A-Z]{2,5}$', t) and t not in raw:
                raw[t] = {'ticker': t, 'exchange': exch, 'name': '', 'icb': ''}

    # Phân loại
    registry = {}
    for ticker, info in raw.items():
        if not re.match(r'^[A-Z]{2,5}$', ticker):
            continue
        sector = _classify(ticker, info.get('name', ''), info.get('icb', ''))
        registry[ticker] = {
            'sector':   sector,
            'exchange': info.get('exchange', ''),
            'name':     info.get('name', ''),
            'icb':      info.get('icb', ''),
        }

    logger.info(f"[registry] built: {len(registry)} mã total")
    return registry


def _load_cache() -> Optional[dict]:
    try:
        if not CACHE_FILE.exists():
            return None
        data = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        ts = datetime.fromisoformat(data.get('timestamp', '2000-01-01'))
        if datetime.now() - ts > timedelta(hours=CACHE_TTL_HOURS):
            return None
        return data.get('registry')
    except Exception:
        return None


def _save_cache(registry: dict):
    try:
        payload = {
            'timestamp': datetime.now().isoformat(),
            'registry': registry,
        }
        CACHE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
    except Exception as e:
        logger.warning(f"[cache] save failed: {e}")


def _ensure_initialized(force_refresh=False):
    global _registry, _initialized
    with _lock:
        if _initialized and not force_refresh:
            return
        cached = None if force_refresh else _load_cache()
        if cached:
            _registry = cached
            logger.info(f"[registry] loaded from cache: {len(_registry)} mã")
        else:
            logger.info("[registry] building from APIs...")
            _registry = _build_registry()
            _save_cache(_registry)
        _initialized = True


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def get_sector(ticker: str) -> str:
    """
    Trả về sector của mã CP:
    'bank' | 'insurance' | 'securities' | 'retail' | 'realestate' |
    'consumer' | 'industrial' | 'general'

    Dùng thay thế toàn bộ các `ticker in BANK_TICKERS` kiểm tra trong
    financial_normalizer.py:

        # Cũ:
        is_bank = ticker in BANK_TICKERS
        # Mới:
        is_bank = get_sector(ticker) == 'bank'
    """
    _ensure_initialized()
    info = _registry.get(ticker.upper().strip())
    if info:
        return info['sector']
    # Fallback: thử phân loại ngay từ seed nếu không có trong registry
    s = _detect_sector_by_seed(ticker.upper())
    return s or 'general'


def get_info(ticker: str) -> dict:
    """Trả về toàn bộ metadata: {sector, exchange, name, icb}"""
    _ensure_initialized()
    return _registry.get(ticker.upper().strip(), {
        'sector': get_sector(ticker),
        'exchange': '',
        'name': '',
        'icb': '',
    })


def get_exchange(ticker: str) -> str:
    """Trả về sàn: 'HOSE' | 'HNX' | 'UPCOM' | ''"""
    return get_info(ticker).get('exchange', '')


def get_all_tickers(exchange: Optional[str] = None) -> list[str]:
    """
    Lấy toàn bộ mã CP. Nếu truyền exchange thì filter theo sàn.

    Dùng trong pipeline thay vì hardcode danh sách:
        all_tickers = get_all_tickers()                  # ~1500+ mã
        hose_tickers = get_all_tickers('HOSE')           # ~400 mã
        hnx_tickers  = get_all_tickers('HNX')            # ~300 mã
        upcom_tickers = get_all_tickers('UPCOM')          # ~800 mã
    """
    _ensure_initialized()
    if exchange is None:
        return sorted(_registry.keys())
    exch = exchange.upper()
    return sorted(t for t, info in _registry.items()
                  if info.get('exchange', '').upper() == exch)


def get_tickers_by_sector(sector: str) -> list[str]:
    """Lấy tất cả mã theo sector."""
    _ensure_initialized()
    return sorted(t for t, info in _registry.items()
                  if info.get('sector') == sector.lower())


def refresh_registry():
    """Force refresh toàn bộ registry từ API (bỏ qua cache)."""
    global _initialized
    _initialized = False
    _ensure_initialized(force_refresh=True)


def registry_stats() -> dict:
    """Trả về thống kê: số mã mỗi sàn, mỗi ngành."""
    _ensure_initialized()
    stats = {
        'total': len(_registry),
        'by_exchange': {},
        'by_sector': {},
    }
    for info in _registry.values():
        exch = info.get('exchange', 'UNKNOWN')
        stats['by_exchange'][exch] = stats['by_exchange'].get(exch, 0) + 1
        sector = info.get('sector', 'general')
        stats['by_sector'][sector] = stats['by_sector'].get(sector, 0) + 1
    return stats


# ─────────────────────────────────────────────────────────────
# BACKWARD-COMPAT: cung cấp set để code cũ vẫn chạy được
# `from ticker_registry import BANK_TICKERS` vẫn work
# ─────────────────────────────────────────────────────────────

class _LazySectorSet:
    """
    Set ảo — tra cứu từ registry thay vì hardcode.
    Tương thích `ticker in BANK_TICKERS`.
    """
    def __init__(self, sector: str):
        self._sector = sector

    def __contains__(self, ticker: str) -> bool:
        return get_sector(str(ticker).upper()) == self._sector

    def __iter__(self):
        return iter(get_tickers_by_sector(self._sector))

    def __len__(self):
        return len(get_tickers_by_sector(self._sector))

    def __repr__(self):
        return f"<LazySectorSet sector={self._sector!r} len={len(self)}>"


# Backward-compat exports (drop-in thay cho các dict cũ)
BANK_TICKERS        = _LazySectorSet('bank')
INSURANCE_TICKERS   = _LazySectorSet('insurance')
SECURITIES_TICKERS  = _LazySectorSet('securities')
FINANCIAL_TICKERS   = _LazySectorSet('securities')   # alias cũ
RETAIL_TICKERS      = _LazySectorSet('retail')
REAL_ESTATE_TICKERS = _LazySectorSet('realestate')

# Toàn bộ mã (lazy)
@property
def ALL_TICKERS():
    return get_all_tickers()


# ─────────────────────────────────────────────────────────────
# INTEGRATION PATCH: update financial_normalizer_fixed.py
# ─────────────────────────────────────────────────────────────

NORMALIZER_PATCH = '''
# ════════════════════════════════════════════════════════════════
# THAY THẾ CÁC DÒNG HARDCODE TRONG financial_normalizer.py
# Xóa toàn bộ các dict BANK_TICKERS, RETAIL_TICKERS, v.v.
# và thêm import này vào đầu file:
# ════════════════════════════════════════════════════════════════

from ticker_registry import get_sector, get_all_tickers, BANK_TICKERS, \\
    RETAIL_TICKERS, REAL_ESTATE_TICKERS, FINANCIAL_TICKERS

# Sau đó trong build_financial_table():
# Cũ:   is_bank = ticker in BANK_TICKERS  (hardcoded set)
# Mới:  is_bank = get_sector(ticker) == 'bank'    # tự động, 1500+ mã

# Thứ tự phân loại:
#   is_bank      = get_sector(ticker) == 'bank'
#   is_financial = get_sector(ticker) in ('insurance', 'securities')
#   is_retail    = get_sector(ticker) == 'retail'
#   is_realestate = get_sector(ticker) == 'realestate'
'''


# ─────────────────────────────────────────────────────────────
# CLI: python ticker_registry.py
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    print("🔄 Đang build registry từ API...")
    refresh_registry()

    stats = registry_stats()
    print(f"\n✅ Tổng mã: {stats['total']}")
    print("\n📊 Theo sàn:")
    for k, v in sorted(stats['by_exchange'].items()):
        print(f"   {k:8s}: {v:4d} mã")
    print("\n🏷  Theo ngành:")
    for k, v in sorted(stats['by_sector'].items(), key=lambda x: -x[1]):
        print(f"   {k:15s}: {v:4d} mã")

    if len(sys.argv) > 1:
        ticker = sys.argv[1].upper()
        info = get_info(ticker)
        print(f"\n🔍 {ticker}: {info}")

    # Export danh sách theo sàn ra file
    for exch in ['HOSE', 'HNX', 'UPCOM']:
        tickers = get_all_tickers(exch)
        fname = f"tickers_{exch.lower()}.txt"
        Path(fname).write_text('\n'.join(tickers), encoding='utf-8')
        print(f"📁 Đã ghi {len(tickers)} mã {exch} → {fname}")
