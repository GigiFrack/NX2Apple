"""Nextendo Network sign-in for the launchers: OAuth 2.0 + PKCE, loopback redirect.

A *public* client, as nextendo.network/developers requires for desktop apps: no client
secret, the player types the password on nextendo.network and never in this app. The
redirect goes to ``http://127.0.0.1:<port>/callback`` — a port this process holds, so the
answer cannot be delivered to another program.

The token is written once (mode 0600) and handed to the game through the environment the
host's HLE already reads: ``DM_NEXTENDO=1`` and ``DM_NEXTENDO_TOKEN_FILE=<path>``. With no
token nothing changes and every offline branch in the host stays as it is.

The two endpoint URLs come from ``nextendo-app.json`` (see ``Config.load``), because they
belong to Nextendo's deployment, not to this app.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

TOKEN_ENV = 'DM_NEXTENDO_TOKEN_FILE'   # the host reads the token from this path
ENABLE_ENV = 'DM_NEXTENDO'             # master switch: unset keeps the offline behaviour


class AuthError(Exception):
    """A failure the player can act on: the message says what to do next."""


# ---------------------------------------------------------------- PKCE

def new_verifier() -> str:
    """A 64-character URL-safe secret, inside the 43..128 characters RFC 7636 allows."""
    return base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b'=').decode()


def challenge_for(verifier: str) -> str:
    """S256 challenge: base64url(sha256(verifier)), unpadded."""
    if not verifier:
        raise AuthError('the sign-in secret is empty')
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()


def check_state(got: str | None, expect: str) -> None:
    """The answer must belong to the request this process started."""
    if not got or not secrets.compare_digest(got, expect):
        raise AuthError('the sign-in answer did not come from the request this app started')


# ---------------------------------------------------------------- config

#: Where Nextendo's account service answers, as its source routes them
#: (`/api/oauth/authorize`, `/api/oauth/token`). Kept here only as a default: an app whose
#: deployment differs names its own two URLs in nextendo-app.json.
DEFAULT_AUTHORIZE_URL = 'https://nextendo.network/api/oauth/authorize'
DEFAULT_TOKEN_URL = 'https://nextendo.network/api/oauth/token'

#: The redirect port. A *registered* client's redirect URI is compared as a string
#: (`nextendo-account/oauth.go:299`), unlike the built-in emulator client, which may use any
#: loopback port: so this number must be the one typed on nextendo.network/developers.
DEFAULT_REDIRECT_PORT = 47821


@dataclass(frozen=True)
class Config:
    authorize_url: str
    token_url: str
    client_id: str
    scope: str = 'openid profile'
    redirect_port: int = DEFAULT_REDIRECT_PORT
    #: Only for a registered (confidential) app: Nextendo's registration always issues a secret
    #: (`oauth.go:522-542`). A secret inside an app can be read out of it, so the right fix is a
    #: public client; until then the file holding it stays on the player's machine, mode 0600.
    client_secret: str | None = None

    @property
    def redirect_uri(self) -> str:
        return f'http://127.0.0.1:{self.redirect_port}/callback'

    @classmethod
    def load(cls, path: str | Path) -> 'Config':
        """Read ``nextendo-app.json``: the app the player registered on Nextendo.

        Only ``client_id`` is required; the URLs and the port fall back to the values above, so
        a file carrying just the id from the portal works.
        """
        p = Path(path)
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except FileNotFoundError as e:
            raise AuthError(
                f'{p} is missing: register an application on nextendo.network/developers and '
                'save its client id there') from e
        except json.JSONDecodeError as e:
            raise AuthError(f'{p} is not valid JSON: {e}') from e
        if not d.get('client_id'):
            raise AuthError(f'{p} is missing client_id')
        port = d.get('redirect_port') or cls.redirect_port
        try:
            port = int(port)
        except (TypeError, ValueError) as e:
            raise AuthError(f'{p}: redirect_port must be a number, not {port!r}') from e
        return cls(str(d.get('authorize_url') or DEFAULT_AUTHORIZE_URL),
                   str(d.get('token_url') or DEFAULT_TOKEN_URL),
                   str(d['client_id']),
                   str(d.get('scope') or cls.scope),
                   port,
                   str(d['client_secret']) if d.get('client_secret') else None)


# ---------------------------------------------------------------- loopback redirect

class LoopbackServer:
    """One redirect, on a port this process owns, then closed.

    ``serve_once`` blocks until the browser reaches ``/callback`` (or the timeout), so no
    background thread is needed and nothing can outlive the sign-in window.
    """

    def __init__(self, port: int = 0, expect_state: str | None = None):
        self.expect_state = expect_state
        self.answer: dict[str, str | None] = {}
        self.last_path: str | None = None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                                        # noqa: N802 - stdlib API
                outer.last_path = self.path
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                outer.answer = {'code': (q.get('code') or [None])[0],
                                'state': (q.get('state') or [None])[0],
                                'error': (q.get('error') or [None])[0],
                                'error_description': (q.get('error_description') or [None])[0]}
                ok = bool(outer.answer['code'])
                self.send_response(200 if ok else 400)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(('Fatto: puoi chiudere questa finestra e tornare a NX2Apple.'
                                  if ok else 'Nextendo non ha completato l’accesso.').encode('utf-8'))

            def log_message(self, format, *args):                     # noqa: A002 - stdlib API
                pass

        self.httpd = http.server.HTTPServer(('127.0.0.1', port), Handler)
        self.httpd.timeout = 0.2
        self.port = self.httpd.server_address[1]

    @property
    def redirect_uri(self) -> str:
        return f'http://127.0.0.1:{self.port}/callback'

    def wait(self, timeout: float = 300.0) -> dict[str, str | None]:
        """Serve requests until the redirect arrives; raises when it does not."""
        deadline = time.monotonic() + timeout
        while not self.answer and time.monotonic() < deadline:
            self.httpd.handle_request()
        if not self.answer:
            raise AuthError('the sign-in window was closed before Nextendo answered')
        self.check()
        return self.answer

    def check(self) -> None:
        if self.answer.get('error'):
            detail = self.answer.get('error_description')
            code = self.answer['error']
            raise AuthError(f'Nextendo refused the sign-in: {code}' + (f' ({detail})' if detail else ''))
        if self.expect_state is not None:
            check_state(self.answer.get('state'), self.expect_state)
        if not self.answer.get('code'):
            raise AuthError('Nextendo answered without an authorization code')

    def close(self) -> None:
        self.httpd.server_close()


# ---------------------------------------------------------------- the flow

def sign_in(cfg: Config, open_browser=webbrowser.open, timeout: float = 300.0) -> dict:
    """PKCE, browser, loopback answer, token exchange. Returns the token JSON."""
    verifier = new_verifier()
    state = secrets.token_urlsafe(16)
    try:
        srv = LoopbackServer(port=cfg.redirect_port, expect_state=state)
    except OSError as e:
        raise AuthError(f'port {cfg.redirect_port} is busy ({e}): close the other NX2Apple window '
                        'and try again — the registered redirect URI names this port') from e
    try:
        url = cfg.authorize_url + '?' + urllib.parse.urlencode({
            'response_type': 'code',
            'client_id': cfg.client_id,
            'redirect_uri': srv.redirect_uri,
            'scope': cfg.scope,
            'state': state,
            'code_challenge': challenge_for(verifier),
            'code_challenge_method': 'S256',
        })
        if not open_browser(url):
            # Some browsers hand off to a running instance and report False; the port is
            # still open, so the answer may yet arrive. Only a closed window is a failure,
            # and wait() reports that with the message the player needs.
            pass
        answer = srv.wait(timeout)
    finally:
        srv.close()
    return exchange_code(cfg, str(answer['code']), verifier, srv.redirect_uri)


def exchange_code(cfg: Config, code: str, verifier: str, redirect_uri: str) -> dict:
    form = {
        'grant_type': 'authorization_code',
        'code': code,
        'code_verifier': verifier,
        'client_id': cfg.client_id,
        'redirect_uri': redirect_uri,
    }
    if cfg.client_secret:
        form['client_secret'] = cfg.client_secret
    req = urllib.request.Request(cfg.token_url, data=urllib.parse.urlencode(form).encode('ascii'),
                                 headers={'Content-Type': 'application/x-www-form-urlencoded',
                                          'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        if 'invalid_client' in detail:
            # The app was registered as confidential (`oauth.go:386-392`: a non-empty SecretHash
            # makes the secret mandatory), while a desktop app should be the public/PKCE kind.
            raise AuthError(
                'Nextendo wants a client secret for this app: either put the secret the portal '
                'showed once into nextendo-app.json, or register the app as "public application '
                '(desktop / mobile, PKCE)" so there is none') from e
        raise AuthError(f'Nextendo refused the token exchange (HTTP {e.code}): {detail}') from e
    except OSError as e:
        raise AuthError(f'could not reach Nextendo to exchange the code: {e}') from e
    try:
        token = json.loads(body)
    except json.JSONDecodeError as e:
        raise AuthError('Nextendo answered with something that is not JSON') from e
    if not token.get('access_token'):
        raise AuthError('Nextendo answered without an access token')
    token.setdefault('obtained_at', int(time.time()))
    return token


# ---------------------------------------------------------------- where it is kept

def app_data_dir() -> Path:
    """Per-user data folder, the same one the launchers use for their own files."""
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / 'NX2Apple'


def default_token_path() -> Path:
    return app_data_dir() / 'nextendo-token.json'


def default_app_path() -> Path:
    return app_data_dir() / 'nextendo-app.json'


def write_token_file(token: dict, path: str | Path) -> Path:
    """Write the token owner-only; a token in a world-readable file is a leak."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(token), encoding='utf-8')
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass                                   # a filesystem without modes (a FAT drive)
    return p


def read_token_file(path: str | Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def nextendo_env(token_file: str | Path | None) -> dict[str, str]:
    """The environment a game gets; empty when there is no token, so nothing changes."""
    if not token_file:
        return {}
    return {ENABLE_ENV: '1', TOKEN_ENV: str(token_file)}


# ---------------------------------------------------------------- helpers for the UI

def describe(token: dict | None) -> str:
    """One line for the settings row: who is signed in, or that nobody is."""
    if not token:
        return 'Non connesso a Nextendo'
    name = token.get('name') or token.get('username') or token.get('email')
    return f'Connesso a Nextendo come {name}' if name else 'Connesso a Nextendo'
