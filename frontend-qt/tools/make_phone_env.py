"""The environment the iPhone shell needs, built from the token the launcher wrote.

    "C:\\...\\python.exe" frontend-qt\\tools\\make_phone_env.py 0100965017338000 > env-for-phone.txt

Prints `KEY=value` lines for the app's `Documents/env.txt`. The game token lasts 24 hours, so this
is a daily command while playing online: it trades the OAuth access token for a fresh one and never
prints it anywhere but the file the caller redirects into.

A line starting with `#` is not known to the shell's env parser, so everything explanatory goes to
stderr, which the redirect leaves out.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nspb_qt import nextendo_auth as na  # noqa: E402


def phone_env(title_id: str, token: dict, cfg: na.Config) -> dict[str, str]:
    """The four switches the phone needs, in the order a reader wants them."""
    nex = na.fetch_nex_token(cfg, token)
    return {
        'DM_NEXTENDO': '1',
        'DM_NEXTENDO_APP_ID': title_id,
        **na.nex_env(nex),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Print the env.txt lines for online play on the phone')
    ap.add_argument('title_id', help='the game, e.g. 0100965017338000')
    ap.add_argument('--token', help='token file (default: the one the launcher wrote)')
    ap.add_argument('--app', help='nextendo-app.json (default: the one next to the token)')
    a = ap.parse_args(argv)

    token = na.read_token_file(a.token or na.default_token_path())
    if not token:
        print('no Nextendo token: sign in from the launcher first', file=sys.stderr)
        return 1
    try:
        cfg = na.Config.load(a.app or na.default_app_path())
        env = phone_env(a.title_id, token, cfg)
    except na.AuthError as e:
        print(str(e), file=sys.stderr)
        return 1

    for key, value in env.items():
        print(f'{key}={value}')
    print(f'# a game token for pid {env.get("DM_NEXTENDO_PID")}, valid 24 hours', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
