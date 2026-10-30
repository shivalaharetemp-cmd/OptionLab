"""Black-Scholes pricing, Greeks and implied vol (European, index options). T in years."""
import math
from datetime import datetime, time, timedelta, timezone

RISK_FREE = 0.065
IST = timezone(timedelta(hours=5, minutes=30))
_SQ2 = math.sqrt(2.0)


def _cdf(x):
    return 0.5 * (1.0 + math.erf(x / _SQ2))


def _pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1d2(S, K, T, sigma, r):
    v = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / v
    return d1, d1 - v


def bs_price(S, K, T, sigma, is_call, r=RISK_FREE):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if is_call else max(K - S, 0.0)
    d1, d2 = _d1d2(S, K, T, sigma, r)
    if is_call:
        return S * _cdf(d1) - K * math.exp(-r * T) * _cdf(d2)
    return K * math.exp(-r * T) * _cdf(-d2) - S * _cdf(-d1)


def bs_greeks(S, K, T, sigma, is_call, r=RISK_FREE):
    """delta, gamma, theta (per calendar DAY, per unit), vega (per 1 vol-point, per unit)."""
    if T <= 0 or sigma <= 0:
        itm = (S > K) if is_call else (S < K)
        return {"delta": (1.0 if is_call else -1.0) if itm else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2 = _d1d2(S, K, T, sigma, r)
    pdf = _pdf(d1)
    gamma = pdf / (S * sigma * math.sqrt(T))
    vega = S * pdf * math.sqrt(T) / 100.0
    first = -(S * pdf * sigma) / (2 * math.sqrt(T))
    if is_call:
        theta = (first - r * K * math.exp(-r * T) * _cdf(d2)) / 365.0
        delta = _cdf(d1)
    else:
        theta = (first + r * K * math.exp(-r * T) * _cdf(-d2)) / 365.0
        delta = _cdf(d1) - 1.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def implied_vol(price, S, K, T, is_call, r=RISK_FREE, lo=0.01, hi=4.0):
    """Bisection IV (fraction). Returns None if the price is outside no-arbitrage bounds."""
    if T <= 0 or price <= 0:
        return None
    if price < bs_price(S, K, T, lo, is_call, r) - 1e-9 or price > bs_price(S, K, T, hi, is_call, r):
        return None
    for _ in range(70):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, is_call, r) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def years_to_expiry(expiry: str, now: datetime) -> float:
    """Expiry date 'YYYY-MM-DD' -> years remaining to 15:30 IST that day (floored at 1 minute)."""
    d = datetime.strptime(expiry, "%Y-%m-%d").date()
    end = datetime.combine(d, time(15, 30), IST)
    secs = max((end - now).total_seconds(), 60.0)
    return secs / (365.0 * 86400.0)
