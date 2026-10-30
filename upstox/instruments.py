"""Static metadata for the index underlyings we scan.

`key` values for NIFTY and BANKNIFTY are taken from the Upstox docs. FINNIFTY / MIDCPNIFTY / SENSEX keys follow the
same naming pattern — confirm them against the Upstox instruments file if the chain call for one of them errors.
Lot size and expiries are always read from the live /option/contract API; `lot` here is only a demo-mode fallback.
"""
VIX_KEY = "NSE_INDEX|India VIX"

UNDERLYINGS = {
    "NIFTY":      {"key": "NSE_INDEX|Nifty 50",          "name": "Nifty 50",     "step": 50,  "lot": 75, "spot0": 24800.0, "iv": 0.135},
    "BANKNIFTY":  {"key": "NSE_INDEX|Nifty Bank",        "name": "Nifty Bank",   "step": 100, "lot": 30, "spot0": 55000.0, "iv": 0.155},
    "FINNIFTY":   {"key": "NSE_INDEX|Nifty Fin Service", "name": "Fin Nifty",    "step": 50,  "lot": 65, "spot0": 26000.0, "iv": 0.145},
    "MIDCPNIFTY": {"key": "NSE_INDEX|NIFTY MID SELECT",  "name": "Midcap Select", "step": 25,  "lot": 120, "spot0": 13000.0, "iv": 0.17},
    "SENSEX":     {"key": "BSE_INDEX|SENSEX",            "name": "Sensex",       "step": 100, "lot": 20, "spot0": 81500.0, "iv": 0.13},
}


def by_key(key: str):
    for sym, meta in UNDERLYINGS.items():
        if meta["key"] == key:
            return sym
    return None
