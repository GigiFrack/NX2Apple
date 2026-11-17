"""Nextendo sign-in: PKCE, the loopback answer, and the environment the game gets.

    python3 -m unittest discover -s frontend-qt/tests

Nothing here talks to Nextendo. The token endpoint is a local server that answers the same
shape, and the "browser" is a function that opens the redirect URL itself, so the whole flow
is exercised end to end without a network or an account.
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'frontend-qt'))

from nspb_qt import nextendo_auth as na  # noqa: E402

TOKEN = {'access_token': 'at-123', 'token_type': 'Bearer', 'expires_in': 3600,
         'refresh_token': 'rt-456', 'name': 'Mattia'}


def fire(url: str) -> None:
    """Open a URL and swallow the status: the server's answer is what the test reads."""
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            r.read()
    except urllib.error.HTTPError as e:
        e.close()
    except OSError:
        pass


class TokenServer:
    """A stand-in for Nextendo's token endpoint: answers the shape, records the request."""

    def __init__(self, body: dict | None = None):
        outer = self
        self.body = TOKEN if body is None else body
        self.form: dict[str, list[str]] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                                     # noqa: N802 - stdlib API
                n = int(self.headers.get('Content-Length') or 0)
                outer.form = urllib.parse.parse_qs(self.rfile.read(n).decode('ascii'))
                payload = json.dumps(outer.body).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):                   # noqa: A002 - stdlib API
                pass

        self.httpd = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.port}/token'

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class PkceTests(unittest.TestCase):
    def test_challenge_is_s256_of_the_verifier(self):
        import base64
        import hashlib
        v = na.new_verifier()
        expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(na.challenge_for(v), expected)

    def test_verifier_is_url_safe_and_inside_the_rfc_bounds(self):
        import re
        v = na.new_verifier()
        self.assertRegex(v, r'^[A-Za-z0-9\-_.~]{43,128}$')

    def test_challenge_of_an_empty_verifier_is_refused(self):
        with self.assertRaises(na.AuthError):
            na.challenge_for('')


class LoopbackTests(unittest.TestCase):
    def test_the_redirect_delivers_code_and_state(self):
        srv = na.LoopbackServer(expect_state='st-1')
        try:
            box: list[dict] = []
            t = threading.Thread(target=lambda: box.append(srv.wait(5)), daemon=True)
            t.start()
            fire(srv.redirect_uri + '?code=abc&state=st-1')
            t.join(5)
            self.assertEqual(box[0]['code'], 'abc')
            self.assertEqual(box[0]['state'], 'st-1')
        finally:
            srv.close()

    def test_an_answer_with_another_state_is_rejected(self):
        srv = na.LoopbackServer(expect_state='expected')
        try:
            t = threading.Thread(target=fire, args=(srv.redirect_uri + '?code=abc&state=other',),
                                 daemon=True)
            t.start()
            with self.assertRaises(na.AuthError):
                srv.wait(5)
            t.join(5)
        finally:
            srv.close()

    def test_a_refusal_from_nextendo_says_why(self):
        srv = na.LoopbackServer()
        try:
            t = threading.Thread(
                target=fire, args=(srv.redirect_uri + '?error=access_denied&error_description=no',),
                daemon=True)
            t.start()
            with self.assertRaises(na.AuthError) as cm:
                srv.wait(5)
            self.assertIn('access_denied', str(cm.exception))
            t.join(5)
        finally:
            srv.close()


class FlowTests(unittest.TestCase):
    def test_sign_in_returns_the_token_and_sends_the_verifier(self):
        tokens = TokenServer()
        try:
            cfg = na.Config('https://nextendo.example/authorize', tokens.url, 'client-1', 'openid')
            seen: list[str] = []

            def browser(url):
                seen.append(url)
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                cb = q['redirect_uri'][0] + '?' + urllib.parse.urlencode(
                    {'code': 'CODE-1', 'state': q['state'][0]})
                threading.Thread(target=fire, args=(cb,), daemon=True).start()
                return True

            token = na.sign_in(cfg, open_browser=browser, timeout=5)

            self.assertEqual(token['access_token'], 'at-123')
            self.assertEqual(token['name'], 'Mattia')
            self.assertIn('obtained_at', token)
            authorize = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
            self.assertEqual(authorize['code_challenge_method'], ['S256'])
            self.assertEqual(authorize['client_id'], ['client-1'])
            # the verifier sent to the token endpoint is the one the challenge was made from
            self.assertEqual(na.challenge_for(tokens.form['code_verifier'][0]),
                             authorize['code_challenge'][0])
            self.assertEqual(tokens.form['grant_type'], ['authorization_code'])
            self.assertEqual(tokens.form['redirect_uri'][0], authorize['redirect_uri'][0])
        finally:
            tokens.close()

    def test_sign_in_with_no_answer_times_out_with_a_message_for_the_player(self):
        cfg = na.Config('https://nextendo.example/authorize', 'https://nextendo.example/token',
                        'client-1')
        with self.assertRaises(na.AuthError) as cm:
            na.sign_in(cfg, open_browser=lambda url: True, timeout=0.6)
        self.assertIn('closed', str(cm.exception))

    def test_a_token_answer_without_a_token_is_refused(self):
        tokens = TokenServer({'error': 'invalid_grant'})
        try:
            cfg = na.Config('https://x/a', tokens.url, 'c')
            with self.assertRaises(na.AuthError):
                na.exchange_code(cfg, 'CODE', 'verifier', 'http://127.0.0.1:1/callback')
        finally:
            tokens.close()


