"""Upstox Market Data Feed V3 over WebSocket (protobuf), driven from a background thread.

Auth: the analytics token calls GET /v3/feed/market-data-feed/authorize, which returns a SINGLE-USE wss:// URL.
Decoding: Upstox's own compiled proto ships inside the official `upstox-python-sdk`, so no .proto compile step is needed.
Limits honoured (normal plan): 2 connections, 2000 `full` keys. We open one connection and stream ATM±N strikes only.
"""
import asyncio
import json
import logging
import threading
import time
import uuid

log = logging.getLogger(__name__)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def decode_feed(data: bytes) -> dict:
    """Binary protobuf frame -> {'type','ts','ticks':{key:tick},'segments':{}}; tick IV is normalised to percent."""
    from google.protobuf.json_format import MessageToDict
    from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as pb

    msg = pb.FeedResponse()
    msg.ParseFromString(data)
    d = MessageToDict(msg)
    out = {"type": d.get("type", "initial_feed"), "ts": _f(d.get("currentTs")), "ticks": {}, "segments": {}}
    seg = (d.get("marketInfo") or {}).get("segmentStatus")
    if seg:
        out["segments"] = seg
    for key, f in (d.get("feeds") or {}).items():
        t = {}
        ltpc = None
        if "fullFeed" in f:
            ff = f["fullFeed"]
            m = ff.get("marketFF") or ff.get("indexFF") or {}
            ltpc = m.get("ltpc")
            depth = (m.get("marketLevel") or {}).get("bidAskQuote") or []
            if depth:
                t.update(bid=_f(depth[0].get("bidP")), ask=_f(depth[0].get("askP")), bidq=_f(depth[0].get("bidQ")), askq=_f(depth[0].get("askQ")))
            g = m.get("optionGreeks")
            t.update(oi=_f(m.get("oi")), vtt=_f(m.get("vtt")), atp=_f(m.get("atp")), iv=(_f(m.get("iv")) or 0) * 100 or None)
            for o in (m.get("marketOHLC") or {}).get("ohlc", []):
                if o.get("interval") in ("I1", "1minute"):
                    t["ohlc1m"] = {k: _f(o.get(k)) for k in ("open", "high", "low", "close")}
        elif "firstLevelWithGreeks" in f:
            m = f["firstLevelWithGreeks"]
            ltpc, g = m.get("ltpc"), m.get("optionGreeks")
            fd = m.get("firstDepth") or {}
            t.update(bid=_f(fd.get("bidP")), ask=_f(fd.get("askP")), bidq=_f(fd.get("bidQ")), askq=_f(fd.get("askQ")),
                     oi=_f(m.get("oi")), vtt=_f(m.get("vtt")), iv=(_f(m.get("iv")) or 0) * 100 or None)
        else:
            ltpc, g = f.get("ltpc"), None
        if g:
            t.update({k: _f(g.get(k)) for k in ("delta", "gamma", "theta", "vega", "rho")})
        if ltpc:
            t.update(ltp=_f(ltpc.get("ltp")), cp=_f(ltpc.get("cp")))
        out["ticks"][key] = t
    return out


class LiveFeed:
    def __init__(self, client, state):
        self.client, self.state = client, state
        self._desired, self._subscribed = set(), set()
        self._loop = None
        self._ws = None
        self._stop = False
        self._thread = threading.Thread(target=self._thread_main, name="upstox-feed", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True

    # called from the orchestrator thread
    def set_subscriptions(self, keys):
        self._desired = set(keys)
        if self._loop and self._ws:
            asyncio.run_coroutine_threadsafe(self._sync(), self._loop)

    # ------------------------------------------------------------------
    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._run())

    async def _run(self):
        import websockets
        backoff = 2
        while not self._stop:
            try:
                url = await self._loop.run_in_executor(None, self.client.feed_authorize_url)
                async with websockets.connect(url, max_size=None, ping_interval=None, open_timeout=15) as ws:
                    self._ws, self._subscribed = ws, set()
                    self.state.feed.update(connected=True, error=None)
                    backoff = 2
                    log.info("Upstox market feed connected")
                    await self._sync()
                    while not self._stop:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=45)
                        except asyncio.TimeoutError:
                            await asyncio.wait_for(await ws.ping(), timeout=10)   # idle (e.g. pre-open): verify link is alive
                            continue
                        if isinstance(raw, (bytes, bytearray)):
                            self._handle(bytes(raw))
            except Exception as e:  # reconnect with backoff; authorize URLs are single-use so we always re-authorize
                self.state.feed.update(connected=False, error=f"{type(e).__name__}: {e}"[:200])
                log.warning("feed error: %s", e)
            finally:
                self._ws = None
                self.state.feed["connected"] = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _handle(self, data):
        try:
            m = decode_feed(data)
        except Exception as e:
            log.warning("undecodable frame: %s", e)
            return
        st = self.state
        st.feed["last_msg"] = time.time()
        st.feed["msgs"] += 1
        if m["segments"]:
            st.segment_status = m["segments"]
        for key, t in m["ticks"].items():
            st.update_tick(key, t)

    async def _sync(self):
        ws = self._ws
        if not ws:
            return
        add, rem = sorted(self._desired - self._subscribed), sorted(self._subscribed - self._desired)
        for method, keys in (("unsub", rem), ("sub", add)):
            for i in range(0, len(keys), 100):
                chunk = keys[i:i + 100]
                req = {"guid": uuid.uuid4().hex[:20], "method": method, "data": {"mode": "full", "instrumentKeys": chunk}}
                if method == "unsub":
                    req["data"] = {"instrumentKeys": chunk}
                await ws.send(json.dumps(req).encode())   # Upstox requires a BINARY frame
        self._subscribed = set(self._desired)
        self.state.feed["subscribed"] = len(self._subscribed)
