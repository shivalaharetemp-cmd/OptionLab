"""Every GET endpoint the Upstox *analytics token* can use, as a data table.

Paths/params were extracted from the official upstox-python-sdk (v2.30) and the Upstox docs.
`static_ip=True` groups (account / orders / portfolio) need the calling server's IP whitelisted in the Upstox
developer console — they are hidden in the Explorer unless UPSTOX_STATIC_IP_WHITELISTED=1.
The analytics token is read-only, so POST/PUT/DELETE endpoints are intentionally absent.
"""

# (category, id, path, [(param, where, required, example)], static_ip)
def _e(cat, eid, path, params=(), static_ip=False, note=""):
    return {"category": cat, "id": eid, "path": path, "static_ip": static_ip, "note": note,
            "params": [{"name": n, "in": w, "required": r, "example": ex} for (n, w, r, ex) in params]}

Q, P = "query", "path"
IK = ("instrument_key", Q, True, "NSE_INDEX|Nifty 50")
EXP = ("expiry", Q, True, "current_week")
DATE = ("date", Q, True, "2026-10-28")

ENDPOINTS = [
    # --- Option chain
    _e("Option Chain", "option_contracts", "/v2/option/contract", [IK, ("expiry_date", Q, False, "current_week")]),
    _e("Option Chain", "option_chain", "/v2/option/chain", [IK, ("expiry_date", Q, True, "current_week")], note="Strike-wise CE/PE: LTP, OI, prev OI, bid/ask, IV, delta, gamma, theta, vega, POP, PCR"),
    # --- Market quote (V3)
    _e("Market Quote", "ltp_v3", "/v3/market-quote/ltp", [("instrument_key", Q, True, "NSE_INDEX|Nifty 50,NSE_INDEX|India VIX")]),
    _e("Market Quote", "ohlc_v3", "/v3/market-quote/ohlc", [("instrument_key", Q, True, "NSE_INDEX|Nifty 50"), ("interval", Q, True, "1d")]),
    _e("Market Quote", "full_quote_v3", "/v3/market-quote/quotes", [("instrument_key", Q, True, "NSE_INDEX|Nifty 50")]),
    _e("Market Quote", "option_greek_v3", "/v3/market-quote/option-greek", [("instrument_key", Q, True, "NSE_FO|43885")], note="max 50 keys per call"),
    # --- Historical
    _e("Historical Data", "candles_v3", "/v3/historical-candle/{instrumentKey}/{unit}/{interval}/{to_date}/{from_date}",
       [("instrumentKey", P, True, "NSE_INDEX|Nifty 50"), ("unit", P, True, "minutes"), ("interval", P, True, "5"), ("to_date", P, True, "2026-10-28"), ("from_date", P, False, "2026-10-20")]),
    _e("Historical Data", "intraday_candles_v3", "/v3/historical-candle/intraday/{instrumentKey}/{unit}/{interval}",
       [("instrumentKey", P, True, "NSE_INDEX|Nifty 50"), ("unit", P, True, "minutes"), ("interval", P, True, "1")]),
    # --- Market information (analytics-grade)
    _e("Market Information", "oi", "/v2/market/oi", [IK, EXP, DATE]),
    _e("Market Information", "change_oi", "/v2/market/change-oi", [IK, EXP, DATE, ("interval", Q, True, "60")]),
    _e("Market Information", "max_pain", "/v2/market/max-pain", [IK, EXP, DATE, ("bucket_interval", Q, True, "60")]),
    _e("Market Information", "pcr", "/v2/market/pcr", [IK, EXP, DATE, ("bucket_interval", Q, True, "60")]),
    _e("Market Information", "fii", "/v2/market/fii", [("data_type", Q, True, ""), ("interval", Q, True, ""), ("from", Q, False, "")], note="see Upstox docs for data_type/interval enums"),
    _e("Market Information", "dii", "/v2/market/dii", [("data_type", Q, True, ""), ("interval", Q, True, ""), ("from", Q, False, "")]),
    _e("Market Information", "smartlist_options", "/v2/market/smartlist/options", [("asset_type", Q, True, "INDEX"), ("category", Q, True, ""), ("page_number", Q, False, "1"), ("page_size", Q, False, "20")]),
    _e("Market Information", "smartlist_futures", "/v2/market/smartlist/futures", [("asset_type", Q, True, "INDEX"), ("category", Q, True, ""), ("page_number", Q, False, "1"), ("page_size", Q, False, "20")]),
    _e("Market Information", "smartlist_mtf", "/v2/market/smartlist/mtf", [("page_number", Q, False, "1"), ("page_size", Q, False, "20")]),
    _e("Market Information", "holidays", "/v2/market/holidays"),
    _e("Market Information", "holiday_on", "/v2/market/holidays/{date}", [("date", P, True, "2026-11-10")]),
    _e("Market Information", "timings", "/v2/market/timings/{date}", [("date", P, True, "2026-10-29")]),
    _e("Market Information", "exchange_status", "/v2/market/status/{exchange}", [("exchange", P, True, "NSE")]),
    # --- Instruments / expired (backtesting data)
    _e("Instruments", "instrument_search", "/v2/instruments/search", [("query", Q, True, "NIFTY"), ("exchanges", Q, False, "NSE"), ("segments", Q, False, "FO"), ("instrument_types", Q, False, "CE"), ("expiry", Q, False, ""), ("atm_offset", Q, False, ""), ("page_number", Q, False, "1"), ("records", Q, False, "10")]),
    _e("Expired Instruments", "expired_expiries", "/v2/expired-instruments/expiries", [IK]),
    _e("Expired Instruments", "expired_option_contracts", "/v2/expired-instruments/option/contract", [IK, ("expiry_date", Q, True, "2026-10-20")]),
    _e("Expired Instruments", "expired_future_contracts", "/v2/expired-instruments/future/contract", [IK, ("expiry_date", Q, True, "2026-10-27")]),
    _e("Expired Instruments", "expired_candles", "/v2/expired-instruments/historical-candle/{expired_instrument_key}/{interval}/{to_date}/{from_date}",
       [("expired_instrument_key", P, True, ""), ("interval", P, True, "1minute"), ("to_date", P, True, "2026-10-20"), ("from_date", P, True, "2026-10-19")]),
    # --- Charges / news / fundamentals / IPO
    _e("Charges", "brokerage", "/v2/charges/brokerage", [("instrument_token", Q, True, "NSE_FO|43885"), ("quantity", Q, True, "75"), ("product", Q, True, "I"), ("transaction_type", Q, True, "BUY"), ("price", Q, True, "100")]),
    _e("News", "news", "/v2/news", [("category", Q, True, "position"), ("instrument_keys", Q, False, "NSE_INDEX|Nifty 50"), ("page_number", Q, False, "1"), ("page_size", Q, False, "10")]),
    _e("Fundamentals", "profile", "/v2/fundamentals/{isin}/profile", [("isin", P, True, "INE002A01018")]),
    _e("Fundamentals", "key_ratios", "/v2/fundamentals/{isin}/key-ratios", [("isin", P, True, "INE002A01018")]),
    _e("Fundamentals", "income_statement", "/v2/fundamentals/{isin}/income-statement", [("isin", P, True, "INE002A01018"), ("type", Q, True, "annual"), ("time_period", Q, False, ""), ("fs", Q, False, "consolidated")]),
    _e("Fundamentals", "balance_sheet", "/v2/fundamentals/{isin}/balance-sheet", [("isin", P, True, "INE002A01018"), ("type", Q, True, "annual"), ("fs", Q, False, "consolidated")]),
    _e("Fundamentals", "cash_flow", "/v2/fundamentals/{isin}/cash-flow", [("isin", P, True, "INE002A01018"), ("type", Q, True, "annual"), ("fs", Q, False, "consolidated")]),
    _e("Fundamentals", "corporate_actions", "/v2/fundamentals/{isin}/corporate-actions", [("isin", P, True, "INE002A01018")]),
    _e("Fundamentals", "share_holdings", "/v2/fundamentals/{isin}/share-holdings", [("isin", P, True, "INE002A01018")]),
    _e("Fundamentals", "competitors", "/v2/fundamentals/{instrument_key}/competitors", [("instrument_key", P, True, "NSE_EQ|INE002A01018")]),
    _e("IPO", "ipos", "/v2/ipos", [("status", Q, False, "open"), ("issue_type", Q, False, ""), ("page_number", Q, False, "1"), ("records", Q, False, "10")]),
    _e("IPO", "ipo_details", "/v2/ipos/{id}", [("id", P, True, "")]),
    # --- Websocket
    _e("Websocket", "feed_authorize_v3", "/v3/feed/market-data-feed/authorize", note="returns a single-use wss:// URL; the app does this automatically"),
    # --- STATIC IP REQUIRED (read-only GETs)
    _e("User", "profile_user", "/v2/user/profile", static_ip=True),
    _e("User", "funds_v3", "/v3/user/get-funds-and-margin", static_ip=True),
    _e("User", "kill_switch", "/v2/user/kill-switch", static_ip=True),
    _e("Portfolio", "positions", "/v2/portfolio/short-term-positions", static_ip=True),
    _e("Portfolio", "holdings", "/v2/portfolio/long-term-holdings", static_ip=True),
    _e("Portfolio", "mtf_positions", "/v3/portfolio/mtf-positions", static_ip=True),
    _e("Payments", "payins", "/v2/user/payments/payin", static_ip=True),
]

_BY_PATH = {e["path"]: e for e in ENDPOINTS}


def visible(static_ip_ok: bool):
    return [e for e in ENDPOINTS if static_ip_ok or not e["static_ip"]]


def find(path_template: str):
    return _BY_PATH.get(path_template)
