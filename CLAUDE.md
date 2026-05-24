# gdlf — architectural overview

A self-hosted parental-control appliance. Kids' devices connect via WireGuard,
their traffic is intercepted at three levels (DNS / URL / firewall), and a
parent-facing dashboard governs the policy.

The original design discussion lives in [`docs/design.md`](docs/design.md).

## The big picture

```
   Kid device (iOS / Android / Chromebook)
            │  WireGuard tunnel  (always-on)
            ▼
   ┌────────────────────────────────────────────┐
   │  wg container's network namespace          │
   │  ┌───────┐  ┌────────┐  ┌─────────┐  ┌───┐ │
   │  │ wg0   │  │AdGuard │  │mitmproxy│  │blk│ │
   │  │       │  │ :53/:80│  │  :8080  │  │8888/9│
   │  └───────┘  └────────┘  └─────────┘  └───┘ │
   │              ▲              ▲              │
   │              │              │              │
   │       (nftables sidecar: routing + DNAT +  │
   │        schedule drops, all inside this NS) │
   └────────────────────────────────────────────┘
            │ rules-svc lives on the gdlf bridge
            │ and talks to all of the above
            ▼
   ┌────────────────────────────────────────────┐
   │  rules-svc  (FastAPI JSON API + React SPA) │
   │  • reads/writes kids.yaml (source of truth)│
   │  • renders wg0.conf, reloads wg container  │
   │  • syncs AdGuard per-client config         │
   │  • answers mitmproxy's per-request decision│
   │  • records events to SQLite, prunes daily  │
   │  • serves the parent SPA (Vite/HeroUI)     │
   └────────────────────────────────────────────┘
```

## Why AdGuard + mitmproxy + blockpage share wg's netns

Originally each lived on the `gdlf` Docker bridge with its own IP. That meant
DNS / HTTPS traffic emerging from `wg0` had to be DNAT'd into the bridge — and
nftables' MASQUERADE rule rewrote the source IP to `10.42.0.2` (the wg
gateway). AdGuard and mitmproxy then couldn't tell *which kid* sent a request.

Putting them inside wg's netns (`network_mode: service:wg`) means:

* Traffic from `wg0` reaches each service with the kid's real `10.13.13.x`
  source IP intact (no SNAT needed in between).
* Each service binds `0.0.0.0:<port>` and is reachable on every interface
  in that NS — including `eth0` (`10.42.0.2`), so rules-svc on the gdlf
  bridge can still reach AdGuard's admin API at `http://10.42.0.2:80`.
* nftables (also in this NS) sees `wg0` directly and can `iifname "wg0"`
  rules without worrying about bridged forwarding.

The trade-off: when the `wg` container restarts, all containers using
`network_mode: service:wg` get orphaned and lose network. Restart wg → also
restart adguard / mitmproxy / blockpage / nft.

## Source-of-truth model

`config/kids.yaml` is **the** source of truth for every policy decision. The
schema lives in `services/rules-svc/src/gdlf/schema.py` (pydantic v2).

Every component either:

* **Reads it** (nftables sidecar — schedule + per-IP sets;
  rules-svc — everything; AdGuard sync loop)
* Or **asks rules-svc** which uses kids.yaml (mitmproxy addon —
  per-request via `/api/decision`).

The dashboard writes back to `kids.yaml` via `store.mutate()` which is
in-place + file-locked (not atomic-rename, because Docker single-file
bind-mounts break rename).

`config/state/gdlf.db` is **ephemeral** — request events, handshake
timestamps, alert delivery log. Pruned hourly (7d / 200k cap by default,
tunable via `RETENTION_DAYS` / `MAX_EVENTS`). Safe to wipe.

## Repo layout

