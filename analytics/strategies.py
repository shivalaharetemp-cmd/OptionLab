"""Buyer-only setups. Each strategy looks at one underlying and proposes "buy a CE" or "buy a PE" — never a sale.

Shared confirmation scores (trend / OI flow / PCR / IV regime / timing) are added by the engine so every strategy is
judged on the same evidence. Pattern points (`base`, <=35) come from the strategy; the sum is the 0-100 confidence.
"""
from dataclasses import dataclass, field
from datetime import time as dtime

from . import indicators as ind


@dataclass
class Setup:
    strategy: str
    direction: str                      # "CE" (bullish) | "PE" (bearish) — both are BUYS
    base: float
    reasons: list
    spot_dist: float | None = None      # adverse spot move (points) that invalidates the idea
    key_hint: str | None = None         # strategy wants this exact option (flow strategy)
    premium_sl: float | None = None     # premium-based stop, if the strategy has a natural one
    extra: dict = field(default_factory=dict)


@dataclass
class Ctx:
    sym: str; now: object; spot: float; lot: int; expiry: str; dte_days: float
    rows: list; metrics: dict; atm: float; step: float
    c1: list; b5: list; cur5: object
    ema_f: float | None; ema_s: float | None; rsi5: float | None; atr5: float | None; bb: list
    orng: dict | None
    iv_pctile: float | None; iv_chg15: float | None; pcr_chg15: float | None
    put_doi: float; call_doi: float
    state: object = None

    @property
    def tod(self):
        return self.now.time()


def _in(ctx, a, b):
    return dtime(*a) <= ctx.tod <= dtime(*b)


# --------------------------------------------------------------------------- shared evidence (0..~60 pts)
def score_trend(c, d):
    if c.ema_f is None or c.ema_s is None or not c.atr5:
        return 0, None
    sep = (c.ema_f - c.ema_s) / c.atr5 * (1 if d == "CE" else -1)
    ok = (c.spot > c.ema_s) if d == "CE" else (c.spot < c.ema_s)
    pts = (10 if sep > 0.15 else 5 if sep > 0 else 0) + (5 if ok else 0)
    return pts, (f"5m trend agrees (EMA8/21 separation {sep:+.2f} ATR)" if pts >= 10 else None)


def score_oi(c, d):
    tot = abs(c.put_doi) + abs(c.call_doi)
    if tot <= 0:
        return 0, None
    r = (c.put_doi - c.call_doi) / tot            # +1 = puts being written / calls unwinding (bullish)
    pts = max(0.0, r if d == "CE" else -r) * 20
    return pts, (f"OI flow near ATM supports {d} (put ΔOI {c.put_doi:,.0f} vs call ΔOI {c.call_doi:,.0f})" if pts >= 8 else None)


def score_pcr(c, d):
    ch = c.pcr_chg15
    if ch is None:
        return 0, None
    sgn = ch if d == "CE" else -ch
    pts = 10 if sgn > 0.02 else 5 if sgn > 0 else 0
    return pts, (f"PCR moving the right way ({ch:+.3f} in 15m)" if pts >= 5 else None)


def score_iv(c):
    """Buyers want cheap, non-spiking vol. Unknown history -> neutral."""
    if c.iv_pctile is None:
        return 5, None
    pts = 10 if c.iv_pctile < 40 else 6 if c.iv_pctile < 60 else 2 if c.iv_pctile < 80 else 0
    if c.iv_chg15 is not None and c.iv_chg15 > 10:
        pts -= 3
    return max(pts, 0), (f"IV is not rich (ATM IV percentile {c.iv_pctile:.0f})" if pts >= 6 else None)


def score_time(c):
    prime = _in(c, (9, 45), (11, 30)) or _in(c, (13, 30), (14, 30))
    return (5 if prime else 2), None


# --------------------------------------------------------------------------- S1: opening-range breakout + OI confirmation
def s_orb(c, cfg):
    if not c.orng or not c.b5 or not c.atr5 or not _in(c, (9, 30), (11, 45)):
        return []
    hi, lo = c.orng["high"], c.orng["low"]
    width = hi - lo
    if width < 0.0012 * c.spot or width > 0.012 * c.spot:
        return []
    last = c.b5[-1]
    buf = max(0.0003 * c.spot, 0.1 * c.atr5)
    mid = (hi + lo) / 2
    out = []
    if last["c"] > hi + buf and (len(c.b5) < 2 or c.b5[-2]["c"] <= hi + 3 * buf):
        d, dist = "CE", c.spot - mid
        why = [f"5m close {last['c']:.0f} broke above the 15-min opening range high {hi:.0f}"]
    elif last["c"] < lo - buf and (len(c.b5) < 2 or c.b5[-2]["c"] >= lo - 3 * buf):
        d, dist = "PE", mid - c.spot
        why = [f"5m close {last['c']:.0f} broke below the 15-min opening range low {lo:.0f}"]
    else:
        return []
    strength = min(1.0, abs(last["c"] - (hi if d == "CE" else lo)) / c.atr5)
    out.append(Setup("ORB_OI", d, 25 + 10 * strength, why, spot_dist=min(max(dist, 0.8 * c.atr5), 2.2 * c.atr5)))
    return out


