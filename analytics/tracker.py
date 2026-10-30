"""Paper tracker: follows every emitted signal on LIVE option premiums and records the outcome.

This is how you find out whether a strategy actually has an edge *for you*: win rate, average R, expectancy,
profit factor and drawdown per strategy, measured on real ticks with spread + slippage — not on a backtest.
Exit rules (long options only): stop-loss, +T1 (assumed to book half and move the stop to cost, then trail),
+T2, time stop, and a forced exit at `force_exit_time`.
"""
import logging
import threading
from datetime import time as dtime

from django.conf import settings

from .models import Signal

log = logging.getLogger(__name__)


class Tracker:
    def __init__(self, state):
        self.state, self.lock = state, threading.RLock()
        self.active, self.last_emit, self.day = [], {}, {}
        self.loaded = False
        self.version = 0

    # ------------------------------------------------------------------ lifecycle
    def load(self):
        st = self.state
        try:
            if st.mode == "demo":     # a demo session can't survive a restart
                Signal.objects.filter(mode="demo", status__in=Signal.ACTIVE).update(status=Signal.EOD)
            with self.lock:
                self.active = list(Signal.objects.filter(mode=st.mode, status__in=Signal.ACTIVE))
                for s in self.active:
                    self.last_emit[(s.underlying, s.strategy, s.action[-2:])] = s.created_at
                if st.mode == "live":
                    for s in Signal.objects.filter(mode="live", created_at__date=st.clock.now().date()):
                        d = self.day.setdefault(self._dk(s.underlying), {"n": 0, "r": 0.0})
                        d["n"] += 1
                        d["r"] += s.realized_r or 0.0
            self.loaded = True
        except Exception as e:   # DB not migrated yet etc. — retried by the orchestrator
            st.error("tracker.load", e)

    def _dk(self, sym):
        c = self.state.clock
        return (sym, str(c.now().date()), c.session)

    # ------------------------------------------------------------------ queries used by the engine's gates
    def has_active(self, sym, strategy=None, direction=None, opposite=None):
        with self.lock:
            for s in self.active:
                if s.underlying != sym:
                    continue
                if strategy and s.strategy != strategy:
                    continue
                if direction and s.action[-2:] != direction:
                    continue
                if opposite and s.action[-2:] == opposite:
                    continue
                return True
        return False

    def open_count(self):
        return len(self.active)

    def count_today(self, sym):
        return self.day.get(self._dk(sym), {}).get("n", 0)

    def day_r(self, sym):
        return self.day.get(self._dk(sym), {}).get("r", 0.0)

    def on_cooldown(self, sym, strategy, direction, now, minutes):
        t = self.last_emit.get((sym, strategy, direction))
        return bool(t) and (now - t).total_seconds() < minutes * 60

    def register(self, sig):
        with self.lock:
            self.active.append(sig)
            self.last_emit[(sig.underlying, sig.strategy, sig.action[-2:])] = sig.created_at
            self.day.setdefault(self._dk(sig.underlying), {"n": 0, "r": 0.0})["n"] += 1
            self.version += 1

    # ------------------------------------------------------------------ the loop
    def update(self):
        cfg, now = settings.SIGNALS, self.state.clock.now()
        with self.lock:
            for sig in list(self.active):
                try:
                    self._step(sig, now, cfg)
                except Exception as e:
                    self.state.error("tracker", e)

    def mark(self, sig):
        """Exit price a long holder could actually get right now: best bid (fallback LTP)."""
        t = self.state.tick(sig.instrument_key)
        if not t:
            return None
        return t.get("bid") or t.get("ltp")

    def _step(self, sig, now, cfg):
        st = self.state
        sess = sig.context.get("session")
        px = self.mark(sig)
        if sess is not None and sess != st.clock.session:        # demo session rolled over
            return self._close(sig, px or sig.entry, Signal.EOD, now)
        if px is None:
            return
        sig.peak, sig.trough = max(sig.peak, px), min(sig.trough or px, px)
        r0 = sig.context.get("risk0") or (sig.entry - sig.sl)
        if not sig.t1_hit and px >= sig.t1:
            sig.t1_hit, sig.status = True, Signal.T1
            sig.sl = max(sig.sl, sig.entry)                      # stop to cost
            sig.save()
        if sig.t1_hit:
            sig.sl = max(sig.sl, sig.entry, sig.peak - 0.75 * r0)  # trail
        held = (now - sig.created_at).total_seconds() / 60
        if px >= sig.t2:
            return self._close(sig, px, Signal.T2, now)
        if px <= sig.sl:
            return self._close(sig, px, Signal.TRAIL if sig.t1_hit else Signal.SL, now)
        if now.time() >= dtime(*map(int, cfg["force_exit_time"].split(":"))):
            return self._close(sig, px, Signal.EOD, now)
        if held >= cfg["max_hold_minutes"] and not sig.t1_hit and px < sig.entry + 0.3 * r0:
            return self._close(sig, px, Signal.TIMEOUT, now)

    def _close(self, sig, px, status, now):
        cfg = settings.SIGNALS
        r0 = sig.context.get("risk0") or (sig.entry - sig.sl) or 1.0
        final_r = (px - sig.entry) / r0
        sig.realized_r = round(0.5 * cfg["t1_r"] + 0.5 * final_r if sig.t1_hit else final_r, 3)
        sig.exit_price, sig.exit_at, sig.status = round(px, 2), now, status
        sig.save()
        self.active = [s for s in self.active if s.pk != sig.pk]
        self.day.setdefault(self._dk(sig.underlying), {"n": 0, "r": 0.0})["r"] += sig.realized_r
        self.version += 1


def compute_stats(mode):
    rows = list(Signal.objects.filter(mode=mode, realized_r__isnull=False).order_by("exit_at").values_list("strategy", "realized_r", "underlying"))

    def agg(rs):
        n = len(rs)
        if not n:
            return {"n": 0}
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        cum, peak, dd = 0.0, 0.0, 0.0
        for r in rs:
            cum += r
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        return {"n": n, "win_rate": round(100 * len(wins) / n, 1), "avg_win_r": round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss_r": round(sum(losses) / len(losses), 2) if losses else 0, "expectancy_r": round(sum(rs) / n, 3),
                "total_r": round(sum(rs), 2), "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
                "max_drawdown_r": round(dd, 2)}

    allr = [r for _, r, _ in rows]
    curve, c = [], 0.0
    for r in allr:
        c += r
        curve.append(round(c, 2))
    return {"overall": agg(allr), "curve": curve,
            "by_strategy": {s: agg([r for st, r, _ in rows if st == s]) for s in sorted({st for st, _, _ in rows})},
            "open": Signal.objects.filter(mode=mode, status__in=Signal.ACTIVE).count()}
