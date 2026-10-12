"""Regression checks for the four personalized sports and tablet-friendly render."""
import os
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


class SportTests(unittest.TestCase):
    def test_default_four_sources(self):
        self.assertEqual(set(S.TEAMS), {"man_utd", "india_men", "f1", "cal_fb"})
        self.assertEqual(S.TEAMS["man_utd"][-1], "360")
        self.assertEqual(S.TEAMS["cal_fb"][-1], "25")

    def test_cricket_innings_and_womens_exclusion(self):
        def event(name="India v Australia"):
            return {"id": "123", "name": name, "date": "2026-10-20T09:00:00Z",
                    "status": "in", "competitors": [
                        {"abbreviation": "IND", "name": "India", "score": "178/5"},
                        {"abbreviation": "AUS", "name": "Australia", "score": "176/8"}]}
        data = {"sports": [{"leagues": [
            {"name": "India tour of Australia", "events": [event()]},
            {"name": "Women's World Cup", "events": [event("India Women v Australia")]},
        ]}]}
        with patch.object(S, "_espn", return_value=data):
            games = S._india_header_games(data)
        self.assertEqual(len(games), 1)
        self.assertIn("IND 178/5", games[0].detail)
        self.assertIsNone(games[0].us)
        self.assertEqual(games[0].opp, "AUS")

    def test_dated_header_discovers_full_india_series(self):
        daily = {"sports": [{"leagues": [{"id": "24289", "name": "West Indies tour of India",
                 "events": [{"name": "India v West Indies", "competitors": [
                     {"abbreviation": "IND"}, {"abbreviation": "WI"}]}]}]}]}
        season = {"events": [{"id": "1529233", "name": "India v West Indies",
                 "date": "2026-10-14T13:30Z", "status": {"type": {"state": "pre"}},
                 "competitions": [{"competitors": [
                     {"homeAway": "home", "team": {"abbreviation": "IND"}},
                     {"homeAway": "away", "team": {"abbreviation": "WI", "location": "West Indies"}}]}]}]}
        with patch.object(S, "_india_header_safe", return_value=daily), \
             patch.object(S, "_espn", return_value=season):
            games = S._india_series_calendar()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].opp_name, "West Indies")
        self.assertEqual(games[0].event_id, "1529233")
        self.assertIsNone(games[0].us)

    def test_f1_calendar_never_fabricates_scores(self):
        data = {"events": [{"id": "77", "name": "Singapore Grand Prix",
                            "date": "2026-10-20T09:00:00Z", "status": {"type": {"state": "pre"}}}]}
        with patch.object(S, "_espn", return_value=data):
            games = S._fetch_f1()
        self.assertEqual(games[0].opp_name, "Singapore Grand Prix")
        self.assertIsNone(games[0].us)
        self.assertIsNone(games[0].them)

    def test_every_detail_screen_renders(self):
        cfg = {"slots": ["weather", "news", "sports", "sky"], "city": "Singapore",
               "lat": 1.3521, "lon": 103.8198, "units": "C", "radar_nm": 25,
               "show_idle": True, "teams": list(S.TEAMS)}
        rows = [S.TeamRow(key=k, label=v[0]) for k, v in S.TEAMS.items()]
        for page in ["quad", "weather", "news", "sports", "sky"]:
            with self.subTest(page=page):
                img = R.compose(R.Ctx(now=time.time(), cfg=cfg, teams=rows, page=page))
                self.assertEqual(img.size, (R.W, R.H))

    def test_tablet_viewer_is_read_only_and_conditional(self):
        import app as A
        with patch.object(A, "TABLET_TOKEN", "local-tablet"):
            server = A.Server(("127.0.0.1", 0), A.H)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(base + "/tablet", timeout=5)
                self.assertEqual(rejected.exception.code, 404)
                rejected.exception.close()
                with urllib.request.urlopen(base + "/tablet?k=local-tablet", timeout=5) as r:
                    page = r.read()
                    self.assertIn(b"apple-mobile-web-app-capable", page)
                    for key in S.TEAMS:
                        self.assertIn(f'data-key="{key}"'.encode(), page)
                with urllib.request.urlopen(base + "/tablet/legacy?k=local-tablet", timeout=5) as r:
                    self.assertIn(b'data-page="sports"', r.read())
                with urllib.request.urlopen(base + "/tablet/india-cricket.svg?k=local-tablet", timeout=5) as r:
                    self.assertEqual(r.headers["Content-Type"], "image/svg+xml")
                    self.assertIn(b"<svg", r.read())
                with self.assertRaises(urllib.error.HTTPError) as rejected_logo:
                    urllib.request.urlopen(base + "/tablet/india-cricket.svg", timeout=5)
                self.assertEqual(rejected_logo.exception.code, 404)
                rejected_logo.exception.close()
                with urllib.request.urlopen(base + "/api/tablet/sports?k=local-tablet", timeout=5) as r:
                    data = json.load(r)
                    self.assertEqual({row["key"] for row in data["sports"]}, set(S.TEAMS))
                    self.assertEqual(set(data["sports"][0]), {"key", "label", "live", "next", "last", "record"})
                with self.assertRaises(urllib.error.HTTPError) as rejected_sports:
                    urllib.request.urlopen(base + "/api/tablet/sports", timeout=5)
                self.assertEqual(rejected_sports.exception.code, 404)
                rejected_sports.exception.close()
                with urllib.request.urlopen(base + "/api/tablet/frame?page=sports&k=local-tablet", timeout=5) as r:
                    self.assertEqual(r.headers["Content-Type"], "image/png")
                    version = r.headers["X-Version"]
                with urllib.request.urlopen(base + f"/api/tablet/frame?page=sports&k=local-tablet&v={version}", timeout=5) as r:
                    self.assertEqual(r.status, 204)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
