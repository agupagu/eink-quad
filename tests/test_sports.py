"""Regression checks for the four personalized sports and tablet-friendly render."""
import os
import sys
import time
import unittest
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
            games = S._fetch_india_men()
        self.assertEqual(len(games), 1)
        self.assertIn("IND 178/5", games[0].detail)
        self.assertIsNone(games[0].us)
        self.assertEqual(games[0].opp, "AUS")

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


if __name__ == "__main__":
    unittest.main()
