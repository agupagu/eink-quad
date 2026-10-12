"""CoinGecko crypto panel regression tests; all API payloads are explicit fixtures.

cg-markets.json / cg-global.json are byte-for-byte live /coins/markets and
/global responses captured 2026-10-12 (parent machine verified both 200).
"""
from __future__ import annotations

import copy
import json
import sys
import time
from unittest.mock import Mock, patch
from PIL import Image, ImageChops
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
import sources as S


def markets_payload():
    with open(HERE / "cg-markets.json") as f:
        return json.load(f)


def global_payload():
    with open(HERE / "cg-global.json") as f:
        return json.load(f)


def coin(id="bitcoin", symbol="btc", name="Bitcoin", price=82982.0, chg=-0.09952, spark=None):
    entry = {"id": id, "symbol": symbol, "name": name, "current_price": price,
             "price_change_percentage_24h": chg,
             "price_change_percentage_24h_in_currency": chg,
             "sparkline_in_7d": {"price": spark if spark is not None else [80000 + i * 17.75 for i in range(168)]}}
    return entry


class CryptoTests(unittest.TestCase):
    def test_parse_live_fixture(self):
        cr = S.parse_crypto(markets_payload(), global_payload())
        self.assertEqual(set(cr.coins), {"bitcoin", "ethereum", "solana"})
        btc = cr.coins["bitcoin"]
        self.assertEqual(btc.symbol, "btc")
        self.assertEqual(btc.price, 82982)
        self.assertAlmostEqual(btc.chg_pct, -0.09952)
        self.assertEqual(len(btc.spark), 168)          # 7d hourly
        self.assertAlmostEqual(cr.btc_dominance, 59.01, places=2)
        self.assertIsNotNone(cr.market_cap_chg_24h)
        self.assertTrue(cr.fetched)
        self.assertIsNone(cr.error)

    def test_requested_but_missing_coins_stay_absent_not_zero(self):
        # only bitcoin came back; ethereum/solana must not appear at all
        cr = S.parse_crypto([coin()], global_payload())
        self.assertEqual(list(cr.coins), ["bitcoin"])
        # junk entries are skipped, never coerced
        junk = [42, None, coin(id="", price=1), coin(id="nan-price", price="8"),
                coin(id="neg", price=-1), coin(id="bool", price=True)]
        solana = coin(id="solana", symbol="sol", name="Solana")
        solana["price_change_percentage_24h_in_currency"] = None
        solana["price_change_percentage_24h"] = None
        solana["sparkline_in_7d"] = None
        cr = S.parse_crypto(junk + [solana], global_payload())
        self.assertEqual(list(cr.coins), ["solana"])
        self.assertIsNone(cr.coins["solana"].chg_pct)   # null change stays None
        self.assertEqual(cr.coins["solana"].spark, [])  # missing sparkline stays empty

    def test_error_payloads_raise(self):
        good_global = global_payload()
        for markets in (None, [], {}, "x", [{}], [{"id": "bitcoin", "current_price": None}]):
            with self.subTest(markets=markets), self.assertRaises(ValueError):
                S.parse_crypto(markets, good_global)
        for glob in (None, {}, {"data": None}, {"data": {"market_cap_percentage": None}}):
            with self.subTest(glob=glob):
                # a global without dominance parses (dominance None) but one
                # without a data object must raise
                if glob == {"data": {"market_cap_percentage": None}}:
                    cr = S.parse_crypto([coin()], glob)
                    self.assertIsNone(cr.btc_dominance)
                    self.assertIsNone(cr.market_cap_chg_24h)
                    continue
                with self.assertRaises(ValueError):
                    S.parse_crypto([coin()], glob)

    def test_failed_refresh_keeps_last_good_and_first_failure_is_empty(self):
        with patch.object(S, "_crypto", S.Cached(600)), \
             patch.object(S, "_fetch_crypto", return_value=S.parse_crypto(markets_payload(), global_payload())) as fetch:
            first = S.crypto({"coins": ["bitcoin", "ethereum", "solana"]}, force=True)
            self.assertEqual(set(first.coins), {"bitcoin", "ethereum", "solana"})
            self.assertAlmostEqual(first.btc_dominance, 59.01, places=2)
            fetch.side_effect = RuntimeError("429")
            failed = S.crypto({"coins": ["bitcoin", "ethereum", "solana"]}, force=True)
            self.assertEqual(set(failed.coins), set(first.coins))
            self.assertEqual(failed.coins["bitcoin"].price, first.coins["bitcoin"].price)
            self.assertAlmostEqual(failed.btc_dominance, first.btc_dominance)
            self.assertIsNotNone(failed.error)
            fetch.side_effect = None
            self.assertIsNone(S.crypto({"coins": ["bitcoin", "ethereum", "solana"]}, force=True).error)
        with patch.object(S, "_crypto", S.Cached(600)), \
             patch.object(S, "_fetch_crypto", side_effect=RuntimeError("down")):
            empty = S.crypto({"coins": ["bitcoin"]}, force=True)
            self.assertEqual(empty.coins, {})
            self.assertIsNone(empty.btc_dominance)
            self.assertTrue(empty.error)

    def test_retry_once_on_429_then_success(self):
        ok_markets = Mock(status_code=200)
        ok_markets.json.return_value = markets_payload()
        ok_global = Mock(status_code=200)
        ok_global.json.return_value = global_payload()
        limited = Mock(status_code=429)
        with patch.object(S, "CG_RETRY_S", 0), \
             patch.object(S.requests, "get", side_effect=[limited, ok_markets, ok_global]) as get:
            cr = S._fetch_crypto(["bitcoin", "ethereum", "solana"])
        self.assertEqual(get.call_count, 3)             # markets retried once, then /global
        self.assertEqual(get.call_args_list[0][0][0], S.CG_MARKETS_URL)
        self.assertEqual(get.call_args_list[0][1]["params"],
                         {"vs_currency": "usd", "ids": "bitcoin,ethereum,solana",
                          "sparkline": "true", "price_change_percentage": "24h"})
        self.assertEqual(get.call_args_list[-1][0][0], S.CG_GLOBAL_URL)
        self.assertEqual(get.call_args_list[0][1]["timeout"], 10)
        self.assertEqual(set(cr.coins), {"bitcoin", "ethereum", "solana"})
        limited.raise_for_status.side_effect = S.requests.exceptions.HTTPError("429")
        with patch.object(S, "CG_RETRY_S", 0), \
             patch.object(S.requests, "get", return_value=limited) as get:
            with self.assertRaises(Exception):
                S._fetch_crypto(["bitcoin"])
            self.assertEqual(get.call_count, 2)         # exactly one retry, then gives up

    def test_new_coin_refetches_before_ttl_and_no_location_gate(self):
        parsed = S.parse_crypto(markets_payload(), global_payload())
        london = {"lat": 51.5, "lon": -0.12, "city": "London", "coins": ["bitcoin", "ethereum", "solana"]}
        with patch.object(S, "_crypto", S.Cached(600)), \
             patch.object(S, "_fetch_crypto", return_value=parsed) as fetch:
            S.crypto(london)                            # crypto is global: no Singapore gate
            self.assertEqual(fetch.call_count, 1)
            S.crypto(london)
            self.assertEqual(fetch.call_count, 1)       # within ttl, same coins: no refetch
            S.crypto({**london, "coins": ["bitcoin", "ethereum", "solana", "dogecoin"]})
            self.assertEqual(fetch.call_count, 2)       # a new coin forces a refresh

    def test_counts(self):
        cr = S.parse_crypto([coin(id="a", chg=1.0), coin(id="b", symbol="b", name="B", chg=-2.0),
                             coin(id="c", symbol="c", name="C", chg=None),
                             coin(id="d", symbol="d", name="D", chg=0.0)], global_payload())
        self.assertEqual(cr.counts(), (1, 1))

    def test_panel_and_detail_render_sizes_and_content(self):
        import render as R
        cr = S.parse_crypto(markets_payload(), global_payload())
        ctx = R.Ctx(now=time.time(), cfg={"lat": 51.5, "lon": -0.12,
                                          "coins": ["bitcoin", "ethereum", "solana"]}, crypto=cr)
        seen = []
        original = R.text

        def record(d, xy, s, f, **kwargs):
            seen.append(s)
            return original(d, xy, s, f, **kwargs)
        with patch.object(R, "text", side_effect=record), \
             patch.object(S.requests, "get", side_effect=AssertionError("render fetched")):
            compact = R.panel_crypto(ctx, (400, 207))
            full = R.detail_crypto(ctx, (800, 414))
            page = R.compose(R.Ctx(now=time.time(), cfg=ctx.cfg, crypto=cr, page="crypto"))
        self.assertEqual(compact.size, (400, 207))
        self.assertEqual(full.size, (800, 414))
        self.assertEqual(page.size, (800, 480))
        for label in ("CRYPTO · COINGECKO", "BTC", "82,982", "BTC 59.0%"):
            self.assertIn(label, seen)
        self.assertTrue(any("mcap 24h" in s for s in seen))
        self.assertTrue(any(s.startswith("BTC DOMINANCE") for s in seen))
        for sym in ("BTC", "ETH", "SOL"):
            self.assertIn(sym, seen)
        # unavailable states render in bounds too
        for bad in (None, S.Crypto(error="down"), S.Crypto()):
            with patch.object(S.requests, "get", side_effect=AssertionError("render fetched")):
                self.assertEqual(R.panel_crypto(R.Ctx(now=1, cfg={}, crypto=bad), (400, 207)).size, (400, 207))
                self.assertEqual(R.detail_crypto(R.Ctx(now=1, cfg={}, crypto=bad), (800, 414)).size, (800, 414))

    def test_changed_crypto_only_moves_the_bottom_right_quadrant(self):
        import render as R
        cr = S.parse_crypto(markets_payload(), global_payload())
        ctx = R.Ctx(now=time.time(), cfg={"lat": 51.5, "lon": -0.12}, crypto=cr)
        # other three slots prerendered blank: only the crypto slot can differ
        panels = {key: Image.new("L", (400, 207), 255) for key in ("weather", "news", "sports")}
        baseline = R.compose_quad(ctx, panels)
        moved = S.parse_crypto(markets_payload(), global_payload())
        for c in moved.coins.values():
            c.price *= 1.1
            c.chg_pct = 5.0
        moved.btc_dominance = 61.0
        ctx.crypto = moved
        changed = R.compose_quad(ctx, panels)
        bbox = ImageChops.difference(baseline, changed).getbbox()
        self.assertIsNotNone(bbox)
        self.assertGreaterEqual(bbox[0], R.CELL_W - 1)   # bottom-right slot only
        self.assertGreaterEqual(bbox[1], R.GRID_Y + R.CELL_H)
        self.assertLessEqual(bbox[2], R.W)
        self.assertLessEqual(bbox[3], R.H)

    def test_pages_and_slots_consistency(self):
        import render as R
        import app
        self.assertEqual(app.PAGES, ["quad", "weather", "news", "sports", "crypto", "sky"])
        self.assertEqual(set(app.DEFAULTS["slots"]), set(R.PANELS))
        self.assertEqual(len(R.PANELS), 4)                       # one panel per quad slot
        self.assertEqual(sorted(app.DEFAULTS["slots"]), sorted(R.PANELS))
        self.assertEqual(app.DEFAULTS["coins"], ["bitcoin", "ethereum", "solana"])
        self.assertEqual(S.DEFAULT_COINS, app.DEFAULTS["coins"])
        self.assertIn("crypto", R.DETAILS)
        self.assertIn("sky", R.DETAILS)                           # sky keeps its own page
        self.assertEqual(set(R.DETAILS), set(app.PAGES[1:]))
        self.assertNotIn("sky", R.PANELS)                         # sky no longer a quad panel


if __name__ == "__main__":
    unittest.main()
