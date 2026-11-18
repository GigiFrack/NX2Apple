# Nextendo: what the official documentation says

Source: <https://wiki.nextendo.network/integration> (Integration guide) and
<https://wiki.nextendo.network/developers> (API/authentication reference), read 2026-11-18, plus
`src/Ryujinx.HLE/HOS/Services/Account/Acc/AccountService/ManagerServer.cs` from
<https://git.nextendo.network/NextendoNetwork/Ryujinx-Nextendo> (the reference client).

These notes correct two things the spike assumed wrongly (see `nextendo-spike.md`).

## 1. A third-party app CAN get the game token

`GET https://nextendo.network/api/nex-token`, `Authorization: Bearer <oauth access token>`,
`X-Nextendo-Client-Id: <client id>` →

```
{"nex_token": "nx2...", "pid": <number>, "friend_code": "...", "username": "...", "expires_in": 86400}
```

It requires the **`game.matchmaking` scope**. The spike concluded the token was only issued to the
built-in emulator client; that was wrong — it is issued to any registered application that asked for
that scope and still has the player's consent.

The token lasts 24 hours, is opaque, and only opens game servers plus
`/api/session-status`, `/api/online-status`, `/api/nex-session` for its own account.

## 2. The game does not log into BAAS: the client builds the id_token

Switch games ask the system for a BAAS id_token and forward it inside the NEX login extra data. An
emulator does not contact any BAAS host: it builds and signs the JWT itself and returns it from the
acc service (`GetIdTokenData`). Claims (RS256, `exp` = `iat` + 3 h):

| claim | value |
|---|---|
| `iss` | `https://e0d67c509fb203858ebcb2fe3f88c2aa.baas.nintendo.com` |
| `aud` | `ted9e2f05d286f7b8` |
| `typ` | `id_token` |
| `sub` | random 16-byte hex |
| `jti` | UUID |
| `di`, `bs:did` | random 16-byte hex |
| `sn` | `XAW10000000000` |
| `hm` | true unless online play is blocked (Splatoon 2 reads it as the NSO membership) |
| `app_id` | the title id, only for the NSO classics and two titles sharing another tenant |
| `tv` | installed game version |
| `nnex` | **the nx2 token** |

Plus, in the reference client, a `jku` claim pointing at
`https://e0d67c509fb203858ebcb2fe3f88c2aa.baas.nintendo.com/1.0.0/certificates` and `kid`
`nextendo-baas-key-1`.

Signing key: the reference tree does **not** bundle one; official builds inject it via
`NEXTENDO_BAAS_SIGNING_KEY` (or `nextendo_baas.pem`). Without it the client signs with a throwaway
key — every game still works except Splatoon 2, which verifies the signature locally against the
JWKS at `jku`.

The account id (NSA) the game sees is the `pid` from step 1. Exception: Animal Crossing must get the
account's BAAS user id (`nsa` field of `GET /api/me`).

## 3. Network redirection (this replaces the "guess the hostnames" plan)

On the Horizon side, in this order: literal IP → exact entry of the emulated SD hosts file
(`/atmosphere/hosts/default.txt`) → the built-in rules → wildcard entries of the hosts file → real
DNS.

- Redirect the **whole namespace**: `*.nintendo.net`, `*.nintendo.com`, `*.nintendowifi.net`,
  `*.nintendo.co.jp`, plus the third-party hostnames of the supported titles.
- Before DNS, `accounts.nintendo.com` and `api.accounts.nintendo.com` are rewritten to
  `e0d67c509fb203858ebcb2fe3f88c2aa.baas.nintendo.com`.
- Everything is **TCP 443** on the server and is routed by **SNI**. When a game opens TLS to an IP
  without a hostname, send as SNI the last hostname resolved to that address.
- **NEX carries PRUDP over a WebSocket** (HTTP/1.1 upgrade), so NEX needs no UDP and no `nn::nex`
  implementation: the game's own client does it over the TLS stream.
- ALPN: offer `http/1.1` by default; keep `h2` only for hosts containing `npln` or `gs.nintendo.net`
  (NPLN is gRPC over HTTP/2).
- The NAT test (Pia) still sends UDP.

## 4. TLS

Two layers that must not be mixed:

- the client's own API calls to `https://nextendo.network`: public certificate, always verified;
- the game's TLS inside the emulated ssl service: Nextendo's own roots, installed in the client's
  trust store (not the OS one), verified together with the hostname.

Roots (from the guide): `nextendo-network-ca.pem`, `nextendo-npln-class2-ca-g3.pem` (signs
`*.npln.srv.nintendo.net`, gamesync, dgs, dauth/aauth/dcert/acert under `ndas.srv.nintendo.net`,
friends/dragons), `nextendo-jd2022-service-ca.pem`, plus the ones cut off in the capture — take the
full list and fingerprints from the page when implementing.

## 5. Cloudflare fronts `nextendo.network`

A client that sends the stock `Python-urllib/3.x` User-Agent gets **403 with Cloudflare "Error
1010: Access denied"** on `/api/oauth/token` — a body that looks like a refused token and is a bot
filter. Measured from this host: no UA → 403 `error-1010`; `User-Agent: NX2Apple/1.5 (Nextendo
client)` → the request reaches Nextendo, which then answers for its own reasons. Every call the
client makes must name itself, and a 403 must be classified before it is blamed on the scope.

## 6. Session keep-alive

`POST /api/nex-session` as a heartbeat while online; `GET /api/online-status` when a login is refused;
`GET /api/session-status` → `{email_verified, revoked}`; refresh the OAuth token and call
`/api/nex-token` again before the 24 hours run out; `POST /api/oauth/revoke` on sign-out. Routes are
rate limited: honour `Retry-After` on 429.
