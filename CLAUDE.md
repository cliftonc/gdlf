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
    amapi/                  # Android Management API: GCP creds + enterprise.json
    windows/                # Windows enrolment: bundled WG MSI + stashed .ppkgs
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

# MDM (Apple iOS, optional — see docs/setup-apple-mdm.md):
./gdlf apns ...    # APNs MDM Push Cert workflow (csr → submit → decrypt)
./gdlf mdm-ca ...  # gdlf MDM signing CA (issues per-device identity certs)

# MDM (Android via Android Management API, optional — see docs/setup-android-mdm.md):
./gdlf amapi ...   # AMAPI setup: GCP service-account + EMM enterprise signup

# Windows (signed Provisioning Package, optional — see docs/setup-windows-mdm.md):
./gdlf windows ... # download the WG MSI; check signing CA — no live channel
```

End-to-end MDM setup walk-throughs:
- [docs/setup-android-mdm.md](docs/setup-android-mdm.md) — Android (~15 min, simpler)
- [docs/setup-apple-mdm.md](docs/setup-apple-mdm.md) — iOS (~30 min, more pieces)
- [docs/setup-windows-mdm.md](docs/setup-windows-mdm.md) — Windows (~10 min, one-shot .ppkg)

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

* **HTTPS interception is splice-by-default, MITM-opt-in.** mitmproxy's
  `tls_clienthello` hook decides per-flow: SNIs in the kid's
  `mitm_inspect_hosts` (plus `INSPECT_GLOBAL_DEFAULTS` in `bulk_cdns.py`,
  currently just YouTube / googlevideo) get terminated and decrypted so
  URL-path rules fire. Everything else is `ignore_connection = True` —
  TLS bytes are tunneled untouched. This means pinned-cert apps Just
  Work (no CA validation = nothing to reject), and `tls_failed_client`
  fires rarely; when it does, it's actionable (the parent inspect-listed
  a pinned domain). The earlier "MITM-everything, exempt-on-failure"
  model produced runaway passthrough lists and is gone.
* **DoH/DoT outbound is dropped** at the nftables sidecar for a curated
  list of well-known resolver IPs (Cloudflare/Google/Quad9/OpenDNS/
  AdGuard/NextDNS/Mullvad on :443 and :853 — see `DOH_DOT_IPS` in
  `nftables/reconcile.py`), and AdGuard returns NXDOMAIN for the
  Firefox canary `use-application-dns.net`. Without this, a kid
  enabling Chrome's "Use secure DNS → Cloudflare" silently bypasses
  every filter. MDM-enrolled devices (iOS supervised, Android Device
  Owner via AMAPI, Windows with locked WG service ACL + kill-switch)
  also have DoH/DoT denied at the OS layer — this nftables drop is
  the safety net for non-MDM / pre-enrolment.
* **MDM is containment, not guardrail, on managed platforms.** iOS
  supervised: WireGuard always-on (`OnDemandUserOverrideDisabled`),
  CA system-trusted, no VPN-creation/profile-install restrictions.
  Android Device Owner: same plus `alwaysOnVpnPackage` lockdown. Windows:
  per-tunnel WG service ACL'd, kernel kill-switch, kid is Standard User.
* **QUIC (UDP/443) is blocked for `mitm_clients`** so browsers fall back
  to TCP/TLS and SNI/path inspection still works. Without this, every
  QUIC-capable destination escapes to UDP and we lose visibility.
* **Inspecting pinned apps is impossible at the network layer.** If you
  add `instagram.com` to `mitm_inspect_hosts`, Instagram will fail.
  Use MDM allowlists or OAuth-based account monitoring for visibility
  into those.
* **Per-kid identity uses the WG peer IP** — there's no per-request auth.
  Trusted because each device is enrolled by a parent.
* **ECH is a slow-motion threat to SNI visibility.** When Encrypted
  Client Hello deploys broadly, SNI-only filtering breaks. Today only
  ~4–9% of top sites. Chrome/Firefox both disable ECH when a trusted
  managed CA is present, so the MDM-pushed CA path is the durable
  long-term answer.
