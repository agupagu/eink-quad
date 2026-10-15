"""Flight viewer render and read-only endpoint regression checks."""
import sys
import time
import threading
import unittest
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import app as A
import render as R


def snapshot(query='SQ321', status='not_found', sighting=None):
    return SimpleNamespace(query=query, callsign='SIA321', status=status,
                           sighting=sighting, error='provider timeout' if status=='error' else None,
                           checked_at=1700000000.0)


class FlightViewTests(unittest.TestCase):
    def test_renderer_missing_live_and_error_states(self):
        s = R.Sighting(callsign='SIA321', airline='Singapore Airlines', origin='LHR', dest='SIN',
                       origin_city='London', dest_city='Singapore', alt_ft=35000, speed_kt=480,
                       dist_nm=120, elev_deg=10, seen_at=time.time()-20, eta_min=25, progress=.95)
        for state, sighting in [('not_found', None), ('error', None), ('live', s), ('error', s)]:
            ctx = R.Ctx(now=time.time(), cfg=A.DEFAULTS.copy(), page='sky', tracked=snapshot(status=state, sighting=sighting))
            with patch.object(R, 'text', wraps=R.text) as draw:
                image = R.compose(ctx)
            self.assertEqual(image.size, (800, 480))
            labels = [str(c.args[2]) for c in draw.call_args_list]
            self.assertIn('SQ321 · SIA321', labels)
            if state=='live':
                self.assertIn('LIVE POSITION', labels)
                self.assertIn('LHR → SIN', labels)
            elif sighting:
                self.assertIn('LAST KNOWN · FEED ERROR', labels)
                self.assertNotIn('LIVE POSITION', labels)
            else:
                self.assertTrue(any('No live position' in x or 'temporarily unavailable' in x for x in labels))
        s.alt_ft = s.speed_kt = None
        ctx.tracked = snapshot(status='live', sighting=s)
        self.assertEqual(R.compose(ctx).size, (800, 480))

    def test_endpoint_auth_identity_validation_and_lock(self):
        import flighttrack as F
        def lookup(query, lat, lon):
            self.assertFalse(A.board.cond._is_owned(), 'network lookup holds render lock')
            return snapshot(query)
        with patch.object(A, 'TABLET_TOKEN', 'flight-view-test'), patch.object(F, 'track_flight', side_effect=lookup) as fetch:
            server = A.Server(('127.0.0.1', 0), A.H)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_port}/api/tablet/frame'
            try:
                for path, code in [('?page=sky&flight=SQ321',404),
                                   ('?k=flight-view-test&page=sky&flight=bad!',400),
                                   ('?k=flight-view-test&page=crypto&flight=SQ321',400)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base+path, timeout=5)
                    self.assertEqual(error.exception.code, code)
                    error.exception.close()
                fetch.assert_not_called()
                versions=[]
                for flight in ['SQ321','EK203']:
                    url=base+'?k=flight-view-test&page=sky&flight='+flight
                    with urllib.request.urlopen(url,timeout=5) as response:
                        self.assertEqual(response.headers['Content-Type'],'image/png')
                        self.assertTrue(response.read().startswith(b'\x89PNG'))
                        version=response.headers['X-Version'];versions.append(version)
                    with urllib.request.urlopen(url+'&v='+urllib.parse.quote(version),timeout=5) as response:
                        self.assertEqual(response.status,204)
                self.assertNotEqual(*versions)
                with urllib.request.urlopen(base+'?k=flight-view-test&page=sky&v='+urllib.parse.quote(versions[0]),timeout=5) as response:
                    self.assertEqual(response.status,200)
                    self.assertNotEqual(response.headers['X-Version'],versions[0])
            finally:
                server.shutdown();server.server_close()


if __name__=='__main__':
    unittest.main()
