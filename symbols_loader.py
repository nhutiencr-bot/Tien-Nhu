"""
symbols_loader.py
-----------------
Load danh sách mã cổ phiếu — dùng vnstock v3.1.0 (explorer) thay vnstock.api
"""

import pandas as pd


def load_all_symbols() -> pd.DataFrame:
    """Trả về DataFrame(symbol, organ_name, exchange)."""
    try:
        from vnstock.explorer.vci.listing import Listing
        lst = Listing()
        df = lst.all_symbols()
        if df is not None and not df.empty:
            return df
    except Exception:
        pass

    try:
        from vnstock.explorer.tcbs.listing import Listing
        lst = Listing()
        df = lst.all_symbols()
        if df is not None and not df.empty:
            return df
    except Exception:
        pass

    # Fallback: danh sách tĩnh các mã phổ biến
    data = [
        ("ACB","Ngân hàng TMCP Á Châu","HOSE"),
        ("VCB","Ngân hàng TMCP Ngoại thương Việt Nam","HOSE"),
        ("BID","Ngân hàng TMCP Đầu tư và Phát triển VN","HOSE"),
        ("CTG","Ngân hàng TMCP Công thương Việt Nam","HOSE"),
        ("TCB","Ngân hàng TMCP Kỹ thương Việt Nam","HOSE"),
        ("MBB","Ngân hàng TMCP Quân đội","HOSE"),
        ("VPB","Ngân hàng TMCP Việt Nam Thịnh Vượng","HOSE"),
        ("STB","Ngân hàng TMCP Sài Gòn Thương Tín","HOSE"),
        ("HDB","Ngân hàng TMCP Phát triển TP.HCM","HOSE"),
        ("TPB","Ngân hàng TMCP Tiên Phong","HOSE"),
        ("HPG","Tập đoàn Hòa Phát","HOSE"),
        ("VNM","Công ty CP Sữa Việt Nam","HOSE"),
        ("FPT","Công ty CP FPT","HOSE"),
        ("MSN","Tập đoàn Masan","HOSE"),
        ("VIC","Tập đoàn Vingroup","HOSE"),
        ("VHM","Công ty CP Vinhomes","HOSE"),
        ("VRE","Công ty CP Vincom Retail","HOSE"),
        ("GVR","Tập đoàn Công nghiệp Cao su VN","HOSE"),
        ("PLX","Tập đoàn Xăng dầu Việt Nam","HOSE"),
        ("GAS","Tổng Công ty Khí Việt Nam","HOSE"),
        ("POW","Tổng Công ty Điện lực Dầu khí VN","HOSE"),
        ("MWG","Công ty CP Đầu tư Thế Giới Di Động","HOSE"),
        ("PNJ","Công ty CP Vàng bạc Đá quý Phú Nhuận","HOSE"),
        ("DGC","Công ty CP Tập đoàn Hóa chất Đức Giang","HOSE"),
        ("SSI","Công ty CP Chứng khoán SSI","HOSE"),
        ("VND","Công ty CP Chứng khoán VNDirect","HOSE"),
        ("HCM","Công ty CP Chứng khoán TP.HCM","HOSE"),
        ("NLG","Công ty CP Đầu tư Nam Long","HOSE"),
        ("KDH","Công ty CP Đầu tư và Kinh doanh Nhà Khang Điền","HOSE"),
        ("DXG","Công ty CP Tập đoàn Đất Xanh","HOSE"),
        ("BCM","Tổng Công ty Đầu tư và Phát triển Công nghiệp","HOSE"),
        ("BSR","Công ty CP Lọc hóa dầu Bình Sơn","UPCOM"),
        ("OIL","Tổng Công ty Dầu Việt Nam","UPCOM"),
        ("PVD","Tổng Công ty CP Khoan và Dịch vụ Khoan Dầu khí","HOSE"),
        ("PVS","Tổng Công ty CP Dịch vụ Kỹ thuật Dầu khí VN","HNX"),
    ]
    return pd.DataFrame(data, columns=["symbol", "organ_name", "exchange"])


def build_display_options(df: pd.DataFrame):
    """
    Trả về (display_list, display_to_symbol).
    display_list: ["ACB — Ngân hàng TMCP Á Châu (HOSE)", ...]
    display_to_symbol: {"ACB — ...": "ACB"}
    """
    if df is None or df.empty:
        return [], {}

    display_list = []
    display_to_symbol = {}

    # Chuẩn hoá tên cột
    sym_col  = next((c for c in df.columns if c.lower() in ["symbol", "ticker"]), None)
    name_col = next((c for c in df.columns if "name" in c.lower() or "organ" in c.lower()), None)
    exch_col = next((c for c in df.columns if "exchange" in c.lower() or "comgroup" in c.lower()), None)

    if sym_col is None:
        return [], {}

    for _, row in df.iterrows():
        sym  = str(row[sym_col]).strip().upper()
        name = str(row[name_col]).strip() if name_col else ""
        exch = str(row[exch_col]).strip() if exch_col else ""

        if exch and exch.upper() not in ("NAN", "NONE", ""):
            label = f"{sym} — {name} ({exch})" if name else f"{sym} ({exch})"
        else:
            label = f"{sym} — {name}" if name else sym

        display_list.append(label)
        display_to_symbol[label] = sym

    return display_list, display_to_symbol
