"""Small dependency-free technical helpers on lists / 1-minute candle dicts {t,o,h,l,c,v}."""
from datetime import time


def ema(vals, n):
    k, e, out = 2.0 / (n + 1), None, []
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    if len(closes) <= n:
        return [None] * len(closes)
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out = [None] * n
    out.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    return out


def atr(bars, n=14):
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / min(n, len(trs))
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def bollinger(closes, n=20, k=2.0):
    """-> list of (mid, upper, lower, bandwidth%) or None until n bars exist."""
    out = []
    for i in range(len(closes)):
        if i + 1 < n:
            out.append(None)
            continue
        w = closes[i + 1 - n:i + 1]
        m = sum(w) / n
        sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
        out.append((m, m + k * sd, m - k * sd, (2 * k * sd) / m * 100 if m else 0.0))
    return out


def percentile_rank(history, value):
    h = [x for x in history if x is not None]
    if not h:
        return None
    return 100.0 * sum(1 for x in h if x <= value) / len(h)


def _bucket(t, minutes):
    return ((t.hour * 60 + t.minute) - (9 * 60 + 15)) // minutes


def resample(candles, minutes, now=None):
    """Aggregate 1-minute candles into N-minute bars anchored at 09:15.
    Returns (completed_bars, in_progress_bar_or_None). A bar is complete once `now` has left its bucket."""
    bars, cur, cur_b = [], None, None
    for c in candles:
        b = _bucket(c["t"], minutes)
        if b != cur_b:
            if cur:
                bars.append(cur)
            cur, cur_b = {"t": c["t"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}, b
        else:
            cur["h"], cur["l"], cur["c"], cur["v"] = max(cur["h"], c["h"]), min(cur["l"], c["l"]), c["c"], cur["v"] + c["v"]
    if cur:
        if now is not None and _bucket(now, minutes) == cur_b:
            return bars, cur
        bars.append(cur)
    return bars, None


def opening_range(candles, minutes=15):
    """High/low of 09:15..09:15+minutes. None until the window has fully formed."""
    win = [c for c in candles if time(9, 15) <= c["t"].time() < _plus(time(9, 15), minutes)]
    if len(win) < minutes - 1:
        return None
    return {"high": max(c["h"] for c in win), "low": min(c["l"] for c in win)}


def _plus(t, minutes):
    total = t.hour * 60 + t.minute + minutes
    return time(total // 60, total % 60)