# --------------------------------------------------------------------------- S2: 5m trend pullback
def s_pullback(c, cfg):
    if len(c.b5) < 12 or not c.atr5 or c.ema_f is None or not _in(c, (10, 15), (14, 30)):
        return []
    last, prev3 = c.b5[-1], c.b5[-3:]
    out = []
    up = c.ema_f > c.ema_s and (c.ema_f - c.ema_s) > 0.2 * c.atr5
    dn = c.ema_f < c.ema_s and (c.ema_s - c.ema_f) > 0.2 * c.atr5
    rsi = c.rsi5
    if up and min(b["l"] for b in prev3) <= c.ema_f + 0.2 * c.atr5 and last["c"] > c.ema_f and last["c"] > last["o"] and (rsi is None or 42 <= rsi <= 66):
        sl_lvl = min(min(b["l"] for b in prev3), c.ema_s) - 0.1 * c.atr5
        out.append(Setup("PULLBACK", "CE", 30, ["Uptrend pullback to the 5m EMA8 held; bullish re-entry bar"], spot_dist=max(c.spot - sl_lvl, 0.7 * c.atr5)))
    if dn and max(b["h"] for b in prev3) >= c.ema_f - 0.2 * c.atr5 and last["c"] < c.ema_f and last["c"] < last["o"] and (rsi is None or 34 <= rsi <= 58):
        sl_lvl = max(max(b["h"] for b in prev3), c.ema_s) + 0.1 * c.atr5
        out.append(Setup("PULLBACK", "PE", 30, ["Downtrend pullback to the 5m EMA8 rejected; bearish re-entry bar"], spot_dist=max(sl_lvl - c.spot, 0.7 * c.atr5)))
    return out


# --------------------------------------------------------------------------- S3: OI wall bounce / wall break
def s_oi_walls(c, cfg):
    m = c.metrics
    if len(c.b5) < 3 or not c.atr5 or not _in(c, (9, 45), (14, 30)):
        return []
    last, out = c.b5[-1], []
    pw, cw = m.get("put_wall"), m.get("call_wall")
    near = 0.002 * c.spot
    rng_ = max(last["h"] - last["l"], 1e-9)

    def wall_doi(strike, side):
        for r in c.rows:
            if r["strike"] == strike and r[side]:
                return r[side]["oi"] - r[side]["prev_oi"], r[side]["oi"]
        return 0, 0

    if pw and 0 <= last["l"] - pw <= near and last["c"] > last["o"] and (min(last["o"], last["c"]) - last["l"]) / rng_ >= 0.35:
        d_oi, _ = wall_doi(pw, "pe")
        if d_oi > 0:
            out.append(Setup("OI_WALLS", "CE", 30, [f"Rejected the {pw:.0f} put-OI wall (support) with a long lower wick while put OI keeps building"], spot_dist=max(c.spot - (pw - 0.001 * c.spot), 0.7 * c.atr5)))
    if cw and 0 <= cw - last["h"] <= near and last["c"] < last["o"] and (last["h"] - max(last["o"], last["c"])) / rng_ >= 0.35:
        d_oi, _ = wall_doi(cw, "ce")
        if d_oi > 0:
            out.append(Setup("OI_WALLS", "PE", 30, [f"Rejected the {cw:.0f} call-OI wall (resistance) with a long upper wick while call OI keeps building"], spot_dist=max((cw + 0.001 * c.spot) - c.spot, 0.7 * c.atr5)))
    if cw:                                                    # writers capitulating above resistance
        d_oi, oi = wall_doi(cw, "ce")
        if oi and d_oi < -0.03 * oi and last["c"] > cw + 0.0003 * c.spot:
            out.append(Setup("OI_WALLS", "CE", 33, [f"Closed above the {cw:.0f} call wall while its OI unwinds ({d_oi / oi:+.0%}) — resistance giving way"], spot_dist=max(c.spot - (cw - 0.001 * c.spot), 0.7 * c.atr5)))
    if pw:
        d_oi, oi = wall_doi(pw, "pe")
        if oi and d_oi < -0.03 * oi and last["c"] < pw - 0.0003 * c.spot:
            out.append(Setup("OI_WALLS", "PE", 33, [f"Closed below the {pw:.0f} put wall while its OI unwinds ({d_oi / oi:+.0%}) — support giving way"], spot_dist=max((pw + 0.001 * c.spot) - c.spot, 0.7 * c.atr5)))
    return out


