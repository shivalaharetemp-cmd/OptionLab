import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.test import TestCase, Client, override_settings

from analytics import indicators as ind
from analytics.chain import compute_metrics, parse_chain, buildup, max_pain
from analytics.engine import SellingDisabled, assert_buy_only
from analytics.greeks import bs_greeks, bs_price, implied_vol, years_to_expiry
from analytics.models import Signal
from analytics.state import MarketState, IST
from analytics.tracker import Tracker, compute_stats
from upstox import registry
from upstox.client import UpstoxClient, UpstoxError, parse_candles, _qs


class GreeksTests(TestCase):
    def test_put_call_parity(self):
        S, K, T, v, r = 25000, 25000, 0.02, 0.14, 0.065
        c, p = bs_price(S, K, T, v, True, r), bs_price(S, K, T, v, False, r)
        self.assertAlmostEqual(c - p, S - K * pow(2.718281828459045, -r * T), places=4)

    def test_iv_roundtrip_and_greek_signs(self):
        px = bs_price(25000, 25100, 0.03, 0.17, True)
        self.assertAlmostEqual(implied_vol(px, 25000, 25100, 0.03, True), 0.17, places=4)
        g = bs_greeks(25000, 25000, 0.03, 0.15, True)
        self.assertTrue(0.45 < g["delta"] < 0.58 and g["gamma"] > 0 and g["theta"] < 0 and g["vega"] > 0)
        self.assertLess(bs_greeks(25000, 25000, 0.03, 0.15, False)["delta"], 0)

    def test_years_to_expiry_floor(self):
        now = datetime(2026, 10, 30, 16, 0, tzinfo=IST)
        self.assertGreater(years_to_expiry("2026-10-30", now), 0)


def api_rows(spot=100, strikes=(90, 100, 110)):
    rows = []
    for k in strikes:
        rows.append({"strike_price": k, "expiry": "2026-11-03", "underlying_spot_price": spot,
                     "call_options": {"instrument_key": f"C{k}", "market_data": {"ltp": 5, "oi": 1000 * (k - 80), "prev_oi": 900, "volume": 10, "close_price": 4, "bid_price": 4.9, "ask_price": 5.1},
                                      "option_greeks": {"delta": .5, "gamma": .01, "theta": -1, "vega": 2, "iv": 14, "pop": 40}},
                     "put_options": {"instrument_key": f"P{k}", "market_data": {"ltp": 4, "oi": 1000 * (120 - k), "prev_oi": 900, "volume": 10, "close_price": 5, "bid_price": 3.9, "ask_price": 4.1},
                                     "option_greeks": {"delta": -.5, "gamma": .01, "theta": -1, "vega": 2, "iv": 15, "pop": 40}}})
    return rows


class ChainTests(TestCase):
    def test_metrics(self):
        rows, spot = parse_chain(api_rows())
        m = compute_metrics(rows, spot, 50, 10)
        self.assertEqual(m["atm"], 100)
        self.assertAlmostEqual(m["pcr_oi"], (30000 + 20000 + 10000) / (10000 + 20000 + 30000))
        self.assertEqual(m["max_pain"], max_pain(rows))
        self.assertEqual(m["atm_iv"], 14.5)
        self.assertEqual(m["straddle"], 9)

    def test_buildup(self):
        s = {"ltp": 5, "close": 4, "oi": 10, "prev_oi": 5}
        self.assertEqual(buildup(s), "long_buildup")
        s["ltp"] = 3
        self.assertEqual(buildup(s), "short_buildup")


class IndicatorTests(TestCase):
    def test_basics(self):
        self.assertAlmostEqual(ind.ema([1, 1, 1], 3)[-1], 1)
        self.assertEqual(ind.rsi(list(range(30)), 14)[-1], 100.0)
        c = [{"t": datetime(2026, 10, 30, 9, 15 + i, tzinfo=IST), "o": i, "h": i + 1, "l": i - 1, "c": i, "v": 1} for i in range(10)]
        bars, cur = ind.resample(c, 5, datetime(2026, 10, 30, 9, 26, tzinfo=IST))
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["h"], 5)


class FeedDecodeTests(TestCase):
    def test_decode_option_and_index(self):
        from upstox.feed import decode_feed
        from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as pb
        m = pb.FeedResponse()
        m.type = pb.live_feed
        m.currentTs = 1740729389592
        f = m.feeds["NSE_FO|45450"]
        mf = f.fullFeed.marketFF
        mf.ltpc.ltp, mf.ltpc.cp = 213.75, 494.05
        q = mf.marketLevel.bidAskQuote.add()
        q.bidP, q.bidQ, q.askP, q.askQ = 213.45, 75, 213.9, 525
        mf.optionGreeks.delta, mf.optionGreeks.gamma, mf.optionGreeks.theta, mf.optionGreeks.vega = 0.4952, 0.0007, -8.4, 16.7
        mf.oi, mf.iv, mf.vtt = 210000, 0.1313, 779625
        ix = m.feeds["NSE_INDEX|Nifty 50"].fullFeed.indexFF
        ix.ltpc.ltp, ix.ltpc.cp = 25000.5, 24900
        out = decode_feed(m.SerializeToString())
        t = out["ticks"]["NSE_FO|45450"]
        self.assertEqual((t["ltp"], t["bid"], t["ask"], t["oi"]), (213.75, 213.45, 213.9, 210000))
        self.assertAlmostEqual(t["iv"], 13.13, places=2)          # fraction -> percent
        self.assertAlmostEqual(t["delta"], 0.4952)
        self.assertEqual(out["ticks"]["NSE_INDEX|Nifty 50"]["ltp"], 25000.5)


