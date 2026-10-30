"""Option-chain parsing and analytics (PCR, max pain, OI walls, IV skew, expected move, gamma exposure, buildup)."""
import math

_F = ("ltp", "close", "vol", "oi", "prev_oi", "bid", "bid_qty", "ask", "ask_qty", "iv", "delta", "gamma", "theta", "vega", "pop")


def _num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _side(d):
    if not d:
        return None
    md, g = d.get("market_data") or {}, d.get("option_greeks") or {}
    return {
        "key": d.get("instrument_key"),
        "ltp": _num(md.get("ltp")), "close": _num(md.get("close_price")), "vol": _num(md.get("volume")),
        "oi": _num(md.get("oi")), "prev_oi": _num(md.get("prev_oi")),
        "bid": _num(md.get("bid_price")), "bid_qty": _num(md.get("bid_qty")),
        "ask": _num(md.get("ask_price")), "ask_qty": _num(md.get("ask_qty")),
        "iv": _num(g.get("iv")), "delta": _num(g.get("delta")), "gamma": _num(g.get("gamma")),
        "theta": _num(g.get("theta")), "vega": _num(g.get("vega")), "pop": _num(g.get("pop")),
    }


def parse_chain(api_rows):
    """Upstox /option/chain `data` -> (rows sorted by strike, spot). iv from this endpoint is already in percent."""
    rows, spot = [], 0.0
    for r in api_rows or []:
        spot = _num(r.get("underlying_spot_price")) or spot
        rows.append({"strike": _num(r.get("strike_price")), "expiry": r.get("expiry"),
                     "ce": _side(r.get("call_options")), "pe": _side(r.get("put_options"))})
    rows.sort(key=lambda x: x["strike"])
    return rows, spot


def spread_pct(s):
    if not s or s["bid"] <= 0 or s["ask"] <= 0:
        return 99.0
    return (s["ask"] - s["bid"]) / ((s["ask"] + s["bid"]) / 2) * 100


def buildup(s):
    """Classic price x OI quadrant for one option."""
    if not s:
        return None
    dp, doi = s["ltp"] - s["close"], s["oi"] - s["prev_oi"]
    if doi == 0 or dp == 0:
        return "neutral"
    if dp > 0 and doi > 0: return "long_buildup"
    if dp < 0 and doi > 0: return "short_buildup"
    if dp < 0 and doi < 0: return "long_unwinding"
    return "short_covering"


def atm_strike(rows, spot):
    return min((r["strike"] for r in rows), key=lambda k: abs(k - spot)) if rows else None


def max_pain(rows):
    strikes = [r["strike"] for r in rows]
    best, best_pain = None, None
    for k in strikes:
        pain = 0.0
        for r in rows:
            if r["ce"]: pain += max(0.0, k - r["strike"]) * r["ce"]["oi"]
            if r["pe"]: pain += max(0.0, r["strike"] - k) * r["pe"]["oi"]
        if best_pain is None or pain < best_pain:
            best, best_pain = k, pain
    return best


def _avg(vals):
    v = [x for x in vals if x and x > 0]
    return sum(v) / len(v) if v else None


