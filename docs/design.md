# Family Network Protection Stack — Plan

## Context

Build a self-hosted parental-control "appliance" that runs on a Linux server in the home and protects kids' iOS, Android, and Chromebook devices wherever they are. Each kid's device runs WireGuard always-on, so all their traffic flows through one chokepoint we control. Inside that chokepoint we want:

1. **DNS-level content blocking** (adult/harmful sites, SafeSearch enforcement, app domains)
2. **URL-path-level blocking** via mitmproxy (block specific YouTube videos/channels, specific subreddits, specific search queries — *the whole reason mitmproxy is in the stack, not just for logging*)
3. **Per-kid, per-device schedules** (no internet 21:00–07:00)
4. **App blocking** (TikTok, Discord, etc. — mostly via DNS, some via IP/SNI)
5. **Monitoring + alerts** (dashboard for browsing, push on flagged events)

The original sketch had OPNsense as a layer. That's swapped for `nftables` inside the container — same routing/firewall role, dockerable. The user explicitly chose a *pragmatic* trust posture: don't fight DoH bypass, install mitmproxy CA normally (accept that pinned mobile apps stay opaque), and accept that a determined teen could route around this. The goal is a guardrail for normal use, not adversarial containment.

## Architecture

```
Kid device (iOS / Android / Chromebook)
    │  WireGuard tunnel (always-on)
    ▼
┌─────────────────────────────────────────────────────┐
│ Linux host (greenfield NUC / Proxmox VM)            │
│ ┌─────────────────────────────────────────────────┐ │
│ │ wg0 (inside wg-easy container, net=host)        │ │
│ │  • each device = its own WG peer (own /32 IP)   │ │
│ │  • peer pubkey → kid identity (in rules svc DB) │ │
│ └─────────────────────────────────────────────────┘ │
│                  │ traffic emerges on wg0 subnet    │
│                  ▼                                  │
│ ┌─────────────────────────────────────────────────┐ │
│ │ nftables (host or dedicated container w/ NET_ADMIN)│
│ │  • redirect :53  → AdGuard                      │ │
│ │  • redirect :80/:443 → mitmproxy transparent    │ │
│ │  • schedule enforcement: drop kid-X out-of-hours│ │
│ │  • app IP blocks (Discord voice ranges, etc.)   │ │
│ └─────────────────────────────────────────────────┘ │
│         │                            │              │
│         ▼                            ▼              │
│ ┌──────────────┐            ┌──────────────────┐    │
│ │ AdGuard Home │            │   mitmproxy      │    │
│ │  per-client  │            │  transparent     │    │
│ │  blocklists  │            │  + Python addon  │    │
│ │  SafeSearch  │            │  → events HTTP   │    │
│ └──────┬───────┘            └─────────┬────────┘    │
│        │ query log webhook            │ events      │
│        └──────────┬───────────────────┘             │
│                   ▼                                 │
│ ┌─────────────────────────────────────────────────┐ │
│ │ rules-svc  (Python / FastAPI)                   │ │
│ │  • SQLite: kids, devices, policies, events      │ │
│ │  • dashboard (Jinja + HTMX)                     │ │
│ │  • schedule daemon → reconciles nftables        │ │
│ │  • flag engine → webhook + email on hits        │ │
│ │  • config: kids.yaml (declarative)              │ │
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
        ▲                                ▼
        │ admin web UI                   webhook/email
```

### Component choices

