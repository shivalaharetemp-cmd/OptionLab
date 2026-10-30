# OptionLab — options analytics + buyer-only signals (Django, Upstox Analytics Token)

Live option chain, Greeks, OI / PCR / max-pain / IV analytics, five option-**buying** strategies, and a paper tracker that
measures whether those strategies actually make money. **Selling is disabled**: the engine can only emit `BUY_CE` / `BUY_PE`
(`ALLOW_SELLING=False` in settings, a guard in `analytics/engine.py`, and `Signal.save()` refuses anything else; tests cover it).
It never places orders — the Analytics Token is read-only by design.

## Run it
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # put your token in UPSTOX_ANALYTICS_TOKEN (never commit / paste it anywhere)
python manage.py migrate
python manage.py runserver      # or: daphne -p 8000 optionlab.asgi:application
```
Open http://localhost:8000. **No token? It starts in DEMO mode** (a simulated session on a replayed clock, `DEMO_SPEED`x) so you can
see every screen and signal flow at any hour. Demo data is not market data. Run **one** server process (state is in memory).

First live run checklist: `/api/health` should show `feed.connected: true` during market hours; the **API explorer** page lets you hit
any endpoint to confirm your token works. If a chain call errors for FINNIFTY/MIDCPNIFTY/SENSEX, correct the instrument key in
`upstox/instruments.py` (only NIFTY / BANKNIFTY keys are from the Upstox docs).

## API coverage (analytics token = read-only GETs)
Everything in `upstox/registry.py` (extracted from the official SDK): option contracts + chain, market quotes (LTP/OHLC/full/**option-greek**),
historical + intraday candles (V3), OI / change-OI / max-pain / PCR, FII / DII, smartlists, holidays / timings / status, expired instruments
(for building a real options backtest), instrument search, brokerage charges, news, fundamentals, IPOs, and the **V3 market-data websocket**.
Account / portfolio / orders-read endpoints need a whitelisted static IP: set `UPSTOX_STATIC_IP_WHITELISTED=1` to unlock them in the Explorer.
The engine itself uses: contracts, chain (every `CHAIN_POLL_SECONDS`), intraday 1-min candles, and the websocket (ATM ± `ATM_WINDOW_STRIKES`
strikes, CE+PE, `full` mode = LTP + depth + OI + Greeks, plus the index and India VIX).

## Websocket
`upstox/feed.py`: calls `/v3/feed/market-data-feed/authorize` (single-use URL), connects, sends binary `sub` requests, decodes protobuf with the
decoder bundled in `upstox-python-sdk`, auto-reconnects with a fresh URL, and re-subscribes as ATM moves. Browser gets 1 Hz pushes over
Django Channels (`/ws/live/`). Limits respected: 1 connection, ≪2000 keys.

## Strategy rules (all long options; thresholds in `settings.SIGNALS`)
| Strategy | Idea |
|---|---|
| `ORB_OI` | 5-min close breaks the 15-min opening range, confirmed by ATM put/call ΔOI flow, PCR trend and non-spiking IV |
| `PULLBACK` | 5-min EMA8>EMA21 trend, pullback to EMA8 holds, RSI not stretched, bullish/bearish re-entry bar |
| `OI_WALLS` | rejection at the biggest put/call OI wall with OI still building — or a wall *breaking* while its OI unwinds |
| `PREMIUM_FLOW` | option premium breaks its 9-min high on ≥1.8x volume with OI rising (long build-up), spot trend agrees |
| `IV_SQUEEZE` | 5-min Bollinger squeeze (bottom quartile of today's bandwidth) + low ATM-IV percentile, buy the breakout direction |

Every setup then goes through: contract selection (|Δ| 0.38–0.65, spread ≤3%, min OI/volume, ATM±3), a **risk plan**
(SL from spot-invalidation × delta/gamma, clamped 15–35% of premium; T1 1.5R, T2 2.5R; theta-bleed and "is T1 realistic vs ATR" filters),
a 0–100 **confidence** (pattern + trend + OI + PCR + IV regime + liquidity + timing, default threshold 65) and **gates** (09:30–14:45 only, live websocket
freshness, 20-min cooldown, ≤3 open, ≤8/day/underlying, daily stop at −3R, no opposite-side overlap).

## Be honest about "profitable"
No rule set is guaranteed profitable, and these were not backtested on your data. The thresholds are sensible defaults, not optimised
(optimising on a short sample just overfits). The **Signal journal** is the point: it paper-trades every signal on live ticks with spread
+ slippage and reports win rate, expectancy (R), profit factor and drawdown *per strategy*. Trust a strategy only after ~50+ closed
trades with positive expectancy, then tune/disable the weak ones. Option buying also pays theta every minute — position-size so a full
stop-out is a small fraction of capital. This is analytics software, not investment advice.

## Layout
`upstox/` client, registry, feed, demo · `analytics/` greeks, chain metrics, indicators, strategies, engine, tracker, orchestrator ·
`dashboard/` views, Channels consumer, templates · `tests/` (`python manage.py test tests`).
Scaling beyond one process: move `MarketState` to Redis and run the orchestrator as its own worker (`channels_redis`).
