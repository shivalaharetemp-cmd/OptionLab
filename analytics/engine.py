"""Signal engine: context -> setups -> contract selection -> risk plan -> confidence -> gates -> (paper) signal.

BUYER ONLY. Every code path ends in `assert_buy_only`, and `Signal.save()` refuses anything but BUY_CE / BUY_PE.
"""
import logging
import time
from datetime import datetime, time as dtime, timedelta

from django.conf import settings

from . import indicators as ind
from .chain import atm_strike, spread_pct
from .greeks import years_to_expiry
from .models import BUY_ACTIONS, ChainMetric, Signal
from .strategies import STRATEGIES, Ctx, score_iv, score_oi, score_pcr, score_time, score_trend

log = logging.getLogger(__name__)
_rt = lambda x: max(0.05, round(round(x / 0.05) * 0.05, 2))
_hm = lambda s: dtime(*map(int, s.split(":")))


class SellingDisabled(Exception):
    pass


def assert_buy_only(action):
    """Selling is disabled (user is an option buyer). ALLOW_SELLING exists only as a visible safety switch: no sell logic exists."""
    if action not in BUY_ACTIONS:
        raise SellingDisabled(f"refusing non-buy action {action!r}")


class SignalEngine:
    def __init__(self, state, tracker):
        self.state, self.tracker, self.cfg = state, tracker, settings.SIGNALS
        self._ivc, self._noted = {}, {}

    # ------------------------------------------------------------------ helpers
    def _note(self, sym, msg):
        k, now = (sym, msg), time.monotonic()
        if now - self._noted.get(k, 0) > 90:
            self._noted[k] = now
            self.state.note(sym, msg)

    def _iv_percentile(self, sym, value):
        if value is None:
            return None
        st, now = self.state, time.monotonic()
        c = self._ivc.get(sym)
        if not c or now - c[0] > 300:
            try:
                cut = st.clock.now() - timedelta(days=20)
                vals = list(ChainMetric.objects.filter(mode=st.mode, underlying=sym, ts__gte=cut, atm_iv__isnull=False).values_list("atm_iv", flat=True)[:6000])
            except Exception:
                vals = []
            c = self._ivc[sym] = (now, vals)
        vals = c[1]
        if len(vals) < 150:                       # not enough history yet -> use today's session only
            vals = [v for _, v in st.metric_series(sym, "atm_iv", 400)]
            if len(vals) < 30:
                return None
        return ind.percentile_rank(vals, value)

    # ------------------------------------------------------------------ context
    def build_ctx(self, sym):
        st = self.state
        ch = st.chains.get(sym)
        if not ch:
            return None
        now = st.clock.now()
        rows = st.live_rows(sym)
        if not rows:
            return None
        spot = st.spot.get(sym) or ch["spot"]
        m = ch["metrics"]
        atm, step = atm_strike(rows, spot), m.get("step") or 50
        c1 = [c for c in (st.candles.get(sym) or []) if c["t"] <= now]
        b5, cur5 = ind.resample(c1, 5, now)
        closes = [b["c"] for b in b5]
        ivs, pcrs = st.metric_series(sym, "atm_iv", 15), st.metric_series(sym, "pcr", 15)
        near = [r for r in rows if abs(r["strike"] - atm) <= 3 * step]
        dte = (datetime.strptime(ch["expiry"], "%Y-%m-%d").date() - now.date()).days
        return Ctx(
            sym=sym, now=now, spot=spot, lot=st.lot.get(sym, 1), expiry=ch["expiry"], dte_days=dte, rows=rows, metrics=m, atm=atm, step=step,
            c1=c1, b5=b5, cur5=cur5,
            ema_f=ind.ema(closes, 8)[-1] if closes else None, ema_s=ind.ema(closes, 21)[-1] if closes else None,
            rsi5=ind.rsi(closes, 14)[-1] if len(closes) > 14 else None, atr5=ind.atr(b5, 14) if len(b5) >= 4 else None,
            bb=[], orng=ind.opening_range(c1, 15),
            iv_pctile=self._iv_percentile(sym, m.get("atm_iv")),
            iv_chg15=((ivs[-1][1] / ivs[0][1] - 1) * 100 if len(ivs) >= 2 and ivs[0][1] else None),
            pcr_chg15=(pcrs[-1][1] - pcrs[0][1] if len(pcrs) >= 2 else None),
            put_doi=sum(r["pe"]["oi"] - r["pe"]["prev_oi"] for r in near if r["pe"]),
            call_doi=sum(r["ce"]["oi"] - r["ce"]["prev_oi"] for r in near if r["ce"]),
            state=st)

    # ------------------------------------------------------------------ gates
    def gates_ok(self, sym):
        st, S = self.state, self.cfg
        now = st.clock.now()
        if not (_hm(S["first_signal_time"]) <= now.time() <= _hm(S["last_signal_time"])):
            return False
        if not st.clock.virtual:
            from upstox.instruments import UNDERLYINGS
            if not st.market_open_now():
                return False
            if not st.fresh(UNDERLYINGS[sym]["key"], S["stale_tick_seconds"]):
                self._note(sym, "index tick stale — websocket not delivering; no signals")
                return False
            seg = st.segment_status.get("BSE_FO" if sym == "SENSEX" else "NSE_FO")
            if seg and seg != "NORMAL_OPEN":
                return False
        if self.tracker.day_r(sym) <= S["daily_stop_r"]:
            self._note(sym, f"daily stop hit ({self.tracker.day_r(sym):+.1f}R) — no more signals today")
            return False
        if self.tracker.open_count() >= S["max_open"] or self.tracker.count_today(sym) >= S["max_signals_per_day"]:
            return False
        return True

    # ------------------------------------------------------------------ run one underlying
    def run(self, sym):
        if settings.ALLOW_SELLING:
            log.warning("ALLOW_SELLING is set but this build has no selling strategies; ignoring")
        if not self.gates_ok(sym):
            return []
        ctx = self.build_ctx(sym)
        if ctx is None or not ctx.atr5:
            return []
        cands = []
        for name, fn in STRATEGIES.items():
            try:
                for su in fn(ctx, self.cfg):
                    sig = self._evaluate(ctx, su)
                    if sig:
                        cands.append(sig)
            except Exception as e:
                self.state.error(f"strategy:{name}", e)
        if not cands:
            return []
        best = max(cands, key=lambda s: s.confidence)            # one signal per underlying per cycle
        assert_buy_only(best.action)
        best.save()
        self.tracker.register(best)
        return [best]

    # ------------------------------------------------------------------ one setup -> a Signal or None
    def _evaluate(self, ctx, su):
        S, sym, d = self.cfg, ctx.sym, su.direction
        if (self.tracker.on_cooldown(sym, su.strategy, d, ctx.now, S["cooldown_minutes"]) or self.tracker.has_active(sym, strategy=su.strategy)
                or self.tracker.has_active(sym, opposite=d)):
            return None
        pick = self._pick(ctx, su)
        if not pick:
            self._note(sym, f"{su.strategy} {d} setup found but no contract passes delta/spread/OI filters")
            return None
        row, s = pick
        plan = self._plan(ctx, su, s)
        if isinstance(plan, str):
            self._note(sym, f"{su.strategy} {d} rejected: {plan}")
            return None
        parts = [su.base, *[f(ctx, d) for f in (score_trend, score_oi, score_pcr)], score_iv(ctx), score_time(ctx)]
        sp = spread_pct(s)
        liq = (6 if sp <= 1 else 4 if sp <= 2 else 2) + (4 if s["oi"] >= 200000 else 2)
        pts = [p[0] if isinstance(p, tuple) else p for p in parts]
        conf = int(min(100, round(sum(pts) + liq)))
        if conf < S["min_confidence"]:
            self._note(sym, f"{su.strategy} {d} confidence {conf} < {S['min_confidence']}")
            return None
        reasons = list(su.reasons) + [p[1] for p in parts[1:4] + parts[4:5] if isinstance(p, tuple) and p[1]]
        reasons.append(f"Contract: {int(row['strike'])} {d} Δ{abs(s['delta']):.2f}, spread {sp:.1f}%, OI {s['oi']:,.0f}")
        action = "BUY_" + d
        assert_buy_only(action)
        m = ctx.metrics
        return Signal(
            mode=self.state.mode, created_at=ctx.now, strategy=su.strategy, underlying=sym, expiry=ctx.expiry, action=action,
            instrument_key=s["key"], trading_symbol=f"{sym} {int(row['strike'])} {d} {ctx.expiry}", strike=row["strike"], lot_size=ctx.lot,
            entry=plan["entry"], sl=plan["sl"], t1=plan["t1"], t2=plan["t2"], confidence=conf, peak=plan["entry"], trough=plan["entry"],
            reasons=reasons,
            greeks={"delta": s["delta"], "gamma": s["gamma"], "theta": s["theta"], "vega": s["vega"], "iv": s["iv"], "pop": s.get("pop"),
                    "spread_pct": round(sp, 2), "oi": s["oi"], "volume": s["vol"]},
            context={"spot": round(ctx.spot, 2), "atm": ctx.atm, "pcr": m.get("pcr_oi"), "pcr_chg15": ctx.pcr_chg15, "max_pain": m.get("max_pain"),
                     "call_wall": m.get("call_wall"), "put_wall": m.get("put_wall"), "atm_iv": m.get("atm_iv"), "iv_pctile": ctx.iv_pctile,
                     "expected_move_pct": m.get("expected_move_pct"), "dte": ctx.dte_days, "risk0": plan["risk"], "sl0": plan["sl"],
                     "need_move_pts": plan["need"], "theta_cost": plan["theta_cost"], "t1_r": S["t1_r"], "t2_r": S["t2_r"],
                     "score_parts": {"pattern": round(su.base, 1), "trend": round(pts[1], 1), "oi": round(pts[2], 1), "pcr": round(pts[3], 1),
                                     "iv": round(pts[4], 1), "timing": pts[5], "liquidity": liq},
                     "session": self.state.clock.session if self.state.clock.virtual else None})

    # ------------------------------------------------------------------ contract selection (BUY ATM-ish, liquid, sane delta)
    def _pick(self, ctx, su):
        S, side = self.cfg, ("ce" if su.direction == "CE" else "pe")
        best = None
        for r in ctx.rows:
            s = r[side]
            if not s or abs(r["strike"] - ctx.atm) > 3 * ctx.step:
                continue
            if su.key_hint and s["key"] != su.key_hint:
                continue
            dlt = abs(s["delta"])
            sp = spread_pct(s)
            if not (S["delta_min"] <= dlt <= S["delta_max"]) or s["ask"] <= 0 or s["bid"] <= 0 or sp > S["max_spread_pct"]:
                continue
            if s["oi"] < S["min_oi"] or s["vol"] < S["min_volume"]:
                continue
            score = abs(dlt - S["delta_target"]) + sp / 40
            if best is None or score < best[0]:
                best = (score, r, s)
        return (best[1], best[2]) if best else None

    # ------------------------------------------------------------------ risk plan
    def _plan(self, ctx, su, s):
        S = self.cfg
        entry = _rt(s["ask"] * (1 + S["slippage_pct"] / 100))
        d, g = abs(s["delta"]), abs(s["gamma"])
        dist = su.spot_dist or ctx.atr5
        raw = su.premium_sl if su.premium_sl else entry - max(d * dist - 0.5 * g * dist * dist, 0.0)
        sl = _rt(min(max(raw, entry * (1 - S["sl_max_pct"] / 100)), entry * (1 - S["sl_min_pct"] / 100)))
        risk = entry - sl
        if risk <= 0:
            return "non-positive risk"
        t1, t2 = _rt(entry + S["t1_r"] * risk), _rt(entry + S["t2_r"] * risk)
        need = (t1 - entry) / max(d, 0.1)
        if need > 3.0 * ctx.atr5:
            return f"T1 needs {need:.0f} spot pts (> 3 ATR) — target unrealistic"
        theta_cost = abs(s["theta"]) * S["max_hold_minutes"] / 375.0
        if theta_cost > S["theta_cost_max_frac"] * risk:
            return f"theta bleed over the hold (₹{theta_cost:.1f}) > {S['theta_cost_max_frac']:.0%} of risk"
        return {"entry": entry, "sl": sl, "t1": t1, "t2": t2, "risk": round(risk, 2), "need": round(need, 1), "theta_cost": round(theta_cost, 2)}
