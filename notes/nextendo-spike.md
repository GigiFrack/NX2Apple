# Nextendo integration — spike results (what a client must present)

Read from source, 2026-11-17. Clones: `NextendoNetwork/{nextendo-nex,nx-dauth,nextendo-account,
nextendo-nncs,sni-router}` at `main`. Nothing here is guessed from a README: every claim carries the
file and line it came from.

## Verdict

**GO**, with one blocked dependency that is a conversation, not code: a token that `nextendo-nex`
accepts (`nex_token`) is handed out by `nextendo-account` **only to its built-in emulator client id**.
Everything else about the chain is reachable from a native port: no client certificates, plain TLS on
`:443`, DNS-level redirection, and the game's own NEX code doing the protocol.

## 1. No client certificate, anywhere

| Where | What it says |
|---|---|
| `nx-dauth/main.go:14` | "TLS configuré ClientAuth=NoClientCert : la console présente son cert PRODINFO (mTLS dauth)…" |
| `nx-dauth/main.go:416-419` | `tls.Config{… ClientAuth: tls.NoClientCert // dauth est en mTLS : on ignore le cert client}` |
| `nextendo-nex/proxyproto.go:78` | `tls.NewListener(&proxyListener{ln}, &tls.Config{Certificates: []tls.Certificate{cert}})` — no `ClientAuth`, so Go's default `NoClientCert` |

Consequence: the host needs a **server-authenticated** TLS client, nothing else. mbedTLS is enough;
no device certificate, no `PRODINFO`, no self-signed client chain to generate.

## 2. What to redirect (the map `dm_nd_lookup` needs)

`sni-router` routes by the **SNI the client sends**, so the retail hostname must survive into the TLS
handshake: the map has to work at name-resolution time and must not rewrite SNI. `sni-router/main.go:6-11`
and `:81-91` give the names it knows:

| Hostname (what the game asks for) | Reaches |
|---|---|
| `g2b309e01-….srv.nintendo.net` | Mario Kart 8 Deluxe auth |
| `g23380901-….srv.nintendo.net` | Super Smash Bros. Ultimate auth |
| `g25c08801-….srv.nintendo.net` | ARMS auth |
| `g2ee2e300-….srv.nintendo.net` | Animal Crossing: New Horizons auth |
| `*.ndas.srv.nintendo.net` | `nx-dauth` |
| `*.dragons.nintendo.net` | `nx-dauth` (BCAT / "dragons") |
| `demonware.net` | the CoD-class backend |
| `nncs1-lp1.n.n.srv.nintendo.net`, `nncs2-lp1.n.n.srv.nintendo.net` | NAT check (`nextendo-nncs/main.go` header comment) |

The **secure** server's address is not in this table on purpose: the game learns it from the auth
server's `LoginEx` answer, so no map entry is needed for gameplay traffic.

## 3. The dauth chain is plain HTTPS with JSON

`nx-dauth/main.go` header comment, paths verbatim:

```
dauth-lp1.ndas.srv.nintendo.net    POST /v8/challenge, /v8/device_auth_tokens, /v8/edge_tokens
aauth-lp1.ndas.srv.nintendo.net    POST /v5/application_auth_token
aauth.hac.lp1.ndas.srv.nintendo.net
acert-lp1.ndas.srv.nintendo.net    GET  /keys   (JWKS that verifies the application token)
dcert-lp1.ndas.srv.nintendo.net    GET  /keys   (JWKS that verifies the device token)
dragons.hac.lp1.dragons.nintendo.net
dragonst.hac.lp1.dragons.nintendo.net
```

So Fase 4's HTTP work is a handful of POSTs with JSON bodies, over the TLS of Fase 3 — not a binary
protocol.

## 4. The token: `nx2.…`, and who is allowed to get one

- Format (`nextendo-account/main.go:848-869`): `nx2.<b64url(pid.username.expiry)>.<b64url(hmac)>`;
  the auth server recognises the prefix and validates it locally.