```
docker-compose.yml          # the stack
gdlf                        # ./gdlf up | logs | rebuild | apns | mdm-ca | ...
.env                        # WG_HOST, passwords, retention, MDM_*, etc.
config/
  kids.example.yaml         # committed template — `./gdlf init` seeds kids.yaml from this
  kids.yaml                 # *** source of truth *** (gitignored)
  wg/                       # WireGuard runtime state (server keys, wg0.conf)
  adguard/                  # AdGuard runtime state
  mitmproxy/                # mitmproxy CA + state
  state/                    # rules-svc SQLite + key material
    apns/                   # Apple Push cert (push.pem) + helpers (`./gdlf apns`)
    mdm-ca/                 # signing CA for per-device MDM identity certs
    caddy/                  # Caddy ACME + state (Let's Encrypt cert)
nftables/                   # firewall sidecar (Alpine + nft + Python)
scripts/
  gen-ca.sh                 # one-time mitmproxy CA generation
  udp-forward.py            # macOS dev: Colima UDP forwarder
services/
  rules-svc/                # FastAPI dashboard + control plane (Python)
  mitmproxy/                # mitmproxy image + httpx
  blockpage/                # tiny dual-port block-page HTTP server
  proxy/                    # Caddy TLS+mTLS front-door for the /mdm/* endpoints
```

Each subdirectory has its own `CLAUDE.md` with the specifics.

## Operating

```
./gdlf init        # one-time: generate mitmproxy CA + empty kids.yaml
./gdlf up          # start stack (docker compose up -d)
./gdlf ps          # status
./gdlf logs [svc]  # follow logs
./gdlf rebuild     # full no-cache rebuild + recreate
./gdlf down        # stop

# MDM (Apple iOS, optional — see services/rules-svc/CLAUDE.md and services/proxy/CLAUDE.md):
./gdlf apns ...    # APNs MDM Push Cert workflow (csr → submit → decrypt)
./gdlf mdm-ca ...  # gdlf MDM signing CA (issues per-device identity certs)
```

`./gdlf up` automatically enables the `mdm` compose profile (which adds the
Caddy front-door for /mdm/*) when `MDM_HOSTNAME` is set in `.env`.

The `gdlf` script auto-detects either `docker compose` (plugin) or the
standalone `docker-compose` binary.

## Common-failure cheat sheet

| Symptom                                  | Likely cause                                                  | Where to look                                            |
| ---------------------------------------- | ------------------------------------------------------------- | -------------------------------------------------------- |
| Phone connects, nothing resolves         | Android Private DNS bypassing :53                             | Phone Settings → Network → Private DNS → Off             |
| Handshake completes, no traffic flows    | Kid's IP in nft `blocked_clients` (schedule out-of-window)    | Check kid's `schedule` in `kids.yaml`                    |
| `Unable to connect` on a DNS-blocked URL | AdGuard blocking-mode is NULL IP, not custom `10.13.13.254`  | AdGuard UI → Settings → DNS settings → Blocking mode     |
| URL block rule "doesn't fire"            | Browser cache served the page; no request reached mitmproxy   | Hard refresh on device                                   |
| mitmproxy stops responding               | FD exhaustion                                                 | `ulimits.nofile` in compose; restart `gdlf-mitm`         |
| AdGuard sees every query as `10.42.0.2`  | adguard not in wg netns (regression)                          | compose: `network_mode: "service:wg"` on adguard service |
| nft sees no `wg0` interface              | nft netns orphaned by wg restart                              | Restart `gdlf-nft` (and adguard/mitm/blockpage)          |
| Events not appearing after block         | Addon crashed silently (template / KeyError)                  | `docker logs gdlf-mitm` for tracebacks                   |

## Pragmatic boundaries (intentional)

* **DoH/DoT bypass is not fought** — kids who actively want to escape can
  set Private DNS to `dns.google`. We could `nft drop` :853 outbound to
  force fallback, but the parent's threat model here is guardrail not
  containment. **EXCEPT** for iOS devices enrolled in MDM (see
  `services/rules-svc/CLAUDE.md` § MDM): for those, the WireGuard tunnel
  is always-on and non-removable, the CA is system-trusted, and adding
  another VPN / profile is restricted at the OS level — so the guardrail
  becomes containment for that platform.
* **QUIC (UDP/443) is blocked for `mitm_clients`** so browsers fall back
  to TCP/TLS and mitmproxy can actually see traffic. Devices without the
  CA still get QUIC; we just can't inspect them beyond DNS / SNI.
* **mitmproxy can't decrypt cert-pinned apps** (TikTok, Instagram, banking
  apps) — those show up as SNI-only events (filtered from default view).
* **Per-kid identity uses the WG peer IP** — there's no per-request auth.
  Trusted because each device is enrolled by a parent.
