<p align="center">
  <img src="docs/img/gandalf.png" alt="You shall not pass" width="420">
</p>

# gdlf — family network protection stack

A self-hosted parental-control appliance. Kids' devices connect over
WireGuard 24/7 and their traffic is filtered at three levels:

- **AdGuard** — DNS blocking (categories, custom lists, parental filter)
- **mitmproxy** — URL-path-level allow / block / flag rules (HTTPS, on
  devices that have the CA installed)
- **nftables** — per-device schedule enforcement (out of hours = blocked)
  with friendly "you shall not pass" pages for HTTP, fast TCP-RST for HTTPS

A single FastAPI dashboard (`rules-svc`) is the parent's control plane:
enrol devices via QR, edit per-kid rules, view real-time activity, get
push/email alerts on flagged events. `config/kids.yaml` is the
declarative source of truth — UI edits and hand edits cooperate.

## Layout

```
docker-compose.yml          # the stack (five services)
gdlf                        # ./gdlf up | down | logs | rebuild | ...
.env / .env.example         # WG_HOST, passwords, retention, SMTP, ...
CLAUDE.md                   # architecture overview (start here)
docs/design.md              # original design discussion
config/
  kids.example.yaml         # template committed to the repo
  kids.yaml                 # *** source of truth *** (gitignored — your real policy)
  wg/                       # WireGuard server keys, rendered wg0.conf
  adguard/                  # AdGuard runtime state
  mitmproxy/                # mitmproxy CA
  state/                    # rules-svc SQLite + per-device keys
services/
  rules-svc/                # FastAPI dashboard + control plane (Python)
  mitmproxy/                # mitmproxy image (+ httpx for the addon)
  blockpage/                # tiny HTTP server for schedule + sinkhole pages
nftables/                   # firewall sidecar
scripts/
  gen-ca.sh                 # one-time mitmproxy CA generation
  udp-forward.py            # macOS dev: bridge LAN UDP to a Colima VM
```

Each service has its own `CLAUDE.md` with module-level detail and gotchas.

## Quick start

```
cp .env.example .env       # edit WG_HOST (your DDNS / LAN IP) + passwords
./gdlf init                # one-time: generate the mitmproxy CA
./gdlf up                  # build + start the stack
open http://localhost:8080 # dashboard (parent UI)
open http://localhost:3000 # AdGuard's first-run wizard (only on first boot)
```

After AdGuard's wizard, its admin UI moves to **http://localhost:8082**.
Use port **80** and **53** as the defaults during the wizard — anything
else breaks our DNS redirect.

## Enrolling a kid's device

1. Dashboard → **Kids → "+ Add kid"** (name, age, schedule).
2. On the kid's page → **"+ Add device"** → name + platform → wizard
   appears with the WireGuard QR.
3. Scan the QR in the WireGuard app on the device. Toggle the tunnel on.
   The wizard auto-advances when it sees the first handshake.
4. Optional (highly recommended for URL-level rules): scan the **CA QR**
   on the same page to download `gdlf-ca.pem` to the device, then trust
   it in OS settings. After install, click "I've installed the CA" — the
   nftables reconciler will start routing :443 from that device through
   mitmproxy on its next cycle (~30s).

## Adding rules

- **Domain blocking** (every device) — done in AdGuard's UI (Filters →
  DNS blocklists). To get the friendly block page instead of "site not
  found", set AdGuard's Blocking Mode → Custom IP → `10.13.13.254`.
- **URL-path rules** (per kid, requires CA installed on device) — Kid
  detail → Rules tab → "Add rule", or click "+ rule" on any Activity row
  to pre-fill from an observed request.
- **Schedules** — Kid detail → Schedule tab (or edit `kids.yaml` directly
  — both stay in sync).

## Operating

```
./gdlf up        # start
./gdlf ps        # status
./gdlf logs svc  # follow logs (svc = rules-svc | mitmproxy | adguard | wg | nft | blockpage)
./gdlf restart   # restart (caveat: wg restart orphans the netns-shared services — restart them too)
./gdlf rebuild   # full no-cache rebuild
./gdlf down      # stop
```

## Activity retention

Events are pruned automatically — default **7 days** OR **200,000 rows**,
whichever bites first. Tune in `.env` via `RETENTION_DAYS` and
`MAX_EVENTS`. Hourly prune, daily VACUUM. Live storage stats on the
Settings page; manual "Prune + VACUUM now" button there too.

## macOS / Colima caveat

Colima (and historically Docker Desktop) doesn't forward UDP from the
host's LAN interface to the VM in its default network mode — so a phone
on the same WiFi can't reach `<mac-ip>:51820/udp`. Two options:

- Enable Colima's vmnet by setting `network.address: true` in
  `~/.colima/default/colima.yaml`. The VM gets a routable IP (e.g.
  `192.168.64.2`) reachable from the Mac but not from other LAN devices.
- Run the helper forwarder: `scripts/udp-forward.py 0.0.0.0:51820
  192.168.64.2:51820 &` to bridge LAN UDP into the VM.

On a real Linux host this isn't an issue.

## Where to read next

- [`CLAUDE.md`](CLAUDE.md) — architecture overview, common-failure cheat sheet
- [`docs/design.md`](docs/design.md) — original design discussion
- Per-service `CLAUDE.md` under `services/*/` and `nftables/`
