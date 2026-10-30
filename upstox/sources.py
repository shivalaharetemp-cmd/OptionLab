"""Data sources behind one small interface (contracts / chain / candles) so the engine doesn't care if it's live or demo."""
from datetime import datetime

from .instruments import UNDERLYINGS
from .client import IST


class LiveSource:
    name = "live"

    def __init__(self, client):
        self.client = client

    def contracts(self, sym):
        meta = UNDERLYINGS[sym]
        rows = self.client.option_contracts(meta["key"])
        today = datetime.now(IST).date().isoformat()
        expiries = sorted({r["expiry"] for r in rows if r.get("expiry") and r["expiry"] >= today})
        lot = next((int(r["lot_size"]) for r in rows if r.get("lot_size")), meta["lot"])
        return {"expiries": expiries, "lot": lot}

    def chain(self, sym, expiry):
        return self.client.option_chain(UNDERLYINGS[sym]["key"], expiry)

    def candles(self, sym):
        return self.client.intraday_candles(UNDERLYINGS[sym]["key"], "minutes", 1)