- **It is only returned to one client id.** `nextendo-account/oauth.go:155`:
  `const emulatorClientID = "nextendo-emulator"`, a *public* client (no secret, PKCE + loopback
  redirect required, `oauth.go:153-167`). `oauth.go:415-427`:

  ```go
  if clientID == emulatorClientID {
      acct, err := s.store.ByPID(d.pid)
      …
      writeJSON(w, http.StatusOK, map[string]any{
          "token_type": "Bearer",
          "nex_token":  signNexToken(acct.PID, acct.Username),
          "account":    acct.Public(),
      })
      return
  }
  ```

  Any other client gets `{access_token, expires_in, scope}` (`oauth.go:429-435`) — an OAuth token for
  `/api/oauth/userinfo`, **not** something `nextendo-nex` will accept as a login.
- The password route also emits `nex_token` (`main.go:1071-1075`, `/api/login`), but it is
  e-mail + password behind `turnstileGuard` (`main.go:1043`), and the developers page is explicit that
  players type their password on `nextendo.network` and never in the app. Not a path.
- Public clients are validated exactly as this repo's `nextendo_auth.py` implements them: loopback
  redirect accepted with any port (`oauth.go:169-179`, `:295-301`), `code_challenge` mandatory
  (`oauth.go:402-411`), S256 compared against `base64.RawURLEncoding(sha256(verifier))`.

## 5. What this changes in the plan

1. **Fase 1 is unaffected and worth shipping**: sign-in works with any registered app, and the
   `identity`/`friends` scopes give the launcher a name and a friend list.
2. **Fase 4 reads `nex_token`, not `access_token`.** `nextendo_auth.write_token_file` stores the whole
   answer, so both are there; the host must be told which one is the NEX login token. Proposed: read
   `nex_token` and fall back to nothing (a build without it keeps the offline branches) — never send
   an OAuth access token where a NEX token is expected and let the server reject it obscurely.
3. **The blocker is one sentence to Nextendo**: *"NX2Apple is a native port (like Ryujinx-Nextendo)
   and needs a public OAuth client whose token answer carries `nex_token` — either let us use the
   `nextendo-emulator` client id, or add a client/scope that does the same."* Without it, in-game
   online cannot work at all, however much host code is written.
4. **The product is the iPhone app, not the Mac host.** The port's target for this work is the
   `DmShell.app` IPA started on the phone: the HLE changes land in the iOS build, the acceptance test
   is a game going online on the device, and `dmhost` on a Mac is only a debugging shortcut for
   whoever has one. The UI that matters first is therefore `frontend-ios` (`ShellSettingsView`,
   `GameSettingsStore`), not the Qt launcher.
5. **Open question to confirm with them/their emulator**: that the `nex_token` is what the host must
   return from `nn::account::LoadNetworkServiceAccountIdTokenCache` (the SDK call games feed to
   `LoginEx`). The name and the flow strongly suggest it; the emulator's source is the proof, and it
   is not public (the `Ryujinx-Nextendo` README link 404s).

## 6. What is verifiable without a human

- `nextendo_auth.py`: PKCE, loopback, state check, token file, environment — 15 tests, all local
  (`frontend-qt/tests/test_nextendo_auth.py`).
- The hostname map is written: `dm/host/hle_nextendo.{c,h}` + `dm/host/tests/t_nextendo_map.c`, carried
  as `patches/0062`. Its logic is verified off-Darwin (built with gcc against the same source, every
  assertion in the test file passes, `dm_nd_enabled` included in the off case). The in-tree run is
  still owed: the whole host suite is macOS-only (it needs `pthread_cond_timedwait_relative_np` and
  clang's `naked` attribute), so `make -C dm/host/tests build/t_nextendo_map && ./.../t_nextendo_map`
  has to happen on a Mac or in CI before the patch is trusted.
- The redirect table above, from their own source.

## 7. What needs a human

1. A Nextendo account and the `nex_token`-capable client id (section 5.3) — **the blocker**.
2. A yes/no from Nextendo on a third-party port speaking NEX (the licence is PolyForm Shield:
   "not use it to provide a product that competes", and a free port is not that, but it is their
   call, not ours).
3. The real hostname/IP of the Nextendo deployment to fill the map (their public DNS answers; a
   `dig` on `nextendo.network` gives the web host, not the game hosts).
