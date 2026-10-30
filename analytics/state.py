"""In-memory market state shared by the REST poller, the websocket feed, the signal engine and the browser push.

Run ONE server process: this object lives in that process (see README for the Redis/Channels scaling path).
"""
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone, time as dtime

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_SECONDS = 375 * 60


class RealClock:
    virtual = False
    session = 0

    def now(self):
        return datetime.now(IST)


class VirtualClock:
    """Demo clock: replays a 09:15–15:30 session over and over, `speed`x faster than real time."""
    virtual = True

    def __init__(self, speed=30.0):
        self.speed, self.t0, self.day = float(speed), time.monotonic(), datetime.now(IST).date()

    @property
    def _elapsed(self):
        return (time.monotonic() - self.t0) * self.speed

    @property
    def session(self):
        return int(self._elapsed // SESSION_SECONDS)

    def now(self):
        within = self._elapsed % SESSION_SECONDS
        return datetime.combine(self.day, dtime(9, 15), IST) + timedelta(seconds=within)


# tick field -> chain-side field used when overlaying live ticks on the REST snapshot
_OVERLAY = {"ltp": "ltp", "bid": "bid", "ask": "ask", "bidq": "bid_qty", "askq": "ask_qty", "oi": "oi", "iv": "iv",
            "delta": "delta", "gamma": "gamma", "theta": "theta", "vega": "vega", "vtt": "vol"}
_PRICE_FIELDS = {"ltp", "bid", "ask"}


class MarketState:
    def __init__(self):
        self.lock = threading.RLock()
        self.clock = RealClock()
        self.mode = "live"
        self.ticks, self.hist = {}, {}
        self.chains, self.candles, self.metric_hist = {}, {}, {}
        self.spot, self.vix = {}, None
        self.expiries, self.lot, self.selected = {}, {}, {}
        self.segment_status = {}
        self.feed = {"connected": False, "last_msg": None, "msgs": 0, "error": None, "subscribed": 0}
        self.errors = deque(maxlen=30)
        self.watch_keys = {}
        self.version = 0
        self.last_engine_run = None
        self.engine_notes = deque(maxlen=40)   # why signals were skipped (useful while tuning)

    # ---------------------------------------------------------------- ticks
    def update_tick(self, key, tick):
        now = self.clock.now().timestamp()
        with self.lock:
            t = self.ticks.setdefault(key, {})
            t.update({k: v for k, v in tick.items() if v is not None})
            t["rx"] = time.monotonic()
            if t.get("ltp"):
                h = self.hist.setdefault(key, deque(maxlen=4000))
                if not h or now - h[-1][0] >= 1.0:
                    h.append((now, t["ltp"], t.get("oi", 0.0), t.get("vtt", 0.0)))

    def tick(self, key):
        with self.lock:
            t = self.ticks.get(key)
            return dict(t) if t else None

    def fresh(self, key, max_age):
        t = self.ticks.get(key)
        return bool(t) and (time.monotonic() - t["rx"]) <= max_age

    def series(self, key, since_ts):
        with self.lock:
            return [p for p in self.hist.get(key, ()) if p[0] >= since_ts]

    def overlay(self, side, max_age=60):
        """Chain side dict + any fresh websocket tick on top."""
        if not side:
            return side
        t = self.ticks.get(side["key"])
        if not t or (time.monotonic() - t["rx"]) > max_age:
            return side
        o = dict(side)
        for tk, sk in _OVERLAY.items():
            v = t.get(tk)
            if v is not None and (v > 0 or tk not in _PRICE_FIELDS):
                o[sk] = v
        return o

    # ---------------------------------------------------------------- chain / metrics
    def set_chain(self, sym, expiry, rows, spot, metrics):
        with self.lock:
            self.version += 1
            self.chains[sym] = {"expiry": expiry, "rows": rows, "spot": spot, "metrics": metrics, "version": self.version,
                                "ts": self.clock.now().isoformat()}
            if not self.spot.get(sym):
                self.spot[sym] = spot

    def live_rows(self, sym):
        with self.lock:
            c = self.chains.get(sym)
            if not c:
                return []
            return [{"strike": r["strike"], "ce": self.overlay(r["ce"]), "pe": self.overlay(r["pe"])} for r in c["rows"]]

    def add_metric(self, sym, m):
        with self.lock:
            self.metric_hist.setdefault(sym, deque(maxlen=2000)).append(m)

    def metric_series(self, sym, field, minutes):
        cut = self.clock.now().timestamp() - minutes * 60
        with self.lock:
            return [(m["ts"], m.get(field)) for m in self.metric_hist.get(sym, ()) if m["ts"] >= cut and m.get(field) is not None]

    # ---------------------------------------------------------------- misc
    def error(self, where, exc):
        self.errors.appendleft({"t": datetime.now(IST).strftime("%H:%M:%S"), "where": where, "msg": str(exc)[:300]})

    def note(self, sym, msg):
        self.engine_notes.appendleft({"t": self.clock.now().strftime("%H:%M:%S"), "sym": sym, "msg": msg})

    def market_open_now(self):
        now = self.clock.now()
        if self.clock.virtual:
            return True
        return now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 30)


STATE = MarketState()
