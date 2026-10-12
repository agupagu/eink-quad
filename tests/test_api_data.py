"""Regression checks for the read-only /api/tablet/data JSON endpoint."""
import datetime
import json
import sys
import time
import unittest
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import sources as S
import render as R
import skyevents


NOW = time.time()


def _snap():
    wx = S.Weather(temp=30.5, feels=36.2, code=2, is_day=True, humidity=73,
                   wind=8.5, wind_dir=180.0, gust=14.0,
                   days=[S.Day(date="2026-10-12", hi=32.0, lo=26.0, code=2, pop=40,
                               sunrise="6:50 AM", sunset="6:45 PM")],
                   hourly=[(NOW - 3600 + i * 3600, 30.0 + i * 0.1, i * 2, 2)
                           for i in range(30)],
                   rain_note="Rain starting around 4 PM")           # psi stays None
    psi = S.Psi(values={"north": 55, "south": 60},
                timestamp=datetime.datetime.now(datetime.timezone.utc),
                fetched=NOW, error=None)
    quote = S.Quote(symbol="^GSPC", name="S&P 500", price=4000.0, prev=3980.1,
                    spark=[float(i) for i in range(200)], state="open", at=NOW)
    heads = [S.Headline(title=f"Headline number {i}", section="World",
                        published=NOW - i * 60) for i in range(10)]
    crypto = S.Crypto(
        coins={"bitcoin": S.Coin(id="bitcoin", symbol="btc", name="Bitcoin",
                                 price=50000.0, chg_pct=2.5,
                                 spark=[float(i) for i in range(168)]),
               "ethereum": S.Coin(id="ethereum", symbol="eth", name="Ethereum",
                                  price=1800.0, chg_pct=None, spark=[])},
        btc_dominance=54.3, market_cap_chg_24h=1.2, fetched=NOW, error=None)
    sighting = R.Sighting(callsign="SIA23", airline="Singapore Airlines",
                          origin="SIN", dest="LHR", origin_city="Singapore",
                          dest_city="London", alt_ft=37000, speed_kt=490.0,
                          dist_nm=12.3, elev_deg=45.0, seen_at=NOW,
                          flight_no="SQ 23", aircraft="Boeing 777", bearing=285.0,
                          progress=0.42, eta_min=700)
    event = skyevents.Event(kind="iss", start=NOW + 3600, end=NOW + 5400,
                            title="Space Station", subtitle="passes overhead",
                            bearing=90.0, where="E", height="overhead",
                            max_elev=75.0, confidence="confirmed", detail="")
    return {"weather": wx, "psi": psi, "quotes": [quote], "heads": heads,
            "crypto": crypto, "featured": sighting, "recent": [sighting],
            "day_count": 12, "event": event, "upcoming": [event]}


