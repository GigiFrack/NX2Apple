"""`make_phone_env.py`: the four lines the phone gets, and nothing that leaks into the file."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'frontend-qt'))
sys.path.insert(0, str(ROOT / 'frontend-qt' / 'tools'))

import make_phone_env as mpe  # noqa: E402
from nspb_qt import nextendo_auth as na  # noqa: E402


class NexServer:
    """Answers `/api/nex-token` with the shape Nextendo gives."""

    def __init__(self, body: dict | None = None):
        outer = self
        self.body = {'nex_token': 'nx2.secret.value', 'pid': 1800057957, 'friend_code': 'SW-3593',
                     'username': 'blackhole12x', 'expires_in': 86400} if body is None else body
        self.headers_seen: dict[str, str] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                                      # noqa: N802 - stdlib API
                outer.headers_seen = {k.lower(): v for k, v in self.headers.items()}
                payload = json.dumps(outer.body).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):                   # noqa: A002 - stdlib API
                pass

        self.srv = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.srv.server_address[1]}/api/nex-token'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class PhoneEnvTests(unittest.TestCase):
    def test_the_four_lines_name_the_game_and_carry_the_token(self):
        srv = NexServer()
        try:
            cfg = na.Config('https://x/a', 'https://x/t', 'nxc_1', nex_token_url=srv.url)
            env = mpe.phone_env('0100965017338000', {'access_token': 'at-1'}, cfg)
            self.assertEqual(env, {
                'DM_NEXTENDO': '1',
                'DM_NEXTENDO_APP_ID': '0100965017338000',
                'DM_NEXTENDO_NEX_TOKEN': 'nx2.secret.value',
                'DM_NEXTENDO_PID': '1800057957',
            })
        finally:
            srv.close()

    def test_the_command_writes_only_the_switches(self):
        srv = NexServer()
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / 'nextendo-token.json'
            token.write_text(json.dumps({'access_token': 'at-1'}), encoding='utf-8')
            app = Path(d) / 'nextendo-app.json'
            app.write_text(json.dumps({'client_id': 'nxc_1', 'nex_token_url': srv.url}), encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = mpe.main(['0100965017338000', '--token', str(token), '--app', str(app)])
            finally:
                srv.close()
        self.assertEqual(rc, 0)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 4, lines)
        self.assertTrue(all('=' in l and not l.startswith('#') for l in lines), lines)
        self.assertIn('24 hours', err.getvalue())
        self.assertNotIn('nx2.secret.value', err.getvalue())

    def test_no_token_says_what_to_do_and_writes_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mpe.main(['0100965017338000', '--token', '/nonexistent/token.json'])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('sign in', err.getvalue())


if __name__ == '__main__':
    unittest.main()
