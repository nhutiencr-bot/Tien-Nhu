"""
vn_data_client.py
-----------------
Thay thế Quote / Finance / Company không dùng vnstock.
Nguồn: DNSE → KBS → CafeF AJAX → Yahoo Finance
"""

import requests
import pandas as pd
import re
from datetime import datetime

_S = requests.Session()
_S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
})


def _get(url, params=None, timeout=12):
    try:
        r = _S.get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Quote
# ══════════════════════════════════════════════════════════════════════════════
class Quote:
    def __init__(self, symbol: str, source: str = "DNSE"):
        self.symbol = symbol.upper()

    def history(self, start: str, end: str, interval: str = "1D") -> pd.DataFrame:
        for fn in [self._dnse, self._yahoo]:
            try:
                df = fn(start, end)
                if df is not None and not df.empty and len(df) > 3:
                    return df
            except Exception:
                continue
        return pd.DataFrame()

    def _dnse(self, start, end):
        s = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
        e = int(datetime.strptime(end,   "%Y-%m-%d").timestamp())
        data = _get("https://services.entrade.com.vn/chart-api/v2/ohlcs/stock",
                    {"from": s, "to": e, "symbol": self.symbol, "resolution": "D"})
        if not isinstance(data, dict) or "c" not in data:
            return None
        times  = [datetime.fromtimestamp(t) for t in data.get("t", [])]
        closes = [float(x) for x in data["c"]]
        opens  = [float(x) for x in data.get("o", closes)]
        highs  = [float(x) for x in data.get("h", closes)]
        lows   = [float(x) for x in data.get("l", closes)]
        vols   = [float(x) for x in data.get("v", [0]*len(closes))]
        df = pd.DataFrame({"time": times, "open": opens, "high": highs,
                           "low": lows, "close": closes, "volume": vols})
        return df.dropna(subset=["close"])

    def _yahoo(self, start, end):
        import yfinance as yf
        for suffix in [".VN", ".HM"]:
            try:
                df = yf.Ticker(self.symbol + suffix).history(start=start, end=end)
                if df is not None and not df.empty:
                    df = df.reset_index().rename(columns={
                        "Date": "time", "Open": "open", "High": "high",
                        "Low": "low", "Close": "close", "Volume": "volume"})
                    df["time"] = pd.to_datetime(df["time"])
                    return df[["time","open","high","low","close","volume"]].dropna(subset=["close"])
            except Exception:
                continue
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Finance — CafeF AJAX (luôn hoạt động, không cần auth)
# ══════════════════════════════════════════════════════════════════════════════
class Finance:
    _RTYPE = {
        "income_statement": 1,
        "balance_sheet":    2,
        "cash_flow":        3,
        "ratio":            4,
    }

    def __init__(self, symbol: str, source: str = "DNSE", period: str = "year"):
        self.symbol = symbol.upper()
        self.period = period

    def _fetch_cafef(self, rname: str) -> pd.DataFrame:
        rtype = self._RTYPE.get(rname, 1)
        ptype = "Y" if self.period == "year" else "Q"
        rows  = []
        for page in [1, 2]:
            try:
                r = _S.get(
                    "https://s.cafef.vn/Handlers/AjaxFinancialData.ashx",
                    params={"symbol": self.symbol, "type": rtype,
                            "period": page, "periodType": ptype},
                    timeout=12)
                if r.status_code != 200:
                    continue
                items = r.json().get("Data", {}).get("ListFinancialData") or []
                for item in items:
                    name = str(item.get("Name", "")).strip()
                    unit = str(item.get("Unit", "")).lower()
                    div  = 1e3 if ("triệu" in unit or unit == "") else 1.0
                    row  = {"item": name}
                    for d in item.get("Data") or []:
                        period_str = str(d.get("Period", ""))
                        m = re.search(r'(20\d{2})', period_str)
                        if not m:
                            continue
                        yr  = m.group(1)
                        qm  = re.search(r'[Qq](\d)', period_str)
                        col = f"{yr}-Q{qm.group(1)}" if qm and self.period == "quarter" else yr
                        try:
                            val = float(d["Value"]) / div if d.get("Value") is not None else None
                        except Exception:
                            val = None
                        row[col] = val
                    rows.append(row)
            except Exception:
                continue
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def income_statement(self, period=None, limit=7) -> pd.DataFrame:
        return self._fetch_cafef("income_statement")

    def balance_sheet(self, period=None, limit=7) -> pd.DataFrame:
        return self._fetch_cafef("balance_sheet")

    def cash_flow(self, period=None, limit=7) -> pd.DataFrame:
        return self._fetch_cafef("cash_flow")

    def ratio(self, period=None, limit=7) -> pd.DataFrame:
        return self._fetch_cafef("ratio")


# ══════════════════════════════════════════════════════════════════════════════
# Company
# ══════════════════════════════════════════════════════════════════════════════
class Company:
    def __init__(self, symbol: str, source: str = "DNSE"):
        self.symbol = symbol.upper()

    def overview(self) -> pd.DataFrame:
        # CafeF scrape số CP lưu hành
        try:
            from bs4 import BeautifulSoup
            r = _S.get(f"https://cafef.vn/thi-truong-chung-khoan/{self.symbol.lower()}.chn",
                       timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            text = soup.get_text(" ", strip=True)
            m = re.search(r'[Ll]ưu hành[:\s]*([\d,\.]+)', text)
            shares = 0
            if m:
                try:
                    shares = float(m.group(1).replace(",", "").replace(".", ""))
                except Exception:
                    pass
            return pd.DataFrame([{
                "ticker": self.symbol,
                "issue_share": shares,
                "outstanding_shares": shares,
                "charter_capital": 0,
            }])
        except Exception:
            return pd.DataFrame()

    def news(self) -> pd.DataFrame:
        return pd.DataFrame()