# --------------------------------------------------------------------------- S4: option flow / premium momentum (needs websocket ticks)
def _at(ser, ts, idx):
    prior = [p for p in ser if p[0] <= ts]
    return prior[-1][idx] if prior else None


def s_premium_momentum(c, cfg):
    st = c.state
    if st is None or not c.atr5 or not _in(c, (9, 45), (14, 40)) or len(c.c1) < 25:
        return []
    ts = c.now.timestamp()
    closes = [x["c"] for x in c.c1]
    e21 = ind.ema(closes, 21)[-1]
    ret5 = closes[-1] / closes[-6] - 1
    out = []
    for d, side in (("CE", "ce"), ("PE", "pe")):
        if (d == "CE" and not (c.spot > e21 and ret5 >= 0)) or (d == "PE" and not (c.spot < e21 and ret5 <= 0)):
            continue
        best = None
        for r in c.rows:
            s = r[side]
            if not s or abs(r["strike"] - c.atm) > 2 * c.step or not st.fresh(s["key"], 30):
                continue
            ser = st.series(s["key"], ts - 600)
            prior = [p for p in ser if p[0] < ts - 45]
            if len(ser) < 8 or len(prior) < 5:
                continue
            ltp = ser[-1][1]
            brk = ltp / max(p[1] for p in prior) - 1
            v_now, v_60, v_540 = ser[-1][3], _at(ser, ts - 60, 3), _at(ser, ts - 540, 3) or prior[0][3]
            if brk < 0.01 or not v_now or v_60 is None:
                continue
            rate = max((v_60 - v_540) / 8.0, 1.0)
            ratio = (v_now - v_60) / rate
            oi_then = _at(ser, ts - 300, 2) or ser[0][2]
            if ratio < 1.8 or ser[-1][2] < oi_then:
                continue
            swing = min(p[1] for p in ser if p[0] >= ts - 300)
            cand = (brk * ratio, r, s, ratio, brk, swing)
            best = cand if (best is None or cand[0] > best[0]) else best
        if best:
            _, r, s, ratio, brk, swing = best
            out.append(Setup("PREMIUM_FLOW", d, 25 + min(10.0, ratio * 2),
                             [f"{r['strike']:.0f} {d} premium +{brk:.1%} above its 9-min high on {ratio:.1f}x normal volume with OI rising (long build-up)"],
                             key_hint=s["key"], premium_sl=swing - 0.05, spot_dist=1.0 * c.atr5))
    return out


# --------------------------------------------------------------------------- S5: low-IV squeeze breakout
def s_squeeze(c, cfg):
    if len(c.b5) < 16 or not c.atr5 or not _in(c, (10, 30), (14, 15)):
        return []
    closes = [b["c"] for b in c.b5]
    bb = ind.bollinger(closes, 12, 2.0)
    bws = [x[3] for x in bb if x]
    if len(bws) < 6:
        return []
    prev, cur = bb[-2], bb[-1]
    rank = ind.percentile_rank(bws[:-2], prev[3]) if len(bws) > 3 else None
    if rank is None or rank > 25 or (c.iv_pctile is not None and c.iv_pctile > 55):
        return []
    last = c.b5[-1]
    if last["c"] > prev[1]:
        d, dist = "CE", c.spot - prev[0]
    elif last["c"] < prev[2]:
        d, dist = "PE", prev[0] - c.spot
    else:
        return []
    return [Setup("IV_SQUEEZE", d, 28 + (7 if c.iv_pctile is not None and c.iv_pctile < 40 else 0),
                  [f"5m Bollinger squeeze (bandwidth in the bottom {rank:.0f}% of today's bars) resolved {'up' if d == 'CE' else 'down'} while options are still cheap"],
                  spot_dist=min(max(dist, 0.8 * c.atr5), 2.0 * c.atr5))]


STRATEGIES = {
    "ORB_OI": s_orb,
    "PULLBACK": s_pullback,
    "OI_WALLS": s_oi_walls,
    "PREMIUM_FLOW": s_premium_momentum,
    "IV_SQUEEZE": s_squeeze,
}
