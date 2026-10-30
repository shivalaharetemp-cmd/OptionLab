"""Thin, safe REST client for Upstox (analytics token = read-only GET APIs)."""
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


class UpstoxError(Exception):
    def __init__(self, status, message, path=""):
        super().__init__(f"[{status}] {path}: {message}")
        self.status, self.message, self.path = status, message, path


class RateLimiter:
    """Sliding windows below Upstox's documented limits (50/s, 500/min, 2000/30min for standard APIs)."""

    def __init__(self, windows=((1, 40), (60, 420), (1800, 1800))):
        self.windows = [(span, cap, deque()) for span, cap in windows]
        self.lock = threading.Lock()

    def acquire(self):
        while True:
            with self.lock:
                now = time.monotonic()
                wait = 0.0
                for span, cap, q in self.windows:
                    while q and now - q[0] > span:
                        q.popleft()
                    if len(q) >= cap:
                        wait = max(wait, span - (now - q[0]))
                if wait <= 0:
                    for _, _, q in self.windows:
                        q.append(now)
                    return
            time.sleep(min(wait, 1.0))


def _qs(params: dict) -> str:
    # %20 (not '+') for spaces: instrument keys look like "NSE_INDEX|Nifty 50". Commas stay literal for key lists.
    return "&".join(f"{quote(str(k), safe='')}={quote(str(v), safe=',')}" for k, v in params.items() if v not in (None, ""))


def parse_candles(raw):
    """[[iso_ts, o, h, l, c, vol, oi], ...] (newest first) -> ascending list of dicts."""
    out = []
    for c in raw or []:
        try:
            t = datetime.fromisoformat(c[0]).astimezone(IST)
        except Exception:
            continue
        out.append({"t": t, "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4]),
                    "v": float(c[5] or 0), "oi": float(c[6] or 0) if len(c) > 6 else 0.0})
    out.sort(key=lambda x: x["t"])
    return out


class UpstoxClient:
    BASE = "https://api.upstox.com"

    def __init__(self, token: str, timeout=12, limiter=None, session=None):
        if not token:
            raise ValueError("Upstox analytics token is empty")
        self._token = token
        self.timeout = timeout
        self.limiter = limiter or RateLimiter()
        self.s = session or requests.Session()
        self.s.headers.update({"Accept": "application/json", "Authorization": f"Bearer {token}"})

    def __repr__(self):  # never leak the token via logs / tracebacks
        return "<UpstoxClient token=***>"

    # ------------------------------------------------------------------ core
    def get(self, path: str, params: dict | None = None, path_args: dict | None = None, raw=False):
        if path_args:
            path = path.format(**{k: quote(str(v), safe="") for k, v in path_args.items()})
        url = self.BASE + path
        if params:
            url += "?" + _qs(params)
        last = None
        for attempt in range(4):
            self.limiter.acquire()
            try:
                r = self.s.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                last = UpstoxError(0, f"network error: {type(e).__name__}", path)
                time.sleep(0.5 * 2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = UpstoxError(r.status_code, r.text[:200], path)
                time.sleep(float(r.headers.get("Retry-After", 0.7 * 2 ** attempt)))
                continue
            try:
                body = r.json()
            except ValueError:
                raise UpstoxError(r.status_code, f"non-JSON response: {r.text[:120]}", path)
            if r.status_code == 401:
                raise UpstoxError(401, "token rejected — expired, revoked or wrong (generate a new Analytics Token)", path)
            if r.status_code >= 400 or body.get("status") == "error":
                errs = body.get("errors") or [{}]
                msg = errs[0].get("message") or body.get("message") or r.text[:200]
                raise UpstoxError(r.status_code, msg, path)
            return body if raw else body.get("data", body)
        raise last or UpstoxError(0, "request failed", path)

    # ------------------------------------------------------------------ option chain
    def option_contracts(self, key, expiry=None):
        return self.get("/v2/option/contract", {"instrument_key": key, "expiry_date": expiry}) or []

    def option_chain(self, key, expiry):
        return self.get("/v2/option/chain", {"instrument_key": key, "expiry_date": expiry}) or []

    # ------------------------------------------------------------------ quotes
    def ltp(self, keys):
        return self.get("/v3/market-quote/ltp", {"instrument_key": ",".join(keys)})

    def full_quotes(self, keys):
        return self.get("/v3/market-quote/quotes", {"instrument_key": ",".join(keys)})

    def option_greeks(self, keys):
        out = {}
        for i in range(0, len(keys), 50):
            out.update(self.get("/v3/market-quote/option-greek", {"instrument_key": ",".join(keys[i:i + 50])}) or {})
        return out

    # ------------------------------------------------------------------ candles
    def intraday_candles(self, key, unit="minutes", interval=1):
        d = self.get("/v3/historical-candle/intraday/{k}/{u}/{i}", path_args={"k": key, "u": unit, "i": interval})
        return parse_candles((d or {}).get("candles"))

    def historical_candles(self, key, unit, interval, to_date, from_date=None):
        p = "/v3/historical-candle/{k}/{u}/{i}/{to}" + ("/{frm}" if from_date else "")
        a = {"k": key, "u": unit, "i": interval, "to": to_date}
        if from_date:
            a["frm"] = from_date
        return parse_candles((self.get(p, path_args=a) or {}).get("candles"))

    # ------------------------------------------------------------------ market information
    def oi(self, key, expiry, date):
        return self.get("/v2/market/oi", {"instrument_key": key, "expiry": expiry, "date": date})

    def change_oi(self, key, expiry, date, interval=60):
        return self.get("/v2/market/change-oi", {"instrument_key": key, "expiry": expiry, "date": date, "interval": interval})

    def max_pain(self, key, expiry, date, bucket=60):
        return self.get("/v2/market/max-pain", {"instrument_key": key, "expiry": expiry, "date": date, "bucket_interval": bucket})

    def pcr(self, key, expiry, date, bucket=60):
        return self.get("/v2/market/pcr", {"instrument_key": key, "expiry": expiry, "date": date, "bucket_interval": bucket})

    def exchange_status(self, exchange="NSE"):
        return self.get("/v2/market/status/{e}", path_args={"e": exchange})

    # ------------------------------------------------------------------ account (needs a whitelisted static IP)
    def profile(self):
        return self.get("/v2/user/profile")

    def funds(self):
        return self.get("/v3/user/get-funds-and-margin")

    # ------------------------------------------------------------------ websocket
    def feed_authorize_url(self) -> str:
        d = self.get("/v3/feed/market-data-feed/authorize")
        return d["authorized_redirect_uri"]
