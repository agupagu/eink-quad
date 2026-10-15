"""Flight lookup tests. All payloads below are synthetic test-only fixtures."""
import sys
import unittest
import copy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import flighttrack as F


class NormalizeTests(unittest.TestCase):
    def test_normalization_per_query(self):
        cases = {"SQ321": "SQ321", "sq 321": "SQ321", " 6 e 1 2 3 4 ": "6E1234",
                 "SIA12AB": "SIA12AB", "sia ab1": "SIAAB1", "123": "123",
                 "BA1234A": "BA1234A", " ": ""}
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(F.normalize_flight(query), expected)

    def test_normalization_and_validation(self):
        for raw, expected in [(" sq 321 ", "SQ321"), ("6e1234", "6E1234"),
                              ("SIA12AB", "SIA12AB"), ("BA1234A", "BA1234A"), ("", "")]:
            self.assertEqual(F.normalize_flight(raw), expected)
        for raw in [None, 321, "SQ-321", "SQ/321", "SQ321?", "SQ\n321", "SQ\t321",
                    "SIAABCDE", "SQ12345", "ABC12345", "A1", "ß123", "ＡＢ123", "SQ\x00321",
                    "<script>", "A" * 100]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                F.normalize_flight(raw)


NOW = 1800000000.0
ROUTE = {"response": {"flightroute": {
    "callsign": "SQ321", "callsign_iata": "SQ321", "callsign_icao": "SIA321",
    "airline": {"name": "Singapore Airlines", "icao": "SIA", "iata": "SQ"},
    "origin": {"iata_code": "LHR", "municipality": "London", "latitude": 51.47, "longitude": -0.45},
    "destination": {"iata_code": "SIN", "municipality": "Singapore", "latitude": 1.36, "longitude": 103.99}}}}
AC = {"flight": "SIA321  ", "hex": "abc123", "lat": 1.5, "lon": 103.8,
      "seen_pos": 10, "alt_baro": 10000, "gs": 350, "t": "B77W"}


def feed(*aircraft, **extra):
    return dict({"ac": list(aircraft), "msg": "No error", "now": NOW * 1000,
                 "total": len(aircraft)}, **extra)


def response(payload=None, status=200):
    r = Mock(status_code=status)
    r.json.return_value = copy.deepcopy(payload)
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(str(status), response=r)
    return r