class BuyerOnlyTests(TestCase):
    def test_guard_and_model(self):
        assert_buy_only("BUY_CE"); assert_buy_only("BUY_PE")
        for bad in ("SELL_CE", "SELL_PE", "BUY", "WRITE_PE"):
            with self.assertRaises(SellingDisabled):
                assert_buy_only(bad)
        with self.assertRaises(ValueError):
            Signal(action="SELL_CE", created_at=datetime.now(IST), strategy="x", underlying="NIFTY", expiry="2026-11-03", instrument_key="k",
                   trading_symbol="t", strike=1, entry=1, sl=.5, t1=2, t2=3, confidence=70).save()

    def test_selling_flag_is_off(self):
        from django.conf import settings
        self.assertFalse(settings.ALLOW_SELLING)


class TrackerTests(TestCase):
    def mk(self, st, entry=100.0, sl=80.0):
        now = st.clock.now()
        s = Signal.objects.create(mode="live", created_at=now, strategy="T", underlying="NIFTY", expiry="2026-11-03", action="BUY_CE", instrument_key="K1",
                                  trading_symbol="x", strike=1, lot_size=75, entry=entry, sl=sl, t1=130, t2=150, confidence=80, peak=entry, trough=entry,
                                  context={"risk0": entry - sl, "sl0": sl})
        tr = Tracker(st)
        tr.register(s)
        return tr, s

    def tick(self, st, px):
        st.update_tick("K1", {"ltp": px, "bid": px})

    def test_stop_loss(self):
        st = MarketState(); tr, s = self.mk(st)
        with mock.patch.object(st.clock, "now", return_value=datetime(2026, 10, 30, 11, 0, tzinfo=IST)):
            self.tick(st, 79); tr.update()
        s.refresh_from_db()
        self.assertEqual((s.status, s.realized_r), ("SL", -1.05))

    def test_t1_then_trail_and_t2(self):
        st = MarketState(); tr, s = self.mk(st)
        t = datetime(2026, 10, 30, 11, 0, tzinfo=IST)
        with mock.patch.object(st.clock, "now", return_value=t):
            self.tick(st, 131); tr.update()
            s.refresh_from_db(); self.assertEqual(s.status, "T1_HIT"); self.assertGreaterEqual(s.sl, 100)
            self.tick(st, 151); tr.update()
        s.refresh_from_db()
        self.assertEqual(s.status, "T2")
        self.assertAlmostEqual(s.realized_r, 0.5 * 1.5 + 0.5 * 2.55, places=2)
        stats = compute_stats("live")
        self.assertEqual(stats["overall"]["n"], 1)

    def test_time_stop_and_eod(self):
        st = MarketState(); tr, s = self.mk(st)
        late = s.created_at.replace(hour=11, minute=0) + timedelta(minutes=50)
        with mock.patch.object(st.clock, "now", return_value=late):
            s.created_at = late - timedelta(minutes=50); s.save()
            self.tick(st, 101); tr.update()
        s.refresh_from_db(); self.assertEqual(s.status, "TIMEOUT")


class ClientTests(TestCase):
    def test_token_never_in_repr_and_qs_encoding(self):
        c = UpstoxClient("SECRETTOKEN")
        self.assertNotIn("SECRETTOKEN", repr(c))
        self.assertEqual(_qs({"instrument_key": "NSE_INDEX|Nifty 50", "x": None}), "instrument_key=NSE_INDEX%7CNifty%2050")
        self.assertIn("a,b", _qs({"k": "a,b"}))

    def test_get_unwraps_and_errors(self):
        sess = mock.Mock(); sess.headers = {}
        r = mock.Mock(status_code=200, headers={}); r.json.return_value = {"status": "success", "data": [1]}
        sess.get.return_value = r
        c = UpstoxClient("t", session=sess)
        self.assertEqual(c.get("/v2/option/chain", {"instrument_key": "NSE_INDEX|Nifty 50"}), [1])
        self.assertIn("NSE_INDEX%7CNifty%2050", sess.get.call_args[0][0])
        r.status_code = 401; r.json.return_value = {"status": "error", "errors": [{"message": "bad"}]}
        with self.assertRaises(UpstoxError) as cm:
            c.get("/v2/x")
        self.assertEqual(cm.exception.status, 401)

    def test_candles_sorted(self):
        out = parse_candles([["2026-10-30T09:16:00+05:30", 2, 3, 1, 2, 5, 0], ["2026-10-30T09:15:00+05:30", 1, 2, 1, 2, 5, 0]])
        self.assertLess(out[0]["t"], out[1]["t"])


class ExplorerTests(TestCase):
    def test_registry_is_get_only_paths_and_static_ip_gated(self):
        self.assertTrue(all(e["path"].startswith("/v") for e in registry.ENDPOINTS))
        vis = registry.visible(False)
        self.assertFalse(any(e["static_ip"] for e in vis))
        self.assertTrue(any(e["id"] == "option_chain" for e in vis))

    @override_settings(EXPLORER_ENABLED=True)
    def test_rejects_unknown_path(self):
        r = Client().post("/api/explorer/call", data=json.dumps({"path": "/v2/order/place"}), content_type="application/json")
        self.assertIn(r.status_code, (400, 403))