def compute_metrics(rows, spot, lot, step=None):
    """Everything the dashboard + signal engine need from one chain snapshot."""
    if not rows or not spot:
        return {}
    atm = atm_strike(rows, spot)
    ix = next(i for i, r in enumerate(rows) if r["strike"] == atm)
    arow = rows[ix]
    step = step or (rows[1]["strike"] - rows[0]["strike"] if len(rows) > 1 else 50)

    tc = sum(r["ce"]["oi"] for r in rows if r["ce"])
    tp = sum(r["pe"]["oi"] for r in rows if r["pe"])
    tcv = sum(r["ce"]["vol"] for r in rows if r["ce"])
    tpv = sum(r["pe"]["vol"] for r in rows if r["pe"])
    above = [r for r in rows if r["strike"] >= spot and r["ce"]]
    below = [r for r in rows if r["strike"] <= spot and r["pe"]]
    call_wall = max(above, key=lambda r: r["ce"]["oi"])["strike"] if above else None
    put_wall = max(below, key=lambda r: r["pe"]["oi"])["strike"] if below else None
    top_c = sorted((r for r in rows if r["ce"]), key=lambda r: -r["ce"]["oi"])[:3]
    top_p = sorted((r for r in rows if r["pe"]), key=lambda r: -r["pe"]["oi"])[:3]

    atm_iv = _avg([arow["ce"] and arow["ce"]["iv"], arow["pe"] and arow["pe"]["iv"]])
    straddle = (arow["ce"]["ltp"] if arow["ce"] else 0) + (arow["pe"]["ltp"] if arow["pe"] else 0)

    # 25-delta-ish skew: put IV minus call IV at |delta| ~ 0.25
    def near(side, target):
        c = [r[side] for r in rows if r[side] and r[side]["iv"] > 0 and r[side]["oi"] > 0]
        return min(c, key=lambda s: abs(abs(s["delta"]) - target)) if c else None
    p25, c25 = near("pe", 0.25), near("ce", 0.25)
    skew = (p25["iv"] - c25["iv"]) if (p25 and c25) else None

    gex_total, series = 0.0, {k: [] for k in ("strikes", "call_oi", "put_oi", "call_doi", "put_doi", "call_iv", "put_iv",
                                              "call_gamma", "put_gamma", "call_theta", "put_theta", "call_vega", "put_vega", "gex")}
    for r in rows:
        ce, pe = r["ce"] or {}, r["pe"] or {}
        g = (ce.get("gamma", 0) * ce.get("oi", 0) - pe.get("gamma", 0) * pe.get("oi", 0)) * lot * spot * spot * 0.01
        gex_total += g
        series["strikes"].append(r["strike"])
        series["call_oi"].append(ce.get("oi", 0)); series["put_oi"].append(pe.get("oi", 0))
        series["call_doi"].append(ce.get("oi", 0) - ce.get("prev_oi", 0)); series["put_doi"].append(pe.get("oi", 0) - pe.get("prev_oi", 0))
        series["call_iv"].append(ce.get("iv") or None); series["put_iv"].append(pe.get("iv") or None)
        series["call_gamma"].append(ce.get("gamma", 0)); series["put_gamma"].append(pe.get("gamma", 0))
        series["call_theta"].append(ce.get("theta", 0)); series["put_theta"].append(pe.get("theta", 0))
        series["call_vega"].append(ce.get("vega", 0)); series["put_vega"].append(pe.get("vega", 0))
        series["gex"].append(g)

    w = [r for r in rows if abs(r["strike"] - atm) <= 5 * step]
    return {
        "spot": spot, "atm": atm, "step": step, "lot": lot,
        "pcr_oi": (tp / tc) if tc else None, "pcr_vol": (tpv / tcv) if tcv else None,
        "total_call_oi": tc, "total_put_oi": tp,
        "call_doi_total": sum(r["ce"]["oi"] - r["ce"]["prev_oi"] for r in rows if r["ce"]),
        "put_doi_total": sum(r["pe"]["oi"] - r["pe"]["prev_oi"] for r in rows if r["pe"]),
        "max_pain": max_pain(rows), "call_wall": call_wall, "put_wall": put_wall,
        "top_call_oi": [r["strike"] for r in top_c], "top_put_oi": [r["strike"] for r in top_p],
        "atm_iv": atm_iv, "skew25": skew,
        "straddle": straddle, "expected_move_pct": (straddle / spot * 100 / 0.8) if spot else None,  # ~1-sigma to expiry
        "atm_ce_ltp": arow["ce"]["ltp"] if arow["ce"] else None, "atm_pe_ltp": arow["pe"]["ltp"] if arow["pe"] else None,
        "gex_proxy": gex_total,
        "window_call_doi": sum(r["ce"]["oi"] - r["ce"]["prev_oi"] for r in w if r["ce"]),   # ATM±5 strikes
        "window_put_doi": sum(r["pe"]["oi"] - r["pe"]["prev_oi"] for r in w if r["pe"]),
        "buildup_atm": {"ce": buildup(arow["ce"]), "pe": buildup(arow["pe"])},
        "series": series,
    }