- **WireGuard**: [`wg-easy`](https://github.com/wg-easy/wg-easy) — gives us a clean admin UI for adding peers, exporting QR codes. Each device = one peer, one /32 IP. The IP is the identity key everywhere downstream.
- **AdGuard Home**: official `adguard/adguardhome` image. Per-client config keyed by WG IP. SafeSearch toggle is built-in. Upstream to 1.1.1.1 over DoT.
- **mitmproxy**: official `mitmproxy/mitmproxy` image, transparent mode. Custom Python addon is the **policy enforcement point**:
  - On every request: match against the kid's `url_rules` (host + path + query glob/regex). If a rule says block → return a synthetic `403` with a friendly block-page HTML (and log the attempt). If allow → pass through.
  - On every response: optionally inspect (e.g. flag search-result pages that contain certain terms).
  - Async-POST the event (method, host, path, client IP, decision, status) to `rules-svc` for the dashboard/alerts pipeline.
  - For TLS-decryptable traffic (CA installed): full URL, query string, request body visible — can block by path/query.
  - For pinned apps / no-CA devices: falls back to SNI-only — can only block at host level, equivalent to AdGuard. We mark these events as `sni_only` in the dashboard so it's clear what we can and can't see.
- **rules-svc**: one Python service, FastAPI + Jinja + HTMX. **Dashboard is the primary management UX** — you add kids and enrol devices through the web UI. `kids.yaml` is the persistent source of truth (so it's git-versionable and editable by hand if you want), and SQLite holds only ephemeral data (events, logs, last-handshake timestamps). The dashboard CRUD operations rewrite `kids.yaml` atomically (write-temp-then-rename + file lock), so hand-edits and UI edits never collide. A file watcher on `kids.yaml` reloads in-memory state on change. Drives nftables and AdGuard config via small reconciler loops that read the file.
- **nftables**: runs in its own minimal container with `NET_ADMIN` + `host` networking (or `network_mode: service:wg` to share the WG namespace, depending on routing choice — see *Open questions* below).

### Single language vs. two
Considered Go for `rules-svc` (single static binary appeal). Rejected: the mitmproxy addon *must* be Python, so going Python everywhere keeps the project to one runtime and lets the addon import shared models if needed. FastAPI's single-binary story is fine via uv + a slim image.

## Repository layout

```
gdlf/
  docker-compose.yml        # the whole stack
  gdlf                      # thin CLI wrapper (./gdlf up | logs | add-kid | ...)
  config/
    kids.yaml               # declarative source of truth (see below)
    adguard/                # mounted into AdGuard
    mitmproxy/              # CA cert lives here; export endpoint for QR
  services/
    rules-svc/              # FastAPI app
      pyproject.toml
      src/gdlf/
        api.py              # mitmproxy + adguard webhook endpoints
        dashboard.py        # HTMX views
        scheduler.py        # nftables reconciler (apscheduler)
        flags.py            # alert engine
        models.py           # sqlmodel
        nft.py              # nftables driver
      addons/
        mitm_capture.py     # mitmproxy addon → POSTs events
  nftables/
    Dockerfile              # alpine + nft + entrypoint that watches rules-svc
  scripts/
    gen-ca.sh               # one-time mitmproxy CA generation
    install-ios.md          # walkthrough for iOS cert trust
    install-android.md
    install-chromeos.md
```

### `kids.yaml` (the declarative spec)

```yaml
kids:
  - name: alice
    age: 12
    devices:
      - name: alice-iphone
        platform: ios
        wg_ip: 10.13.13.10
        mitm_ca_installed: false   # affects what we can decrypt
      - name: alice-chromebook
        platform: chromeos
        wg_ip: 10.13.13.11
        mitm_ca_installed: true
    schedule:
      weekday: { allowed: "07:00-21:00" }
      weekend: { allowed: "08:00-22:00" }
    blocklists: [adult, gambling, social-media-aggressive]   # DNS-level (AdGuard)
    blocked_apps: [tiktok, discord]                          # DNS + IP-range
    url_rules:                                               # mitmproxy path-level
      - { action: block, match: "youtube.com/shorts/*" }
      - { action: block, match: "youtube.com/watch", query: "v=(dQw4w9WgXcQ|...)" }
      - { action: block, match: "reddit.com/r/(teenagers|gonewild|...)/*" }
      - { action: block, match: "*/search", query: "q=.*(weed|vape|nicotine).*", flag: true }
      - { action: allow, match: "youtube.com/@KhanAcademy/*" }   # allow-overrides
      - { action: flag,  match: "google.com/search", query: "q=.*(suicide|self.harm).*" }
    keyword_flags: [suicide, self-harm, "how to buy weed"]   # response-body scan
```

`rules-svc` watches this file, reconciles nftables, and syncs AdGuard's per-client config via its REST API. SQLite stores only ephemeral data: request events, AdGuard query log entries, device last-handshake timestamps, alert delivery state.

### Dashboard pages & flows

Primary navigation: **Kids**, **Activity**, **Rules library**, **Settings**.

- **Kids list** (`/kids`): cards for each kid with status (devices online/offline, currently-allowed/blocked-by-schedule, today's flag count). "Add kid" button → modal asks name, age, default schedule template.
- **Kid detail** (`/kids/{name}`): four tabs.
  - *Devices*: list of enrolled devices with platform icon, WG IP, last handshake, "Install mitmproxy CA" status (manual toggle the parent flips after installing). "Add device" button kicks off the enrolment flow (below).
  - *Schedule*: weekday/weekend allowed-hours editor, plus exception dates.
  - *Rules*: blocklists (checkbox picker from the library), blocked apps (multi-select), URL rules (table editor: action / host pattern / path pattern / query regex / flag toggle).
  - *Activity*: per-kid filtered Activity view.
- **Add-device enrolment wizard** (modal launched from Kid detail):
  1. Choose platform (iOS / Android / ChromeOS / other) and give the device a name.
  2. Server generates WG keypair, assigns next free `/32` in the WG subnet, writes peer to `kids.yaml`, triggers wg-easy reload.
  3. Show QR code + downloadable `.conf` + platform-specific install instructions inline (linked to `install-ios.md` etc.).
  4. Page polls `wg show` every 2s; when first handshake observed, advance to **step 5: install mitmproxy CA** (platform-specific walkthrough + downloadable cert).
  5. Done — device appears in the kid's device list as "active".
- **Activity** (`/activity`): chronological feed of requests/blocks/flags, filterable by kid, device, decision (allow/block/flag), and time. SNI-only events are visually marked so it's clear when we couldn't see the path.
- **Rules library** (`/rules`): editable blocklists (e.g. `adult`, `gambling`, `social-media-aggressive`) and app definitions (e.g. `tiktok` = list of domains + IP ranges). These are referenced by kids by name, so editing here updates every kid that uses them.
- **Settings**: webhook URL, email config, mitmproxy CA download + expiry indicator, "pause all VPNs for N minutes" panic button for captive-portal situations.

## Critical implementation notes & gotchas

1. **iOS cert trust is a manual two-step**. After installing the `.mobileconfig` profile, the user must go to Settings → General → About → Certificate Trust Settings and toggle the CA on. There's no way around this without supervised mode (which the user opted out of). Document this in `install-ios.md`.
2. **iOS Always-On VPN failsafe**: if WG drops, iOS by default blocks all traffic ("disconnect on demand" off). Good for our threat model, bad if our home server reboots. Mitigate with a watchdog that pings the WG endpoint and falls back to a public DNS-only profile after N minutes.
3. **Pinned apps will not decrypt**. TikTok, Instagram, Snapchat, banking apps, etc. mitmproxy will see them as opaque connections — we'll log the SNI (which is the destination hostname in cleartext) but not URLs/content. The dashboard should show these as "domain-only" entries, not pretend they're empty.
4. **AdGuard per-client identification**: because each device has its own WG peer IP, AdGuard's "Clients" tab can apply distinct blocklists per device. This is the whole reason for one-peer-per-device.
5. **nftables reconciliation**: the scheduler runs once per minute, computes desired rule set from kids.yaml + current time, diffs against live ruleset, applies minimal changes. Atomic via `nft -f`.
6. **Captive portals**: if a kid takes their device to a coffee shop and the captive portal fights the VPN, they're locked out. Document the "pause VPN for 10 min" parent flow.
7. **Resource budget on the NUC**: mitmproxy is the heaviest piece. 4–6 devices, mixed browsing, is comfortably <1 core. Plan for ~2GB RAM total.
8. **mitmproxy CA expiry**: default 3 years. Set a calendar reminder; rotation requires reinstalling on every device.

## Verification plan

End-to-end test sequence, run on the NUC against one test device (a spare phone or laptop):

1. `./gdlf up` — compose stack comes up; all health checks pass.
2. In the dashboard, click "Add kid" → enter test kid → click "Add device" → enrolment wizard runs → QR code appears. Verify `kids.yaml` was rewritten atomically and AdGuard now has a matching client.
2a. Hand-edit `kids.yaml` to add a second device. Verify the dashboard reloads and shows it within ~1s (file watcher).
3. Scan the QR on the test device. Confirm tunnel comes up and the dashboard auto-advances the enrolment wizard to the "install CA" step when it sees the first handshake.
4. On the device, browse to a known-blocked domain (`pornhub.com` test) — verify NXDOMAIN, AdGuard query log shows the block, dashboard event log shows it.
5. Install the mitmproxy CA on the test device. Browse a clear domain — verify dashboard shows the full URL + query (not just SNI).
5a. Add a `url_rules` entry that blocks `youtube.com/shorts/*`. Visit `youtube.com` (works), then `youtube.com/shorts/xxx` (gets block page). Verify dashboard logs both the block and the allow with full path visible.
5b. Add a `url_rules` flag entry for a benign search term. Search for it on Google. Verify the dashboard event marks it `flag=true` and that the webhook fires.
6. Set the schedule to block "now"; wait ≤60s; verify all traffic from that device's IP is dropped at the nftables layer (curl times out), and dashboard shows "out-of-window".
7. Add a `keyword_flag` for the word "weed"; search for it; verify the flag fires and a webhook POST lands at `webhook.site` (or wherever the user points it).
8. Reboot the NUC; confirm stack auto-restarts, devices reconnect, no manual intervention needed.
9. Repeat 4–7 on one device per platform class (iOS, Android, ChromeOS) to confirm cross-platform behavior — especially that mitmproxy decrypts browsers on all three and that pinned iOS apps gracefully fall back to SNI-only logging.

## What this plan does NOT include (intentional cuts)

- DoH/DoT escape-hatch blocking (user chose to skip)
- iOS supervised-mode MDM enrollment (user chose to skip)
- Multi-tenant / multi-family support
- Mobile app for parents — dashboard is browser-only initially
- Active content-modification (e.g., rewriting search results) — only blocking + logging

## Open questions for implementation phase

1. **nftables placement**: cleanest is to run it inside the WG container's net namespace (`network_mode: service:wg-easy`). Alternative is host-mode nftables. Will pick during build based on what wg-easy exposes.
2. **CA generation flow**: should `./gdlf up` generate the CA on first run and surface install QR codes in the dashboard, or require an explicit `./gdlf init` step? Leaning toward the latter for explicitness.
3. **AdGuard query-log ingestion**: AdGuard's webhook support is limited; may need to tail its query log file via a sidecar instead.

These don't block planning — they're choices to make at code time.
