"""Sports uses the same renderer, with separate overview/focus identities."""
import sys
import time
import threading
import unittest
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import app as A
import render as R
import sources as S


class RasterSportsTests(unittest.TestCase):
    def test_cricket_future_title_is_matchup_not_feed_time(self):
        row = S.TeamRow(key='india_men', label='India Men')
        game = S.Game(team='India', abbr='IND', opp='WI', opp_name='West Indies',
                      home=True, start=time.time(), state='pre', detail='19:00 local time')
        self.assertEqual(R._sports_game_title(row, game), 'India vs West Indies')
        game.state = 'post'
        game.detail = 'IND 223 · WI 247/3 · West Indies won by 24 runs'
        self.assertEqual(R._sports_game_title(row, game), game.detail)

    def test_overview_and_focus_use_renderer_fonts(self):
        rows = [S.TeamRow(key=k, label=S.TEAMS[k][0]) for k in ('india_men', 'man_utd', 'cal_fb', 'f1')]
        for focus in ('', 'india_men', 'man_utd', 'cal_fb', 'f1'):
            ctx = R.Ctx(now=time.time(), cfg={**A.DEFAULTS, 'sports_focus': focus}, teams=rows, page='sports')
            with patch.object(R, 'text', wraps=R.text) as draw:
                image = R.compose(ctx)
            self.assertEqual(image.size, (800, 480))
            labels = [str(call.args[2]) for call in draw.call_args_list]
            if focus:
                self.assertIn(S.TEAMS[focus][0].upper(), labels)
                self.assertTrue(any('PREVIOUS' in label for label in labels))
            else:
                positions = [labels.index(S.TEAMS[k][0].upper()) for k in ('india_men', 'man_utd', 'cal_fb', 'f1')]
                self.assertEqual(positions, sorted(positions))

    def test_focus_validation_and_conditional_ownership(self):
        with patch.object(A, 'TABLET_TOKEN', 'raster-test'):
            server = A.Server(('127.0.0.1', 0), A.H)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_port}/api/tablet/frame?k=raster-test'
            try:
                versions = []
                for focus in ('', 'india_men', 'man_utd', 'cal_fb', 'f1'):
                    url = base + '&page=sports' + ('&sport='+focus if focus else '')
                    with urllib.request.urlopen(url, timeout=5) as response:
                        self.assertEqual(response.headers['Content-Type'], 'image/png')
                        self.assertTrue(response.read().startswith(b'\x89PNG'))
                        version = response.headers['X-Version']
                    versions.append(version)
                    with urllib.request.urlopen(url+'&v='+urllib.parse.quote(version), timeout=5) as response:
                        self.assertEqual(response.status, 204)
                self.assertEqual(len(set(versions)), 5)
                for query in ('&page=sports&sport=bad', '&page=crypto&sport=man_utd'):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base+query, timeout=5)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
