"""Background coordinator (one daemon thread): REST polling + websocket subscriptions + signal engine + paper tracker."""
import logging
import threading
import time
from collections import defaultdict

from django.conf import settings

from upstox.instruments import UNDERLYINGS, VIX_KEY
from .chain import compute_metrics, parse_chain
from .engine import SignalEngine
from .state import STATE, RealClock, VirtualClock
from .tracker import Tracker

log = logging.getLogger(__name__)


class Orchestrator(threading.Thread):
    def __init__(self):
        super().__init__(name="optionlab-orchestrator", daemon=True)
        self.state = STATE
        self.watch = [s for s in settings.WATCHLIST if s in UNDERLYINGS]
        self.last = defaultdict(float)
        self._subs = None
        self._persist_min = {}
        self.ready = threading.Event()

    # ------------------------------------------------------------------ setup
    def setup(self):
        st = self.state
        st.mode = settings.OPTIONLAB_MODE
        if st.mode == "live":
            from upstox.client import UpstoxClient
            from upstox.feed import LiveFeed
            from upstox.sources import LiveSource
            client = UpstoxClient(settings.UPSTOX_TOKEN)
            self.client, self.source, self.feed = client, LiveSource(client), LiveFeed(client, st)
            st.clock = RealClock()
        else:
            from upstox.demo import DemoFeed, DemoSource, DemoWorld
            st.clock = VirtualClock(settings.DEMO_SPEED)
            self.client, world = None, DemoWorld(self.watch, st.clock)
            self.source, self.feed = DemoSource(world), DemoFeed(world, st)
        self.tracker = Tracker(st)
        self.tracker.load()
        self.engine = SignalEngine(st, self.tracker)
        self.feed.start()
        self.ready.set()
        log.info("OptionLab engine started in %s mode, watching %s", st.mode, self.watch)

    # ------------------------------------------------------------------ steps
    def _load_meta(self, sym):
        meta = self.source.contracts(sym)
        st = self.state
        st.expiries[sym], st.lot[sym] = meta["expiries"], meta["lot"]
        if st.selected.get(sym) not in meta["expiries"] and meta["expiries"]:
            st.selected[sym] = meta["expiries"][0]

    def _poll_chain(self, sym):
        st, expiry = self.state, self.state.selected.get(sym)
        if not expiry:
            return
        rows, spot = parse_chain(self.source.chain(sym, expiry))
        live = st.tick(UNDERLYINGS[sym]["key"])
        if live and live.get("ltp") and st.fresh(UNDERLYINGS[sym]["key"], 30):
            spot = live["ltp"]
        m = compute_metrics(rows, spot, st.lot.get(sym, 1), UNDERLYINGS[sym]["step"])
        if not m:
            return
        st.set_chain(sym, expiry, rows, spot, m)
        now = st.clock.now()
        st.add_metric(sym, {"ts": now.timestamp(), "spot": spot, "atm_iv": m.get("atm_iv"), "pcr": m.get("pcr_oi"), "max_pain": m.get("max_pain"), "straddle": m.get("straddle")})
        minute = now.strftime("%H%M")
        if self._persist_min.get(sym) != minute:
            self._persist_min[sym] = minute
            from .models import ChainMetric
            ChainMetric.objects.create(mode=st.mode, underlying=sym, expiry=expiry, ts=now, spot=spot, atm_iv=m.get("atm_iv"), pcr=m.get("pcr_oi"),
                                       max_pain=m.get("max_pain"), straddle=m.get("straddle"))

    def _poll_candles(self, sym):
        c = self.source.candles(sym)
        if c:
            self.state.candles[sym] = c

    def _refresh_spot(self):
        st = self.state
        for sym in self.watch:
            key = UNDERLYINGS[sym]["key"]
            if st.fresh(key, 30):
                st.spot[sym] = st.ticks[key]["ltp"]
        v = st.ticks.get(VIX_KEY)
        if v:
            st.vix = v.get("ltp")

    def _sync_subscriptions(self):
        st, keys = self.state, {VIX_KEY}
        for sym in self.watch:
            keys.add(UNDERLYINGS[sym]["key"])
            ch = st.chains.get(sym)
            if not ch:
                continue
            n, step = settings.ATM_WINDOW_STRIKES, ch["metrics"]["step"]
            atm = ch["metrics"]["atm"]
            spot = st.spot.get(sym)
            if spot:
                atm = min((r["strike"] for r in ch["rows"]), key=lambda k: abs(k - spot))
            wk = set()
            for r in ch["rows"]:
                if abs(r["strike"] - atm) <= n * step:
                    wk |= {s["key"] for s in (r["ce"], r["pe"]) if s}
            st.watch_keys[sym] = wk
            keys |= wk
        for s in self.tracker.active:
            keys.add(s.instrument_key)
        if keys != self._subs:
            self._subs = keys
            self.feed.set_subscriptions(keys)

    def select(self, sym, expiry):
        if sym in self.watch and expiry in self.state.expiries.get(sym, []):
            self.state.selected[sym] = expiry
            self.last[("chain", sym)] = 0

    # ------------------------------------------------------------------ main loop
    def _guard(self, where, fn, *a):
        try:
            fn(*a)
        except Exception as e:
            self.state.error(where, e)
            log.warning("%s: %s", where, e)

    def run(self):
        try:
            self.setup()
        except Exception as e:
            self.state.error("setup", e)
            log.exception("engine setup failed")
            return
        st, poll = self.state, settings.CHAIN_POLL_SECONDS
        while True:
            t = time.monotonic()
            slow = 1 if (st.market_open_now() or st.mode == "demo") else 6
            if not self.tracker.loaded:
                self.tracker.load()
            for sym in self.watch:
                if t - self.last[("meta", sym)] > 1800 or sym not in st.expiries:
                    self.last[("meta", sym)] = t
                    self._guard(f"contracts:{sym}", self._load_meta, sym)
                if t - self.last[("chain", sym)] >= poll * slow:
                    self.last[("chain", sym)] = t
                    self._guard(f"chain:{sym}", self._poll_chain, sym)
                if t - self.last[("candles", sym)] >= settings.CANDLE_POLL_SECONDS * slow:
                    self.last[("candles", sym)] = t
                    self._guard(f"candles:{sym}", self._poll_candles, sym)
            self._guard("spot", self._refresh_spot)
            self._guard("subscribe", self._sync_subscriptions)
            if t - self.last["engine"] >= 2:
                self.last["engine"] = t
                for sym in self.watch:
                    self._guard(f"engine:{sym}", self.engine.run, sym)
                self._guard("tracker", self.tracker.update)
                st.last_engine_run = st.clock.now().isoformat()
            time.sleep(0.5)


_orch, _lock = None, threading.Lock()


def ensure_started():
    global _orch
    with _lock:
        if _orch is None or not _orch.is_alive():
            _orch = Orchestrator()
            _orch.start()
        return _orch


def get_orchestrator():
    return _orch
