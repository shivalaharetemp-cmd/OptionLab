"""JSON payload builders shared by the HTTP API and the browser WebSocket."""
from analytics.greeks import bs_price, years_to_expiry
from analytics.orchestrator import get_orchestrator
from analytics.state import STATE
from upstox.instruments import UNDERLYINGS

_FIELDS = ("key", "ltp", "close", "vol", "oi", "prev_oi", "bid", "ask", "iv", "delta", "gamma", "theta", "vega", "pop")


def _side(s):
    return {k: s[k] for k in _FIELDS} if s else None


def meta(sym):
    st = STATE
    return {"symbols": list(getattr(get_orchestrator(), "watch", [])), "symbol": sym, "name": UNDERLYINGS.get(sym, {}).get("name"),
            "expiries": st.expiries.get(sym, []), "expiry": st.selected.get(sym), "lot": st.lot.get(sym),
            "mode": st.mode, "now": st.clock.now().isoformat(), "virtual_clock": st.clock.virtual}


def chain_payload(sym):
    st = STATE
    ch = st.chains.get(sym)
    out = meta(sym)
    out["ready"] = bool(ch)
    if not ch:
        out["errors"] = list(st.errors)[:5]
        return out
    rows = st.live_rows(sym)
    hist = list(st.metric_hist.get(sym, []))[-300:]
    out.update(rows=[{"strike": r["strike"], "ce": _side(r["ce"]), "pe": _side(r["pe"])} for r in rows], metrics=ch["metrics"], version=ch["version"],
               spot=st.spot.get(sym) or ch["spot"], vix=st.vix, history=hist, errors=list(st.errors)[:5], notes=list(st.engine_notes)[:12])
    return out


def live_payload(sym):
    st, orch = STATE, get_orchestrator()
    keys = st.watch_keys.get(sym, set())
    ticks = {}
    for k in keys:
        t = st.ticks.get(k)
        if t:
            ticks[k] = [t.get("ltp"), t.get("bid"), t.get("ask"), t.get("oi"), t.get("iv"), t.get("delta"), t.get("vtt")]
    marks = {}
    tr = getattr(orch, "tracker", None)
    if tr:
        for s in list(tr.active):
            px = tr.mark(s)
            if px:
                marks[s.pk] = {"px": px, "r": round((px - s.entry) / (s.context.get("risk0") or 1), 2)}
    ix = st.ticks.get(UNDERLYINGS.get(sym, {}).get("key", ""), {})
    return {"t": "live", "now": st.clock.now().isoformat(), "spot": st.spot.get(sym), "spot_chg": (ix.get("ltp") - ix["cp"]) if ix.get("cp") and ix.get("ltp") else None,
            "vix": st.vix, "feed": dict(st.feed), "segments": st.segment_status, "ticks": ticks, "marks": marks,
            "sigv": getattr(tr, "version", 0), "chain_version": (st.chains.get(sym) or {}).get("version", 0), "errors": list(st.errors)[:3]}


def signal_dict(s):
    return {"id": s.pk, "t": s.created_at.isoformat(), "strategy": s.strategy, "sym": s.underlying, "expiry": s.expiry, "action": s.action,
            "symbol": s.trading_symbol, "strike": s.strike, "lot": s.lot_size, "entry": s.entry, "sl": s.sl, "t1": s.t1, "t2": s.t2, "conf": s.confidence,
            "status": s.status, "exit": s.exit_price, "exit_at": s.exit_at.isoformat() if s.exit_at else None, "r": s.realized_r, "reasons": s.reasons,
            "greeks": s.greeks, "ctx": s.context, "mode": s.mode, "risk_rs": round((s.entry - s.context.get("sl0", s.sl)) * s.lot_size, 0)}


def scenario(sig, spot_moves=(-1.0, -0.5, -0.25, 0, 0.25, 0.5, 1.0, 1.5), horizons=(0, 30, 120)):
    """Re-price the long option with Black-Scholes at the signal's IV: premium & P&L (per lot) vs spot move and time passed."""
    g, c = sig.greeks, sig.context
    S0, K, iv = c.get("spot"), sig.strike, (g.get("iv") or 0) / 100
    call = sig.action == "BUY_CE"
    T0 = years_to_expiry(sig.expiry, STATE.clock.now())
    grid = []
    for mv in spot_moves:
        row = {"move_pct": mv, "spot": round(S0 * (1 + mv / 100), 1), "cells": []}
        for h in horizons:
            T = max(T0 - h * 60 / (365 * 86400), 60 / (365 * 86400))
            px = bs_price(row["spot"], K, T, iv, call)
            row["cells"].append({"h": h, "px": round(px, 2), "pnl": round((px - sig.entry) * sig.lot_size)})
        grid.append(row)
    be = K + sig.entry if call else K - sig.entry
    return {"grid": grid, "horizons": list(horizons), "breakeven_at_expiry": be, "max_loss_rs": round(sig.entry * sig.lot_size), "iv": iv * 100}