class StorageTests(unittest.TestCase):
    def test_the_token_file_is_owner_only(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = na.write_token_file(TOKEN, Path(d) / 'sub' / 'nextendo-token.json')
            tok = na.read_token_file(p)
            self.assertIsNotNone(tok)
            self.assertEqual((tok or {})['access_token'], 'at-123')
            if os.name == 'posix':
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_no_token_means_no_environment_change(self):
        self.assertEqual(na.nextendo_env(None), {})
        self.assertEqual(na.nextendo_env(''), {})

    def test_a_token_file_becomes_the_two_switches(self):
        env = na.nextendo_env('/tmp/nextendo-token.json')
        self.assertEqual(env, {'DM_NEXTENDO': '1', 'DM_NEXTENDO_TOKEN_FILE': '/tmp/nextendo-token.json'})

    def test_a_missing_app_file_says_what_to_do(self):
        with self.assertRaises(na.AuthError) as cm:
            na.Config.load('/nonexistent/nextendo-app.json')
        self.assertIn('nextendo.network/developers', str(cm.exception))

    def test_an_app_file_without_a_client_id_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'app.json'
            p.write_text(json.dumps({'authorize_url': 'https://x/a', 'token_url': 'https://x/t'}))
            with self.assertRaises(na.AuthError) as cm:
                na.Config.load(p)
            self.assertIn('client_id', str(cm.exception))

    def test_describe_names_the_account_when_it_can(self):
        self.assertIn('Mattia', na.describe(TOKEN))
        self.assertIn('Non connesso', na.describe(None))


class RegisteredClientTests(unittest.TestCase):
    """A registered app is confidential and its redirect is an exact string (oauth.go:299)."""

    def test_a_file_with_only_the_client_id_gets_the_defaults(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'nextendo-app.json'
            p.write_text(json.dumps({'client_id': 'nxc_TEST'}))
            cfg = na.Config.load(p)
        self.assertEqual(cfg.client_id, 'nxc_TEST')
        self.assertEqual(cfg.authorize_url, na.DEFAULT_AUTHORIZE_URL)
        self.assertEqual(cfg.token_url, na.DEFAULT_TOKEN_URL)
        self.assertEqual(cfg.redirect_port, na.DEFAULT_REDIRECT_PORT)
        self.assertEqual(cfg.redirect_uri, 'http://127.0.0.1:47821/callback')
        self.assertIsNone(cfg.client_secret)

    def test_the_sign_in_uses_the_registered_port(self):
        import socket
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        cfg = na.Config('https://x/a', 'https://x/t', 'nxc_1', redirect_port=port)
        seen = []

        def browser(url):
            seen.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query))
            cb = seen[0]['redirect_uri'][0] + '?' + urllib.parse.urlencode(
                {'code': 'C', 'state': seen[0]['state'][0]})
            threading.Thread(target=fire, args=(cb,), daemon=True).start()
            return True

        tokens = TokenServer()
        try:
            cfg = na.Config('https://x/a', tokens.url, 'nxc_1', redirect_port=port)
            na.sign_in(cfg, open_browser=browser, timeout=5)
        finally:
            tokens.close()
        self.assertEqual(seen[0]['redirect_uri'][0], f'http://127.0.0.1:{port}/callback')

    def test_a_busy_port_names_the_port_and_what_to_do(self):
        import socket
        busy = socket.socket()
        busy.bind(('127.0.0.1', 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        try:
            cfg = na.Config('https://x/a', 'https://x/t', 'nxc_1', redirect_port=port)
            with self.assertRaises(na.AuthError) as cm:
                na.sign_in(cfg, open_browser=lambda url: True, timeout=0.2)
            self.assertIn(str(port), str(cm.exception))
        finally:
            busy.close()

    def test_a_confidential_client_sends_its_secret(self):
        tokens = TokenServer()
        try:
            cfg = na.Config('https://x/a', tokens.url, 'nxc_1', client_secret='shh')
            na.exchange_code(cfg, 'CODE', 'verifier', cfg.redirect_uri)
            self.assertEqual(tokens.form['client_secret'], ['shh'])
            self.assertEqual(tokens.form['code_verifier'], ['verifier'])
        finally:
            tokens.close()

    def test_invalid_client_says_what_kind_of_app_to_register(self):
        import http.server

        class Refuse(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                                     # noqa: N802 - stdlib API
                body = b'{"error":"invalid_client"}'
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):                   # noqa: A002 - stdlib API
                pass

        srv = http.server.HTTPServer(('127.0.0.1', 0), Refuse)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cfg = na.Config('https://x/a', f'http://127.0.0.1:{port}/token', 'nxc_1')
            with self.assertRaises(na.AuthError) as cm:
                na.exchange_code(cfg, 'CODE', 'v', cfg.redirect_uri)
            self.assertIn('public application', str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_a_public_client_sends_no_secret(self):
        tokens = TokenServer()
        try:
            cfg = na.Config('https://x/a', tokens.url, 'nxc_1')
            na.exchange_code(cfg, 'CODE', 'verifier', cfg.redirect_uri)
            self.assertNotIn('client_secret', tokens.form)
        finally:
            tokens.close()


if __name__ == '__main__':
    unittest.main()
