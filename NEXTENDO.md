# Nextendo, in this fork

This fork teaches NX2Apple to speak to the [Nextendo Network](https://nextendo.network) — a
community replacement for Nintendo's online services — so a game that is built here can go online
from the iPhone. It is the only change of substance over upstream; everything else follows
`main`.

Nothing here contains or downloads Nintendo software, keys or data: it reads the game you dumped,
and it talks to a server you have an account on.

## The switch

One variable turns the whole seam on:

| variable | what it does |
|---|---|
| `DM_NEXTENDO=1` | everything below; unset, the host behaves exactly as upstream |
| `DM_NEXTENDO_HOSTS` | `<ip> <name>` one per line, `*.suffix` allowed — the names the game resolves to Nextendo's servers |
| `DM_NEXTENDO_HOSTS_FILE` | the same map from a file, for a per-game copy |
| `DM_NEXTENDO_DOMAIN` | the domain the dauth hosts hang off (`dauth.<domain>`, `aauth.<domain>`) |
| `DM_NEXTENDO_DEVICE_ID` | the client id device tokens are asked for; a UUID, stable per install |
| `DM_NEXTENDO_HTTPS_PORT` | where the dauth chain is reached (default 443) |
| `DM_NEXTENDO_TLS_INSECURE` | accepts an untrusted certificate — for the mocks in CI, never for real |

## What is built

The layers below the game's own code, in the order a game uses them:

1. **Sockets.** With the switch on, `nn::socket` is real: TCP in and out (`0063`), UDP that actually
   leaves the process (`0065`), and `GetAddrInfo` that answers from the map first and the system
   resolver second (`0064`).
2. **TLS.** `nn::ssl` over SecureTransport, driven through the game's own socket descriptor so its
   blocking mode, timeouts and errno stay where they already are (`0066`).
3. **The dauth chain.** Challenge, device tokens, edge tokens, application auth token — the four
   calls `nx-dauth` serves, as a host module (`0070`).
4. **The network service account.** `nn::account`'s six online entries, which used to answer "no",
   now answer from that chain: available, an id stable per device, and the token itself (`0073`).
   They need `DM_NEXTENDO_APP_ID`, the title id the launcher already knows.

## What is proven, and how

`git log` never lies about this, so it is worth saying where to look. The fork's macOS workflow
(`.github/workflows/nextendo-host-checks.yml`) runs on every push:

| test | proves |
|---|---|
| `t_nextendo_map` | the map: exact names, wildcards, case, a bad address never becomes a rule |
| `t_nextendo_gai` | `GetAddrInfo` builds FreeBSD-shaped `addrinfo`s from the map, then from the resolver |
| `t_net_tcp` | a real listener, a message each way |
| `t_net_udp_remote` | one datagram through a real listener, and the switch-off refusal |
| `t_ssl_local` | a TLS handshake and a message each way, against a server the workflow starts |
| `t_dauth_local` | the whole dauth chain against a mock, and the switch-off refusal |

## What is not built

- **The `nn::dauth` service surface.** `nn::account` is bound (`hle_account.c` always had the real
  names), but the dauth entry points a game calls directly are not written: no symbol of them has
  been seen yet, and guessing mangled names is how a day disappears.
- **The iPhone shell.** `frontend-ios` has no Nextendo settings yet: the token, the switch and the
  map have to reach the engine when a game is launched. Today the card exists only in the Qt
  launcher.
- **The licence calls** (`dragons`) that follow the auth token.
- **Verification against the real servers.** Every test above speaks to a mock; nothing here has
  been run against Nextendo's own hosts from this machine.

## Where the thinking is

`notes/nextendo-spike.md` — the research: no mTLS on the dauth chain (their `tls.NoClientCert`),
what `aauth` returns, the `nex_token` question, and the order of work.