class TrackTests(unittest.TestCase):
    def setUp(self):
        F._cache.clear()
        self.clock = patch.object(F.time, "time", return_value=NOW).start()
        self.mono = patch.object(F.time, "monotonic", return_value=1000.0).start()
        self.get = patch.object(F.requests, "get").start()
        self.get.side_effect = [response(ROUTE), response(feed(AC))]
        self.addCleanup(patch.stopall)

    def lookup(self, query="sq 321", lat=37.77, lon=-122.42):
        return F.track_flight(query, lat, lon)

    def test_iata_authoritative_mapping_worldwide_geometry(self):
        result = self.lookup()
        self.assertEqual((result.query, result.callsign, result.status), ("SQ321", "SIA321", "live"))
        self.assertIsNone(result.error)
        self.assertEqual(result.checked_at, NOW)
        s = result.sighting
        self.assertEqual((s.origin, s.dest, s.airline), ("LHR", "SIN", "Singapore Airlines"))
        self.assertEqual((s.flight_no, s.alt_ft, s.speed_kt, s.seen_at), ("SQ321", 10000, 350, NOW - 10))
        self.assertAlmostEqual(s.dist_nm, F.adsb.haversine_nm(37.77, -122.42, 1.5, 103.8))
        self.assertAlmostEqual(s.bearing, F.adsb.bearing_deg(37.77, -122.42, 1.5, 103.8))
        self.assertAlmostEqual(s.elev_deg, F.adsb.elevation_deg(s.dist_nm, 10000))
        self.assertIsNotNone(s.eta_min)
        self.assertEqual(self.get.call_args_list[1].args[0], "https://api.adsb.lol/v2/callsign/SIA321")

    def test_missing_altitude_speed_stay_unknown(self):
        ac = {k: v for k, v in AC.items() if k not in ('alt_baro', 'gs')}
        self.get.side_effect = [response(ROUTE), response(feed(ac))]
        result = self.lookup()
        self.assertEqual(result.status, 'live')
        self.assertIsNone(result.sighting.alt_ft)
        self.assertIsNone(result.sighting.speed_kt)
        self.assertIsNone(result.sighting.eta_min)

    def test_icao_live_still_works_when_route_fails(self):
        for failure in [requests.Timeout("route timeout"), response(status=404), response({"bad": 1})]:
            with self.subTest(failure=failure):
                F._cache.clear()
                self.get.side_effect = [failure, response(feed(AC))]
                result = self.lookup("SIA321")
                self.assertEqual(result.status, "live")
                self.assertFalse(result.sighting.has_route)

    def test_iata_unknown_does_not_guess_mapping_or_schedule(self):
        self.get.side_effect = [response(status=404)]
        result = self.lookup()
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.sighting)
        self.assertEqual(result.callsign, "")
        self.assertEqual(self.get.call_count, 1)

    def test_icao_does_not_follow_unrelated_route(self):
        other = copy.deepcopy(ROUTE)
        other["response"]["flightroute"]["callsign_icao"] = "BAW321"
        self.get.side_effect = [response(other), response(feed(AC))]
        result = self.lookup("SIA321")
        self.assertEqual(result.status, "live")
        self.assertIn("/SIA321", self.get.call_args.args[0])
        self.assertFalse(result.sighting.has_route)

    def test_exact_callsign_only(self):
        self.get.side_effect = [response(ROUTE), response(feed(dict(AC, flight="SIA3210")))]
        self.assertEqual(self.lookup().status, "not_found")

    def test_stale_missing_invalid_positions_not_live(self):
        for changes in [{"seen_pos": 121}, {"seen_pos": None}, {"seen_pos": -1},
                        {"lat": None}, {"lon": None}, {"lat": 91}, {"lon": float("nan")},
                        {"seen_pos": True}, {"lat": "1.5"}]:
            with self.subTest(changes=changes):
                F._cache.clear()
                self.get.side_effect = [response(ROUTE), response(feed(dict(AC, **changes)))]
                result = self.lookup()
                self.assertEqual(result.status, "not_found")
                self.assertIsNone(result.sighting)

    def test_duplicate_callsign_is_ambiguous_even_if_one_stale(self):
        self.get.side_effect = [response(ROUTE), response(feed(AC, dict(AC, hex="fff111", seen_pos=200)))]
        result = self.lookup()
        self.assertEqual(result.status, "error")
        self.assertIn("Ambiguous", result.error)
        self.assertIsNone(result.sighting)

    def test_feed_errors_and_bad_json_not_not_found(self):
        bad = [None, {}, {"ac": []}, feed(msg="error"), feed(total=1),
               feed(now=(NOW - 121) * 1000), feed(now=(NOW + 60) * 1000),
               feed(ac={}), feed(total=True)]
        for payload in bad:
            with self.subTest(payload=payload):
                F._cache.clear()
                self.get.side_effect = [response(ROUTE), response(payload)]
                result = self.lookup()
                self.assertEqual(result.status, "error")
                self.assertEqual(result.checked_at, NOW)
        F._cache.clear()
        broken = response()
        broken.json.side_effect = ValueError("bad JSON")
        self.get.side_effect = [response(ROUTE), broken]
        self.assertEqual(self.lookup().status, "error")

    def test_route_mismatch_never_maps_iata(self):
        bad = copy.deepcopy(ROUTE)
        bad["response"]["flightroute"]["callsign"] = "BA208"
        bad["response"]["flightroute"]["callsign_iata"] = "BA208"
        self.get.side_effect = [response(bad)]
        self.assertEqual(self.lookup().status, "error")
        self.assertEqual(self.get.call_count, 1)

    def test_implausible_route_is_not_attached(self):
        self.get.side_effect = [response(ROUTE), response(feed(dict(AC, lat=-40, lon=-100)))]
        result = self.lookup()
        self.assertEqual(result.status, "live")
        self.assertFalse(result.sighting.has_route)
        self.assertEqual((result.origin, result.dest, result.airline), ("", "", ""))
        self.assertIsNone(result.sighting.eta_min)

    def test_cache_ttl_and_location_key_and_mutation_isolation(self):
        result = self.lookup()
        result.sighting.callsign = "MUTATED"
        self.mono.return_value = 1059
        self.assertEqual(self.lookup("SQ321").sighting.callsign, "SIA321")
        self.assertEqual(self.get.call_count, 2)
        self.mono.return_value = 1060
        self.get.side_effect = [response(ROUTE), response(feed())]
        self.assertEqual(self.lookup().status, "not_found")
        self.assertEqual(self.get.call_count, 4)
        self.get.side_effect = [response(ROUTE), response(feed(AC))]
        self.assertEqual(self.lookup(lat=0, lon=0).status, "live")
        self.assertEqual(self.get.call_count, 6)

    def test_errors_cached_and_last_good_retained_not_live(self):
        good = self.lookup()
        self.mono.return_value = 1061
        self.clock.return_value = NOW + 61
        self.get.side_effect = requests.Timeout("upstream timed out")
        failed = self.lookup()
        self.assertEqual(failed.status, "error")
        self.assertEqual(failed.checked_at, NOW + 61)
        self.assertEqual(failed.sighting.seen_at, good.sighting.seen_at)
        self.assertIn("timed out", failed.error)
        calls = self.get.call_count
        self.mono.return_value = 1092
        self.assertEqual(self.lookup().status, "error")
        self.assertEqual(self.get.call_count, calls)

    def test_cached_position_does_not_become_stale_live(self):
        self.get.side_effect = [response(ROUTE), response(feed(dict(AC, seen_pos=119)))]
        self.assertEqual(self.lookup().status, "live")
        self.clock.return_value = NOW + 2
        self.assertEqual(self.lookup().status, "not_found")
        self.assertEqual(self.get.call_count, 2)

    def test_position_age_includes_feed_age(self):
        self.get.side_effect = [response(ROUTE), response(feed(dict(AC, seen_pos=100), now=(NOW - 30) * 1000))]
        self.assertEqual(self.lookup().status, "not_found")

    def test_cache_bounded_and_errors_throttled(self):
        self.get.side_effect = requests.Timeout("offline")
        for number in range(33):
            self.lookup(f"SIA{number}")
        self.assertEqual(len(F._cache), 32)
        calls = self.get.call_count
        self.lookup("SIA32")
        self.assertEqual(self.get.call_count, calls)
        self.lookup("SIA0")
        self.assertGreater(self.get.call_count, calls)
        self.assertEqual(len(F._cache), 32)

    def test_concurrent_same_query_has_one_network_lookup(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.lookup(), range(16)))
        self.assertTrue(all(r.status == "live" for r in results))
        self.assertEqual(self.get.call_count, 2)

    def test_timeout_budget_and_no_redirects(self):
        self.lookup()
        for call in self.get.call_args_list:
            timeout = call.kwargs["timeout"]
            self.assertEqual(timeout.total, 7)
            self.assertEqual(timeout.connect_timeout, 2)
            self.assertEqual(timeout.read_timeout, 5)
            self.assertFalse(call.kwargs["allow_redirects"])

    def test_lock_contention_returns_explicit_error_without_network(self):
        busy = Mock()
        busy.acquire.return_value = False
        with patch.object(F, "_lock", busy):
            result = self.lookup()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.checked_at, NOW)
        self.assertIsNone(result.sighting)
        busy.acquire.assert_called_once_with(timeout=2)
        self.get.assert_not_called()

    def test_blank_invalid_query_and_coordinates_never_network(self):
        self.assertEqual(self.lookup("").status, "not_found")
        for query in ["SQ-321", "SQ321\n", "SQ12345", "SQ\u00a0321"]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.lookup(query)
        for lat, lon in [(91, 0), (0, -181), (True, 0), (float("nan"), 0)]:
            with self.assertRaises(ValueError):
                self.lookup(lat=lat, lon=lon)
        self.assertEqual(self.get.call_count, 0)


if __name__ == "__main__":
    unittest.main()