class DataEndpointTests(unittest.TestCase):
    def test_gate_payloads_and_conditional(self):
        import app as A
        with patch.object(A, "TABLET_TOKEN", "local-tablet"):
            server = A.Server(("127.0.0.1", 0), A.H)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with patch.dict(A.board.snap, _snap()), \
                     patch.dict(A.cfg, {"coins": ["bitcoin", "ethereum", "solana"]}):
                    # -- gate: no token -> 404
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(base + "/api/tablet/data?page=weather", timeout=5)
                    self.assertEqual(rejected.exception.code, 404)
                    rejected.exception.close()
                    with patch.object(S.requests, "get",
                                      side_effect=AssertionError("fetch under lock")):
                        tok = "&k=local-tablet"
                        # -- invalid page -> 400 (bogus and missing alike)
                        for bad in ("page=bogus&k=local-tablet", "k=local-tablet"):
                            with self.assertRaises(urllib.error.HTTPError) as bad_page:
                                urllib.request.urlopen(base + f"/api/tablet/data?{bad}", timeout=5)
                            self.assertEqual(bad_page.exception.code, 400)
                            bad_page.exception.close()
                        # -- weather
                        with urllib.request.urlopen(base + f"/api/tablet/data?page=weather{tok}", timeout=5) as r:
                            self.assertEqual(set(w := json.load(r)),
                                             {"temp", "feels", "code", "is_day", "humidity",
                                              "wind", "wind_dir", "gust", "days", "hourly",
                                              "rain_note", "psi"})
                            self.assertEqual(w["temp"], 30.5)
                            self.assertEqual(w["rain_note"], "Rain starting around 4 PM")
                            self.assertEqual(set(w["days"][0]),
                                             {"date", "hi", "lo", "code", "pop", "sunrise", "sunset"})
                            # hourly trimmed to next 24h; the request lands ms
                            # after NOW so the NOW-1h point slides out
                            self.assertEqual(len(w["hourly"]), 25)
                            self.assertEqual([pt[0] for pt in w["hourly"]],
                                             [NOW - 3600 + i * 3600 for i in range(1, 26)])
                            # psi falls back to the board snapshot (weather.psi is None)
                            self.assertEqual(w["psi"]["values"], {"north": 55, "south": 60})
                            self.assertIn("+00:00", w["psi"]["timestamp_iso"])
                            self.assertFalse(w["psi"]["stale"])
                            self.assertIsNone(w["psi"]["error"])
                        # -- news
                        with urllib.request.urlopen(base + f"/api/tablet/data?page=news{tok}", timeout=5) as r:
                            n = json.load(r)
                            self.assertEqual(set(n), {"quotes", "heads", "cycle"})
                            self.assertEqual(n["cycle"], A.board.cycle)
                            self.assertEqual(n["heads"], [f"Headline number {i}" for i in range(10)])
                            self.assertEqual(set(n["quotes"][0]),
                                             {"symbol", "name", "price", "prev", "chg_pct",
                                              "spark", "state"})
                            self.assertEqual(len(n["quotes"][0]["spark"]), 40)   # last 40 only
                            self.assertAlmostEqual(n["quotes"][0]["chg_pct"], 4000.0 / 3980.1 * 100 - 100)
                        # -- crypto
                        with urllib.request.urlopen(base + f"/api/tablet/data?page=crypto{tok}", timeout=5) as r:
                            c = json.load(r)
                            self.assertEqual(set(c), {"coins", "btc_dominance", "mcap24",
                                                      "updated", "error"})
                            self.assertEqual([coin["id"] for coin in c["coins"]],
                                             ["bitcoin", "ethereum"])   # cfg order first
                            self.assertEqual(set(c["coins"][0]),
                                             {"id", "symbol", "name", "price", "chg24", "spark"})
                            self.assertEqual(len(c["coins"][0]["spark"]), 84)   # 168 -> stride 2
                            self.assertEqual(c["coins"][0]["spark"][0], 0.0)
                            self.assertEqual(c["coins"][0]["spark"][-1], 166.0)
                            self.assertIsNone(c["coins"][1]["chg24"])          # null stays null
                            self.assertEqual(c["btc_dominance"], 54.3)
                            self.assertEqual(c["mcap24"], 1.2)
                            self.assertEqual(c["updated"], NOW)
                            self.assertIsNone(c["error"])
                        # -- sky
                        with urllib.request.urlopen(base + f"/api/tablet/data?page=sky{tok}", timeout=5) as r:
                            k = json.load(r)
                            self.assertEqual(set(k), {"featured", "recent_count", "day_count",
                                                      "event", "upcoming"})
                            self.assertEqual(set(k["featured"]),
                                             {"callsign", "airline", "flight_no", "origin", "dest",
                                              "origin_city", "dest_city", "alt_ft", "speed_kt",
                                              "aircraft", "bearing", "elev_deg", "progress",
                                              "eta_min"})
                            self.assertEqual(k["featured"]["flight_no"], "SQ 23")
                            self.assertEqual(k["featured"]["progress"], 0.42)
                            self.assertEqual(k["recent_count"], 1)
                            self.assertEqual(k["day_count"], 12)
                            self.assertEqual(k["event"]["kind"], "iss")
                            self.assertEqual(len(k["upcoming"]), 1)
                        # -- conditional v: match -> 204, mismatch -> 200
                        with urllib.request.urlopen(base + f"/api/tablet/data?page=sky{tok}", timeout=5) as r:
                            version = r.headers["X-Version"]
                        with urllib.request.urlopen(base + f"/api/tablet/data?page=sky{tok}&v={version}", timeout=5) as r:
                            self.assertEqual(r.status, 204)
                            self.assertEqual(r.headers["X-Version"], version)
                            self.assertEqual(r.read(), b"")
                        with urllib.request.urlopen(base + f"/api/tablet/data?page=sky{tok}&v={int(version) - 1}", timeout=5) as r:
                            self.assertEqual(r.status, 200)
                            self.assertEqual(r.headers["X-Version"], version)
            finally:
                server.shutdown()
                server.server_close()

    def test_empty_snapshots_never_500(self):
        import app as A
        with patch.object(A, "TABLET_TOKEN", "local-tablet"), \
             patch.dict(A.board.snap, {}, clear=True):
            for page in ("weather", "news", "crypto", "sky"):
                with self.subTest(page=page):
                    payload = A.tablet_data(page)
                    self.assertIsInstance(payload, dict)


if __name__ == "__main__":
    unittest.main()
