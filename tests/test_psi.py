"""NEA 24-hour PSI regression tests; all API payloads are explicit fixtures."""
from __future__ import annotations

import sys
import time
from unittest.mock import patch
from PIL import Image, ImageChops
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import sources as S


def payload(values=None, timestamp="2026-10-12T19:00:00+08:00"):
    return {"code": 0, "data": {"items": [{"timestamp": timestamp,
            "updatedTimestamp": "2026-10-12T19:30:54+08:00",
            "readings": {"psi_twenty_four_hourly": values if values is not None else
                         {"north": 118, "south": 122, "west": 155, "east": 139, "central": 155},
                         "pm25_twenty_four_hourly": {"north": 72}}}]}}


class PsiTests(unittest.TestCase):
    def test_live_v2_schema_uses_psi_not_pm25(self):
        reading = S.parse_psi(payload())
        self.assertEqual(reading.values["north"], 118)
        self.assertEqual(reading.values["central"], 155)
        self.assertIsNotNone(reading.timestamp.tzinfo)
        self.assertEqual(reading.timestamp.utcoffset().total_seconds(), 28800)

    def test_missing_invalid_and_extra_regions_are_not_zero(self):
        reading = S.parse_psi(payload({"north": 0, "south": None, "east": "139",
                                       "west": True, "central": 155, "national": 91}))
        self.assertEqual(reading.values, {"north": 0, "central": 155})
        for invalid in ({}, {"north": -1}, {"north": float("nan")}, {"north": 12.5}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                S.parse_psi(payload(invalid))

    def test_empty_error_naive_and_pm25_only_payloads_rejected(self):
        bad = payload()
        del bad["data"]["items"][0]["readings"]["psi_twenty_four_hourly"]
        for item in ({"code": 1, "errorMsg": "down"}, {"code": 0, "data": {"items": []}},
                     bad, None, {"code": 0, "data": None},
                     payload(timestamp="2026-10-12T19:00:00")):
            with self.subTest(item=item), self.assertRaises(ValueError):
                S.parse_psi(item)

    def test_newest_reporting_time_not_update_time_or_array_order(self):
        older = payload(timestamp="2026-10-12T17:00:00Z")
        newest = payload(timestamp="2026-10-13T02:00:00+08:00")
        combined = {"code": 0, "data": {"items": newest["data"]["items"] + older["data"]["items"]}}
        self.assertEqual(S.parse_psi(combined).timestamp.hour, 2)

    def test_stale_threshold_and_failed_refresh_keep_last_good(self):
        with patch.object(S, "_psi", S.Cached(600)), patch.object(S, "_fetch_psi", return_value=S.parse_psi(payload())) as fetch:
            first = S.psi(force=True)
            boundary = first.timestamp.timestamp()
            self.assertFalse(first.stale(boundary + 7199))
            self.assertTrue(first.stale(boundary + 7200))
            S.psi()
            self.assertEqual(fetch.call_count, 1)
            fetch.side_effect = RuntimeError("network down")
            failed = S.psi(force=True)
            self.assertEqual(failed.values, first.values)
            self.assertEqual(failed.timestamp, first.timestamp)
            self.assertIsNotNone(failed.error)
            self.assertTrue(failed.stale(boundary + 10))
            fetch.side_effect = None
            recovered = S.psi(force=True)
            self.assertIsNone(recovered.error)
            self.assertEqual(recovered.values["west"], 155)

    def test_first_failure_yields_no_data_not_fake_values(self):
        with patch.object(S, "_psi", S.Cached(600)), patch.object(S, "_fetch_psi", side_effect=ValueError("empty feed")):
            reading = S.psi(force=True)
            self.assertEqual(reading.values, {})
            self.assertIsNone(reading.timestamp)
            self.assertTrue(reading.error)

    def test_location_gating_weather_cadence_and_forecast_failure(self):
        import render as R
        singapore = {"lat": 1.3521, "lon": 103.8198, "city": "Singapore", "units": "C"}
        wx = sample_weather()
        with patch.object(S, "_psi", S.Cached(600)), patch.object(S, "_weather", S.Cached(600)), \
             patch.object(S, "_fetch_psi", return_value=S.parse_psi(payload())) as get_psi, \
             patch.object(S, "_fetch_weather", return_value=wx):
            self.assertEqual(S.weather(singapore).psi.values["north"], 118)
            S.weather(singapore)
            self.assertEqual(get_psi.call_count, 1)
            S.weather({"lat": 51.5, "lon": -.12, "city": "Singapore"})
            self.assertEqual(get_psi.call_count, 1)
            self.assertIsNone(wx.psi)
        with patch.object(S, "_psi", S.Cached(600)), patch.object(S, "_weather", S.Cached(600)), \
             patch.object(S, "_fetch_psi", return_value=S.parse_psi(payload())), \
             patch.object(S, "_fetch_weather", side_effect=RuntimeError("weather unavailable")):
            self.assertIsNone(S.weather(singapore))
            ctx = R.Ctx(now=time.time(), cfg=singapore, psi=S.psi_snapshot())
            self.assertEqual(R.panel_weather(ctx, (400, 207)).size, (400, 207))
            self.assertIn(118, R._ctx_psi(ctx).values.values())
        self.assertTrue(S.is_singapore({"city": "Singapore"}))
        self.assertFalse(S.is_singapore({"lat": 3.1, "lon": 101.7}))

    def test_category_boundaries(self):
        for value, expected in ((0, "Good"), (50, "Good"), (51, "Moderate"), (100, "Moderate"),
                                (101, "Unhealthy"), (200, "Unhealthy"), (201, "Very unhealthy"),
                                (300, "Very unhealthy"), (301, "Hazardous"), (None, "No data")):
            self.assertEqual(S.psi_category(value), expected)

    def test_compact_and_full_render_preserve_rain_forecast_and_bounds(self):
        import render as R
        for reading in (S.parse_psi(payload()), S.parse_psi(payload({"north": 118})),
                        S.Psi(error="down"), S.Psi()):
            with self.subTest(values=reading.values):
                ctx = R.Ctx(now=S.parse_psi(payload()).timestamp.timestamp() + 60,
                            cfg={"lat": 1.35, "lon": 103.82}, weather=sample_weather(reading))
                seen = []
                original = R.text
                original_pill = R.pill
                def record_pill(d, x, y, s, f, **kwargs):
                    seen.append(s)
                    return original_pill(d, x, y, s, f, **kwargs)
                def record(d, xy, s, f, **kwargs):
                    seen.append(s)
                    return original(d, xy, s, f, **kwargs)
                with patch.object(R, "text", side_effect=record), patch.object(R, "pill", side_effect=record_pill), patch.object(S.requests, "get", side_effect=AssertionError("render fetched")):
                    compact = R.panel_weather(ctx, (400, 207))
                    full = R.detail_weather(ctx, (800, 414))
                self.assertEqual(compact.size, (400, 207))
                self.assertEqual(full.size, (800, 414))
                for label in ("24H PSI · NEA", "N/S/E/W compass · C central", "NEXT 24 HOURS"):
                    self.assertIn(label, seen)
                self.assertTrue(any("North " in s for s in seen))
                self.assertTrue(any("Rain starting" in s for s in seen))
                self.assertTrue(any("UV " in s for s in seen))
                if not reading.values:
                    self.assertTrue(any("No data" in s for s in seen))
                self.assertTrue(any("schematic" in s for s in seen))
                # A changed PSI can only change the weather quadrant, not any adjacent slot.
                panels = {key: Image.new("L", (400, 207), 255) for key in ("news", "sports", "sky")}
                baseline = R.compose_quad(ctx, panels)
                ctx.weather.psi = S.parse_psi(payload({r: 999 for r in S.PSI_REGIONS}))
                changed = R.compose_quad(ctx, panels)
                bbox = ImageChops.difference(baseline, changed).getbbox()
                self.assertIsNotNone(bbox)
                self.assertLessEqual(bbox[2], 399)
                self.assertLessEqual(bbox[3], 273)
                self.assertGreaterEqual(bbox[1], R.GRID_Y)

    def test_cache_refreshes_after_weather_ttl_and_throttles_errors(self):
        with patch.object(S, "_psi", S.Cached(600)), patch.object(S.time, "time", return_value=1000) as clock, \
             patch.object(S, "_fetch_psi", return_value=S.parse_psi(payload())) as fetch:
            S.psi()
            clock.return_value = 1599
            S.psi()
            self.assertEqual(fetch.call_count, 1)
            clock.return_value = 1601
            fetch.side_effect = RuntimeError("outage")
            failed = S.psi()
            self.assertEqual(fetch.call_count, 2)
            self.assertTrue(failed.error)
            clock.return_value = 1602
            S.psi()
            self.assertEqual(fetch.call_count, 2)

    def test_http_boundary_uses_official_endpoint_timeout_and_status(self):
        from unittest.mock import Mock
        response = Mock()
        response.json.return_value = payload()
        with patch.object(S.requests, "get", return_value=response) as get:
            reading = S._fetch_psi()
            self.assertEqual(get.call_args[0][0], S.PSI_URL)
            self.assertEqual(get.call_args[1]["timeout"], 10)
            response.raise_for_status.assert_called_once()
            self.assertEqual(reading.values["south"], 122)

    def test_compact_facts_do_not_collide_with_feels_or_rain_pill(self):
        import render as R
        ctx = R.Ctx(now=S.parse_psi(payload()).timestamp.timestamp(),
                    cfg={"lat": 1.35, "lon": 103.82}, weather=sample_weather(S.parse_psi(payload())))
        boxes = {}
        original = R.text
        def record(d, xy, s, f, **kwargs):
            if s.startswith("Feels") or s.startswith("Wind "):
                boxes[s.split()[0]] = d.textbbox(xy, s, font=f, anchor=kwargs.get("anchor", "la"))
            return original(d, xy, s, f, **kwargs)
        with patch.object(R, "text", side_effect=record):
            R.panel_weather(ctx, (400, 207))
        self.assertLess(boxes["Feels"][3], boxes["Wind"][1])
        self.assertLess(boxes["Wind"][3], 128)

    def test_fresh_old_and_error_timestamp_labels(self):
        import render as R
        reading = S.parse_psi(payload())
        now = reading.timestamp.timestamp()
        self.assertIn("19:00 SGT", R._psi_stamp(reading, now))
        self.assertNotIn("STALE", R._psi_stamp(reading, now))
        self.assertIn("STALE", R._psi_stamp(reading, now + 7200))
        reading.error = "failed"
        self.assertIn("API ERROR", R._psi_stamp(reading, now))
        self.assertIn("No data", R._psi_stamp(S.Psi(), now))


def sample_weather(reading=None):
    now = S.parse_psi(payload()).timestamp.timestamp()
    return S.Weather(temp=31, feels=34, code=3, is_day=True, humidity=77,
                     wind=5, wind_dir=90, gust=8,
                     days=[S.Day(date=f"2026-10-{12+i}", hi=32, lo=26, code=3, pop=65,
                                 sunrise="7:00 AM", sunset="7:00 PM", uv=8) for i in range(5)],
                     hourly=[(now+i*3600, 28+i%4, 20+i%3*30, 3) for i in range(24)],
                     rain_note="Rain starting around 4 PM", psi=reading)


if __name__ == "__main__":
    unittest.main()
