"""Built-in market simulator ("demo mode"): lets you run, see and test the whole app with no token, at any hour.

A virtual clock replays a 09:15–15:30 session (DEMO_SPEED x real time). Spot follows regime-switching random walks
(trend / chop / squeeze→breakout); options are priced with Black-Scholes on a skewed smile; OI evolves with price.
It exercises the exact same engine / tracker / UI code paths as live data — but it is NOT market data.
"""
import math
import random
import threading
import time
from collections import deque
from datetime import datetime, timedelta, time as dtime

from analytics.greeks import bs_greeks, bs_price, years_to_expiry
from analytics.state import IST
from .instruments import UNDERLYINGS, VIX_KEY

TICK = 0.05
_rt = lambda x: max(TICK, round(round(x / TICK) * TICK, 2))


class _Sym:
    def __init__(self, sym, meta, rng):
        self.sym, self.meta = sym, meta
        self.prev_close = meta["spot0"]
        self.spot = self.prev_close * (1 + rng.gauss(0, 0.003))
        self.iv = self.iv0 = meta["iv"]
        self.candles, self.last_min = [], -1
        self.regime, self.until, self.burst = "chop", 0, False
        self.ret = deque(maxlen=5)
        self.oi, self.close = {}, {}


class DemoWorld:
    def __init__(self, symbols, clock, seed=None):
        self.clock, self.rng = clock, random.Random(seed)
        self.symbols = [s for s in symbols if s in UNDERLYINGS]
        self.lock = threading.RLock()
        self.keys = {}
        self._reset()

    def _reset(self):
        with self.lock:
            self._session = self.clock.session
            self.day_start = datetime.combine(self.clock.day, dtime(9, 15), IST)
            self.syms = {s: _Sym(s, UNDERLYINGS[s], self.rng) for s in self.symbols}
            d = self.clock.day
            self.expiries = {s: [(d + timedelta(days=n)).isoformat() for n in (2, 9, 16)] for s in self.symbols}

    # ------------------------------------------------------------------ price path
    def advance(self):
        with self.lock:
            if self.clock.session != self._session:
                self._reset()
            idx = int((self.clock.now() - self.day_start).total_seconds() // 60)
            for s in self.syms.values():
                while s.last_min < idx:
                    self._step(s, s.last_min + 1)

    def _step(self, s, m):
        r = self.rng
        if m >= s.until:
            prev = s.regime
            s.burst = prev == "squeeze"
            s.regime = r.choice(["up", "down"]) if s.burst else r.choice(["up", "down", "chop", "chop", "squeeze"])
            s.until = m + (r.randint(12, 25) if s.burst else r.randint(20, 55))
        sigma = 0.00031 * {"squeeze": 0.35, "chop": 0.8}.get(s.regime, 1.0) * (1.7 if s.burst else 1.0)
        drift = {"up": 0.00024, "down": -0.00024}.get(s.regime, 0.0) * (1.6 if s.burst else 1.0)
        o = x = s.spot
        hi = lo = o
        for _ in range(4):
            x *= 1 + drift / 4 + sigma / 2 * r.gauss(0, 1)
            hi, lo = max(hi, x), min(lo, x)
        s.spot, ret = x, x / o - 1
        s.ret.append(ret)
        s.candles.append({"t": self.day_start + timedelta(minutes=m), "o": o, "h": hi, "l": lo, "c": x, "v": 0.0, "oi": 0.0})
        s.last_min = m
        # IV: mean-reverting, falls in squeezes, spikes on bursts, leverage effect
        tgt = s.iv0 * {"squeeze": 0.8}.get(s.regime, 1.0) * (1.25 if s.burst else 1.0)
        s.iv = min(max(s.iv + 0.04 * (tgt - s.iv) - ret * 2.2 + r.gauss(0, 0.0004), 0.07), 0.6)
        # OI: puts build under rising price, calls build under falling price
        for (exp, K, side), e in s.oi.items():
            bias = ret * 3000 * (1 if side == "PE" else -1) * (1 if (K <= x) == (side == "PE") else 0.3)
            e[0] = max(1000.0, e[0] + e[3] * 0.0012 * (bias + r.gauss(0, 1.0)))
            e[2] += e[0] * 0.0007 * r.random()

    # ------------------------------------------------------------------ quotes
    def _oi_entry(self, s, exp, K, side):
        k = (exp, K, side)
        if k not in s.oi:
            step = s.meta["step"]
            dist = (K - s.prev_close) / step
            peak = 5 if side == "CE" else -5
            base = (6.0e5 * math.exp(-((dist - peak) / 6.0) ** 2) + 4e4 + self.rng.random() * 3e4) * (s.meta["spot0"] / 25000) ** 0.3
            s.oi[k] = [base, base, 0.0, base]        # oi, prev_oi, volume, base
        return s.oi[k]

    def quote(self, sym, expiry, K, side):
        s = self.syms[sym]
        now = self.clock.now()
        T, S = years_to_expiry(expiry, now), s.spot
        m = math.log(K / S)
        iv = min(max(s.iv * (1 - 2.5 * m + 12 * m * m), 0.07), 1.2)
        call = side == "CE"
        px = bs_price(S, K, T, iv, call)
        g = bs_greeks(S, K, T, iv, call)
        spr = max(0.1, px * (0.004 + 0.12 * abs(m)))
        bid = _rt(px - spr / 2)
        ask = max(_rt(bid + spr), bid + TICK)
        e = self._oi_entry(s, expiry, K, side)
        ck = (expiry, K, side)
        if ck not in s.close:
            s.close[ck] = _rt(bs_price(s.prev_close, K, T + 1 / 365, iv, call))
        key = f"NSE_FO|DEMO-{sym}-{expiry}-{int(K)}-{side}"
        self.keys[key] = (sym, expiry, K, side)
        return {"key": key, "ltp": _rt(px), "bid": bid, "ask": ask, "bidq": 600.0 + self.rng.random() * 900, "askq": 600.0 + self.rng.random() * 900,
                "close": s.close[ck], "oi": e[0], "prev_oi": e[1], "vol": e[2], "iv": iv * 100,
                "delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]}

    def chain_rows(self, sym, expiry, span=20):
        self.advance()
        s = self.syms[sym]
        step = s.meta["step"]
        atm = round(s.spot / step) * step
        rows = []
        for i in range(-span, span + 1):
            K = atm + i * step
            leg = {}
            for side in ("CE", "PE"):
                q = self.quote(sym, expiry, K, side)
                leg[side] = {"instrument_key": q["key"],
                             "market_data": {"ltp": q["ltp"], "volume": q["vol"], "oi": q["oi"], "close_price": q["close"], "bid_price": q["bid"],
                                             "bid_qty": q["bidq"], "ask_price": q["ask"], "ask_qty": q["askq"], "prev_oi": q["prev_oi"]},
                             "option_greeks": {"vega": q["vega"], "theta": q["theta"], "gamma": q["gamma"], "delta": q["delta"], "iv": q["iv"], "pop": abs(q["delta"]) * 100}}
            rows.append({"expiry": expiry, "strike_price": float(K), "underlying_key": s.meta["key"], "underlying_spot_price": s.spot,
                         "call_options": leg["CE"], "put_options": leg["PE"], "pcr": 0})
        return rows


class DemoSource:
    name = "demo"

    def __init__(self, world):
        self.w = world

    def contracts(self, sym):
        return {"expiries": list(self.w.expiries[sym]), "lot": UNDERLYINGS[sym]["lot"]}

    def chain(self, sym, expiry):
        return self.w.chain_rows(sym, expiry)

    def candles(self, sym):
        self.w.advance()
        return list(self.w.syms[sym].candles)


class DemoFeed(threading.Thread):
    """Plays the role of the Upstox websocket: pushes ticks for whatever the engine subscribes to."""

    def __init__(self, world, state):
        super().__init__(name="demo-feed", daemon=True)
        self.w, self.state, self.keys = world, state, set()
        self._idx = {m["key"]: s for s, m in UNDERLYINGS.items()}

    def set_subscriptions(self, keys):
        self.keys = set(keys)
        self.state.feed["subscribed"] = len(self.keys)

    def run(self):
        self.state.feed.update(connected=True, error=None)
        while True:
            try:
                self.w.advance()
                for key in list(self.keys):
                    sym = self._idx.get(key)
                    if sym:
                        s = self.w.syms[sym]
                        self.state.update_tick(key, {"ltp": s.spot, "cp": s.prev_close})
                    elif key == VIX_KEY:
                        v = next(iter(self.w.syms.values()))
                        self.state.update_tick(key, {"ltp": v.iv * 100 * 1.1, "cp": v.iv0 * 100 * 1.1})
                    elif key in self.w.keys:
                        sy, exp, K, side = self.w.keys[key]
                        q = self.w.quote(sy, exp, K, side)
                        self.state.update_tick(key, {k: q[k] for k in ("ltp", "bid", "ask", "bidq", "askq", "oi", "iv", "delta", "gamma", "theta", "vega")} | {"vtt": q["vol"], "cp": q["close"]})
                self.state.feed["last_msg"] = time.time()
                self.state.feed["msgs"] += 1
            except Exception as e:  # pragma: no cover
                self.state.error("demo-feed", e)
            time.sleep(0.5)
