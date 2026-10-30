"""python manage.py live_check [--seconds 20] [--symbol NIFTY]

End-to-end check of your real Upstox setup: token → REST (status, contracts, chain, greeks, candles, OI/PCR/max-pain, profile)
→ WebSocket (authorize, connect, subscribe, decode live ticks) → analytics + a dry run of the signal engine on the live chain.
Run it on YOUR machine (during market hours for live ticks). Prints PASS/FAIL/SKIP per step; exit code 1 if a core step fails.
"""
import asyncio
import base64
import json
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand

from analytics.chain import compute_metrics, parse_chain
from upstox.client import IST, UpstoxClient, UpstoxError
from upstox.instruments import UNDERLYINGS, VIX_KEY
from upstox.feed import decode_feed
import uuid


class Command(BaseCommand):
    help = "Live end-to-end check of the Upstox token, REST APIs, websocket feed and analytics."

    def add_arguments(self, p):
        p.add_argument("--seconds", type=int, default=20)
        p.add_argument("--symbol", default="NIFTY")

    def handle(self, *a, **o):
        self.res = []
        sym = o["symbol"].upper()
        meta = UNDERLYINGS[sym]
        tok = settings.UPSTOX_TOKEN
        if not tok:
            self.stderr.write("UPSTOX_ANALYTICS_TOKEN is empty — put it in .env")
            raise SystemExit(1)
        c = UpstoxClient(tok)
        today = datetime.now(IST).date().isoformat()
        ctx = {}

        def step(name, fn, core=True):
            t = time.time()
            try:
                msg = fn() or ""
                self.res.append((name, "PASS", msg, core))
                self.stdout.write(f"PASS  {name:<28} {msg}   ({time.time()-t:.1f}s)")
            except UpstoxError as e:
                self.res.append((name, "FAIL", str(e), core))
                self.stdout.write(f"FAIL  {name:<28} {e}")
            except Exception as e:
                self.res.append((name, "FAIL", f"{type(e).__name__}: {e}", core))
                self.stdout.write(f"FAIL  {name:<28} {type(e).__name__}: {e}")

        def token():
            part = tok.split(".")[1]
            cl = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            left = (cl["exp"] - time.time()) / 86400
            assert left > 0, "token expired"
            return f"valid, {left:.0f} days left, extended(read-only)={cl.get('isExtended')}"

        def status():
            d = c.exchange_status("NSE")
            ctx["status"] = d
            return f"NSE: {d}"

        def contracts():
            rows = c.option_contracts(meta["key"])
            ctx["exp"] = sorted({r["expiry"] for r in rows if r["expiry"] >= today})
            ctx["lot"] = next(r["lot_size"] for r in rows if r.get("lot_size"))
            return f"{len(rows)} contracts, lot {ctx['lot']}, next expiries {ctx['exp'][:3]}"

        def chain():
            rows, spot = parse_chain(c.option_chain(meta["key"], ctx["exp"][0]))
            m = compute_metrics(rows, spot, ctx["lot"], meta["step"])
            ctx["rows"], ctx["spot"], ctx["m"] = rows, spot, m
            return f"{len(rows)} strikes, spot {spot}, ATM {m['atm']}, PCR {m['pcr_oi']:.2f}, max pain {m['max_pain']}, ATM IV {m['atm_iv']:.1f}%"

        def greeks():
            a = next(r for r in ctx["rows"] if r["strike"] == ctx["m"]["atm"])
            d = c.option_greeks([a["ce"]["key"], a["pe"]["key"]])
            ctx["atm_keys"] = [a["ce"]["key"], a["pe"]["key"]]
            return "; ".join(f"{k.split(':')[-1]} Δ{v.get('delta')} IV{v.get('iv')}" for k, v in d.items())

        def candles():
            cs = c.intraday_candles(meta["key"], "minutes", 1)
            return f"{len(cs)} 1-min candles today" + (f", last close {cs[-1]['c']}" if cs else " (market not open yet?)")

        def hist():
            cs = c.historical_candles(meta["key"], "days", 1, today, (datetime.now(IST) - timedelta(days=10)).date().isoformat())
            return f"{len(cs)} daily candles"

        def oi_pcr():
            e = ctx["exp"][0]
            a, b, d = c.oi(meta["key"], e, today), c.pcr(meta["key"], e, today, 60), c.max_pain(meta["key"], e, today, 60)
            return f"OI calls {a['total_calls']:,} puts {a['total_puts']:,}; PCR {b['pcr']:.2f}; max pain {d['max_pain']}"

        def profile():
            p = c.profile()
            return f"{p.get('user_name')} ({p.get('user_id')}) exchanges={p.get('exchanges')}"

        step("token (local JWT check)", token)
        step("exchange status NSE", status)
        step(f"{sym} option contracts", contracts)
        if "exp" in ctx:
            step(f"{sym} option chain + analytics", chain)
            if "rows" in ctx:
                step("option greeks API", greeks)
            step("OI / PCR / max-pain (market info)", oi_pcr, core=False)
        step("intraday candles (V3)", candles, core=False)
        step("historical daily candles (V3)", hist, core=False)
        step("account profile (static IP)", profile, core=False)
        self.ws_check(c, meta, ctx, o["seconds"], step)

        self.stdout.write("\n" + "=" * 60)
        bad = [r for r in self.res if r[1] == "FAIL" and r[3]]
        soft = [r for r in self.res if r[1] == "FAIL" and not r[3]]
        self.stdout.write(f"{sum(r[1]=='PASS' for r in self.res)} passed, {len(bad)} core failures, {len(soft)} optional failures")
        if any("profile" in r[0] for r in soft):
            self.stdout.write("note: profile/account APIs only work from a whitelisted static IP; the rest of the app does not need it.")
        raise SystemExit(1 if bad else 0)

    # ------------------------------------------------------------------ websocket
    def ws_check(self, c, meta, ctx, seconds, step):
        got = {"msgs": 0, "ticks": set(), "greeks": 0, "segments": None, "index": None, "iv": None}

        def run():
            import websockets

            async def main():
                url = c.feed_authorize_url()
                async with websockets.connect(url, max_size=None, ping_interval=None, open_timeout=15) as ws:
                    keys = [meta["key"], VIX_KEY] + ctx.get("atm_keys", [])
                    await ws.send(json.dumps({"guid": uuid.uuid4().hex[:20], "method": "sub", "data": {"mode": "full", "instrumentKeys": keys}}).encode())
                    end = time.time() + seconds
                    while time.time() < end:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=max(end - time.time(), 0.1))
                        except asyncio.TimeoutError:
                            break
                        if not isinstance(raw, (bytes, bytearray)):
                            continue
                        m = decode_feed(bytes(raw))
                        got["msgs"] += 1
                        if m["segments"]:
                            got["segments"] = m["segments"]
                        for k, t in m["ticks"].items():
                            got["ticks"].add(k)
                            if k == meta["key"] and t.get("ltp"):
                                got["index"] = t["ltp"]
                            if t.get("delta") is not None:
                                got["greeks"] += 1
                                got["iv"] = t.get("iv")
            asyncio.run(main())
            if not got["msgs"]:
                raise RuntimeError("connected but no frames received")
            seg = (got["segments"] or {}).get("NSE_FO", "?")
            return (f"{got['msgs']} frames in {seconds}s, {len(got['ticks'])} instruments, index {got['index']}, "
                    f"{got['greeks']} greek ticks (IV {got['iv']}), NSE_FO={seg}")

        step("websocket V3 (authorize+stream)", run)
