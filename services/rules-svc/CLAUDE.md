# rules-svc

FastAPI app — control plane, JSON API, and event sink. Everything that
isn't directly forwarding packets goes through here.

## Responsibilities

1. **JSON API** for the React SPA (`/api/*`) and the mitmproxy addon
   (`POST /api/decision`, `POST /api/events`).
2. **SPA hosting** — serves the built React app from `/app/web/` (multi-stage
   Dockerfile copies the Vite bundle in). Catch-all route returns
   `index.html` for client-routed deep links.
3. **Source-of-truth I/O** — read & write `config/kids.yaml` under filelock.
4. **WireGuard control** — generate device keypairs, allocate `/32` IPs,
   render `wg0.conf`, reload the wg container via the docker socket.
5. **AdGuard sync** — push one `gdlf:<kid>:<device>` client per WG IP. Event-driven (wakes on every `store.mutate()`) with a 5-minute backstop that also refreshes the services-catalog index. A reachability watchdog probes AdGuard from the gdlf bridge every 30s and asks docker to restart it after 3 consecutive failures (catches the cold-start netns race where AdGuard's bridge-IP listener doesn't come up cleanly).
6. **Event ingest** from the addon → SQLite + SSE fanout to live subscribers.
7. **Alerting** — fire webhook + email on `flag` events.
8. **Retention** — prune the events table hourly, VACUUM daily.

## Module map

| File             | Responsibility                                                        |
| ---------------- | --------------------------------------------------------------------- |
| `main.py`        | App, middleware, lifespan, non-API utility routes (CA, QR, wg conf), SPA catch-all, mitmproxy `/api/decision` + `/api/events`. |
| `api_auth.py`    | `/api/auth/login`, `/logout`, `/me` — cookie-based session.           |
| `api_kids.py`    | Kid CRUD, schedule, bonus, block.                                     |
| `api_devices.py` | Device CRUD, enrolment, handshake, regenerate, mitm-installed flag.   |
| `api_rules.py`   | URL-rule CRUD, suggest, library reference.                            |
| `api_activity.py`| Activity list + SSE stream.                                           |
| `api_settings.py`| Settings + DB stats + prune-now.                                      |
| `api_mdm.py`     | Apple iOS MDM: enrollment-token issuance, `.mobileconfig` serving, `/mdm/checkin`, `/mdm/server`, admin push + command endpoints. |
| `api_android_mdm.py` | Android Management API: per-device enrollment tokens, QR PNGs, policy + status sync endpoints. |
| `api_windows_mdm.py` | Windows Provisioning Package endpoints: build `.ppkg`, serve single-use download, parent-attest enrolment, revoke. |
| `mdm/`           | Apple MDM internals (see § MDM below).                                 |
| `amapi/`         | Android Management API internals (see § MDM (Android) below).          |
| `windows_mdm/`   | Windows enrolment package internals (see § MDM (Windows) below).        |
| `dto.py`         | Shared projection from pydantic models to JSON DTOs.                  |
| `pubsub.py`      | In-process publish/subscribe used by SSE.                             |
| `schema.py`      | Pydantic v2 models for kids.yaml. Single source of contract.          |
| `store.py`       | Load/save kids.yaml. `filelock` + in-place write.                     |
| `settings.py`    | Frozen dataclass from env vars (`from .settings import settings`).    |
| `wg.py`          | X25519 keypair gen, IP allocation, wg0.conf rendering, docker exec.   |
| `rules.py`       | URL-rule evaluator + `suggest_match()` helper.                        |
| `db.py`          | SQLModel Event/Handshake/AlertLog + MDM tables; `insert_or_bump` (session-window upsert), `recent_events`, `prune`, `vacuum`. |
| `aggregates.py`  | Per-kid counters derived from `event` via SQL aggregation (no in-memory accumulator). |
| `api_stats.py`   | `/api/stats/overview` and `/api/stats/kid/{name}` — wraps `aggregates`. |
| `api_tls_failures.py` | `/api/tls-failures` — reads `event` rows with `decision='tls_failed'`, grouped by registrable domain. |
| `adguard.py`     | REST-API client + event-driven sync loop + bridge-reachability watchdog. |
| `alerts.py`      | Webhook + SMTP dispatcher; logs each attempt to `AlertLog`.           |
| `auth.py`        | HMAC cookie token primitives.                                         |
| `addons/mitm_capture.py` | mitmproxy addon (mounted into the mitm container). Splice-by-default in `tls_clienthello`: only SNIs in `INSPECT_GLOBAL_DEFAULTS` (bulk_cdns.py) ∪ per-kid `mitm_inspect_hosts` get terminated for URL-path rules. Everything else is `ignore_connection = True`. |
| `web/`           | Vite + React + TypeScript SPA (HeroUI, TanStack, zod, Tailwind).      |

## How it talks to other services

| Peer        | Direction         | Protocol                            | Why                                  |
| ----------- | ----------------- | ----------------------------------- | ------------------------------------ |
| Browser SPA | browser → svc     | HTTP JSON `/api/*` + SSE `/api/activity/stream` | Dashboard UI         |
| mitmproxy   | mitm → svc        | HTTP POST `/api/decision`           | Per-request allow/block/flag         |
| mitmproxy   | mitm → svc        | HTTP POST `/api/events`             | Log every request                    |
| AdGuard     | svc → adguard     | HTTP REST (`10.42.0.2:80`)          | Push per-client config on each `kids.yaml` mutation (5-min backstop) |
| wg (docker) | svc → docker.sock | `httpx` over `/var/run/docker.sock` | exec `wg syncconf`, restart on reload|
| kids.yaml   | both ways         | filesystem (`config/kids.yaml`)     | Source of truth                      |
| nftables    | none directly     | nft reads kids.yaml on its own loop | We publish via yaml, no push         |

## Front-end (`web/`)

- **Build**: `vite build` → `dist/`. The Dockerfile's `node:20-slim` builder
  stage emits this; the Python image `COPY --from=web /web/dist /app/web`.
- **Dev**: `./gdlf web-dev` runs Vite on :5173 with `/api`, `/devices/*`,
  `/ca*` proxied to the running rules-svc container.
- **Routing**: TanStack Router, file-based under `web/src/routes/`. The
  route tree is regenerated by `@tanstack/router-plugin/vite` on each build.
- **Data**: TanStack Query for caching/polling. SSE in
  `hooks/useActivityStream.ts` prepends events into the activity cache;
  falls back to 5s `invalidateQueries` polling on disconnect.
- **Components**: HeroUI (`@heroui/react`) on Tailwind. Dark/light via
  `next-themes`. All destructive actions go through `useConfirm()` → a
  single `<ConfirmModal>`; no `window.confirm` anywhere.
- **API contract**: shared via zod schemas in `web/src/lib/schemas.ts`.
  Mirror the Python DTOs in `src/gdlf/dto.py` — keep both in sync.

## Auth flow

- Cookie `gdlf_auth` set by `POST /api/auth/login`, HMAC-signed with a key
  derived from `RULES_SVC_ADMIN_PASSWORD`. Empty password → middleware is
  a no-op (dev / first boot).
- Middleware: unauthenticated `/api/*` → JSON 401. Unauthenticated
  non-API → still serves `index.html` so the SPA can route to `/login`
  itself (the URL the user typed stays intact).
- Public allow-list: `/healthz`, `/ca.pem`, `/ca/qr`, `/api/dl/*` (shortlink
  enrolment only), `/dl/*` (shortlink SPA + code-only package downloads),
  `/api/decision`, `/api/events` (mitmproxy), `/api/auth/login`, and the
  Vite `/assets/` bundle. IP-addressed device config routes require the
  parent cookie; shared enrolment uses `/api/dl/{code}/conf` and
  `/api/dl/{code}/qr`.

## Gotchas

* **Bind-mounted single-file `os.replace()` fails.** `kids.yaml` is mounted
  file-by-file from the host so atomic rename hits `EBUSY`. `store.save()`
  writes in place under `filelock`.

* **Docker CLI isn't installed in the image.** `wg.py` talks to the docker
  daemon directly via `httpx` over `/var/run/docker.sock`. Handles the 8-byte
  multiplex frame headers manually — see `_docker_exec()`.

* **WAL mode for SQLite.** Concurrent inserts (`/api/events`) and the
  hourly prune deadlocked occasionally in default journal mode. Enabled in
  `db.engine()`.

* **Activity ingest is a single SQL upsert.** `/api/events` calls
  `db.insert_or_bump`, which is one `INSERT ... ON CONFLICT DO UPDATE`
  keyed on `(kid, host, path, query, decision, bucket_ts)`. Same-bucket
  repeats collapse onto an existing row with `hit_count += 1`. This is
  the only writer; counters (`/api/stats/*`), the feed
  (`/api/activity`), and SSE all read from the same `event` table, so
  the displayed numbers always match the rows in the feed.

* **SSE is a change-ping, not a row stream.** `pubsub.publish()` emits
  one `{"kind":"changed","kid":<name>}` per ingest; the SPA's
  `useActivityStream` debounces and `invalidateQueries` so it
  refetches `/api/activity` + the stats endpoints. The bounded
  per-subscriber queue (depth 100) still drops on overflow, but the
  payload is coalescable — a missed ping is recovered by the next one
  (and by the 5s polling fallback in `useActivityStream`).

* **Mitmproxy addon lives here, runs there.** `addons/mitm_capture.py` is
  mounted read-only into `gdlf-mitm` at `/addons/`. Edit here, rebuild
  the mitm container (or just `docker restart gdlf-mitm`).

## MDM (Apple iOS, supervised)

Opt-in feature that turns gdlf from a guardrail into actual containment for
iOS devices. The flow is implemented natively (no NanoMDM dependency) and
intentionally narrow — iOS only; macOS / Android / Windows are future work.

### How a device gets enrolled

1. **Setup (one time per appliance)**: APNs MDM Push Cert acquired via
   `./gdlf apns {csr,submit,decrypt}` + `identity.apple.com/pushcert/`.
   Signing CA generated via `./gdlf mdm-ca init`. See top-level CLAUDE.md
   for `config/state/{apns,mdm-ca}/` layout.

2. **Per-device enrolment**: dashboard `POST /api/devices/{ip}/mdm/enroll-token`
   mints a one-time token → opens `/mdm/enroll/{token}` in Apple
   Configurator 2 on a Mac. Configurator wipes + supervises + installs the
   `.mobileconfig` we serve. The profile embeds a PKCS12 identity cert
   (RSA 2048 signed by the gdlf MDM CA, 10-year validity) + an `com.apple.mdm`
   payload pointing at our endpoints.

3. **Steady state**: Apple sends Authenticate + TokenUpdate to /mdm/checkin
   (mTLS-validated by Caddy, see `services/proxy/CLAUDE.md`). On the *first*
   TokenUpdate, `mdm.orchestrator.deploy_baseline()` queues an InstallProfile
   command bundling: WG always-on (com.wireguard.ios + OnDemand +
   `OnDemandUserOverrideDisabled`), CA trust (com.apple.security.root,
   auto-trusted because MDM-pushed), and Restrictions (no VPNCreation,
   ProfileInstallation, EraseContentAndSettings). The whole profile is
   `PayloadRemovalDisallowed`.

4. **Pushes**: APNs HTTP/2 wakeups via `mdm.apns.send_push()` — device
   pulls from /mdm/server, executes, posts back to /mdm/server. See
   `mdm.commands` for the queue + response wiring.

### Module map (`mdm/`)

| File            | Responsibility                                                           |
| --------------- | ------------------------------------------------------------------------ |
| `identity.py`   | Mint per-device RSA cert + PKCS12 from the MDM CA. Cached CA load.       |
| `enrollment.py` | Build the enrollment `.mobileconfig` (PKCS12 + MDM payload).             |
| `apns.py`       | APNs topic extraction + HTTP/2 push client (mTLS using push.{pem,key}).  |
| `commands.py`   | Command plist builders + SQLite-backed queue/response helpers.           |
| `checkin.py`    | /mdm/checkin handlers (Authenticate / TokenUpdate / CheckOut).           |
| `server.py`     | /mdm/server poll handler — record response, return next command.         |
| `profiles.py`   | Policy payload builders (VPN / CA / Restrictions) + baseline composer.   |
| `orchestrator.py` | Glue: build profile + enqueue InstallProfile + fire push.              |

### Lookup model

Devices identify themselves at the TLS layer via a cert with
`CN=gdlf-device-<wg_ip>`. Caddy validates the chain and forwards
`X-Mdm-Client-Subject` + `X-Mdm-Client-Cert-B64` to rules-svc.
`schema.KidsConfig.device_by_mdm_identity(cn)` is the lookup helper.
The `MdmState` sub-model on `Device` is persisted in kids.yaml alongside
everything else.

### Tables (db.py)

* `mdm_enroll_tokens` — one-time enrollment URL tokens (30-min TTL).
* `mdm_command_queue` — pending/sent/acknowledged/error commands per device.
* `mdm_command_responses` — full plist response excerpts for the dashboard.

### Gotchas

* **PKCS12 password embedded plaintext** in the profile is intentional —
  Apple decrypts at install time; the password is no more sensitive than
  the profile itself.
* **No SCEP** — cert rotation requires pushing a new profile via the
  existing `InstallProfile` channel while the cert is still valid. With
  10-year validity, this is effectively never.
* **APNs cert is annual** — renew via identity.apple.com (the existing
  `push.key` stays, only the cert refreshes). Should add a dashboard
  banner at 30-days-to-expiry; not yet built.
* **mTLS cert in the X-Mdm-Client-Cert-B64 header is base64-DER**, not
  PEM — PEM has newlines which aren't legal in HTTP header values.

## MDM (Android, Android Management API)

The Android equivalent of Apple MDM, deliberately separate code because the
protocols share nothing. Structurally much smaller than the iOS stack because
Google hosts the DPC (`Android Device Policy`), provides push, and acts as
the device-to-cloud TLS terminator — rules-svc only calls the AMAPI REST API.

### How a device gets enrolled

1. **Setup (one time per appliance)**:
   * `./gdlf amapi init` — prints GCP setup steps (create project, enable
     Android Management API, download service-account JSON to
     `config/state/amapi/service-account.json`).
   * `./gdlf amapi enterprise signup <callback_url>` — calls
     `signupUrls.create`, returns a URL the parent visits to bind an
     Enterprise to their Google account.
   * `./gdlf amapi enterprise complete <enterpriseToken> <signupUrlName>`
     — finalises and writes `config/state/amapi/enterprise.json`.

2. **Per-device enrolment**: dashboard `POST /api/devices/{ip}/android-mdm/enroll-token`
   builds the per-device Policy (force-installed WireGuard with managed
   config carrying the `.conf`, `alwaysOnVpnPackage` with `lockdownEnabled`,
   `caCerts` carrying the mitmproxy CA, plus restrictions), patches it via
   `enterprises.policies.patch`, then mints an enrollment token bound to
   that policy. Returns a QR PNG URL the dashboard renders.

3. **Provisioning**: parent factory-resets the phone, taps the welcome
   screen six times to open the QR scanner, scans `/devices/{ip}/android-mdm/qr.png`.
   Android downloads `Android Device Policy`, enrols as Device Owner, applies
   the Policy. The factory reset is unavoidable — Device Owner mode can only
   be set during initial provisioning.

4. **Steady state**: the background `amapi.orchestrator.status_sync_loop`
   polls `enterprises.devices.list` every 60s. The device is matched back to
   a kids.yaml row via `additionalData` (we stuff `{wg_ip, kid, device}`
   into it at token mint), and `AndroidMdmState.{status, model,
   applied_policy_version, last_status_at}` is mirrored from Google's view.

5. **Policy updates**: any `store.mutate()` fires the `mutation_event`,
   which a debounced `_amapi_policy_watch_loop` in `main.py` picks up and
   triggers `sync_all_policies()`. AMAPI propagates new policy to devices
   within a few minutes (or instantly if the device polls).

### Module map (`amapi/`)

| File              | Responsibility                                                              |
| ----------------- | --------------------------------------------------------------------------- |
| `client.py`       | Lazy googleapiclient + state-file readers (service-account, enterprise).    |
| `enterprise.py`   | `signupUrls.create` + `enterprises.create` for the one-time signup.         |
| `policy.py`       | `build_policy(kid, device) -> dict` — the JSON we patch into AMAPI.         |
| `enrollment.py`   | `mint(policy_name=...)` — wraps `enrollmentTokens.create`.                  |
| `orchestrator.py` | sync_policy / sync_device_status / 60s background loop.                     |

### What's NOT in the Android path (vs iOS)

Don't go looking for these — they don't exist on the Android side:

* No identity certs / MDM signing CA — devices authenticate to Google, not us.
* No Caddy mTLS terminator — there's no inbound device traffic.
* No APNs / push cert — Google's DPC handles push.
* No command queue or response tables — policy is declarative; AMAPI
  reconciles.
* No `/mdm/checkin` / `/mdm/server` equivalents — outbound calls only.

### State files

* `config/state/amapi/service-account.json` — GCP service-account key.
  Treat as a long-lived credential (0600). Rotating it means downloading
  a new key from GCP and dropping it into the same path.
* `config/state/amapi/enterprise.json` — `{name, project_id,
  signup_url_name, display_name}`. Written by `./gdlf amapi enterprise
  complete`. Recreating it requires going through signup again, which is
  fine — devices stay enrolled to the same `enterprises/...` resource.

### Gotchas

* **WireGuard for Android managed config** — the load-bearing key is
  `config` (the full .conf as a string). The DPC injects it at the moment
  WG installs, so the kid sees no "import tunnel" prompt.
* **Factory reset is unavoidable** — Device Owner mode requires fresh
  provisioning. Devices already in use need to be wiped (same constraint
  as the Apple Configurator flow for iOS).
* **CA trust goes via `openNetworkConfiguration`**, not a top-level
  `caCerts` field. AMAPI's public Policy schema has no `caCerts` —
  certificate authority install is done via Chromium's Open Network
  Configuration (ONC) embedded under `openNetworkConfiguration`, with
  `Certificates[].Type="Authority"` + `TrustBits=["Web"]` to mark the
  cert as system-trusted for TLS. `policy.build_policy()` constructs
  this; `_load_ca_b64()` returns the base64-DER bytes that go in the
  `X509` field.
* **Status poll is the only way we discover enrolment** — the device
  doesn't tell us when it's done; it tells Google. We learn via the next
  60s poll, so freshly-enrolled devices show "pending" for up to a minute.

## MDM (Windows, downloadable .zip)

The Windows path is deliberately asymmetric vs Apple / Android: **there
is no live channel after enrolment**. A per-device `.zip` is generated
on demand, containing a self-elevating `Install.cmd` plus PowerShell
scripts + WG MSI + per-kid conf + CA. The parent extracts it on the
kid's PC and runs `Install.cmd` as Administrator (UAC). After that, the
only ongoing enforcement is what install.ps1 set up locally — the
per-tunnel WireGuard Windows service (kernel kill-switch + service ACL),
the `LimitedOperatorUI` registry flag, the gdlf CA in `LocalMachine\Root`,
and a SYSTEM scheduled task that re-asserts state every 5 minutes.

**Why not .ppkg?** We tried. Building a Windows Provisioning Package
from Python is not viable — `.ppkg` is internally a WIM archive with a
compiled multi-XML structure (CommonSettings + Multivariant +
MasterDatastore + RunTime + per-setting `.provxml` files keyed by
undocumented SettingsGroup GUIDs). Only icd.exe / Windows Configuration
Designer can produce a compliant one; anything else gets "Enter the
package password" at apply time then fails to aka.ms/provisioningfaq.
See [docs/setup-windows-mdm.md](../../docs/setup-windows-mdm.md) for
the user-facing version of this story.

The reason for the broader "no MDM CSP" choice: VPNv2 CSP doesn't speak
WireGuard natively, and OMA-DM enrolment isn't available on Home edition
anyway. WireGuard for Windows already provides everything a real MDM
channel would — its per-tunnel service is ACL'd against non-admins and
installs WFP kernel filters that drop untunneled traffic. So gdlf wraps
that plus the CA + reconcile task in a `.zip` an Administrator runs once.

### How a device gets enrolled

1. **Setup (one time per appliance)**:
   * `./gdlf windows init` — fetches the official WireGuard for Windows
     MSI to `config/state/windows/wireguard.msi`, pinned-version with
     SHA256 verification. Bundled into every per-device `.zip`.
   * (No MDM CA needed — we don't sign anything on the Windows path.)

2. **Per-device build**: dashboard `POST /api/devices/{ip}/windows-mdm/enroll-package`
   assembles `Install.cmd` + `install.ps1` + `reconcile.ps1` (per-device,
   templated) plus per-kid wg-quick conf + mitmproxy CA (DER) + WireGuard
   MSI + `README.txt`, packs them into a `.zip` via Python's stdlib
   `zipfile`. Returns a one-time download URL. (The endpoint name and
   `BuiltPackage` field names still say `ppkg` for backwards compat with
   stored state.)

3. **Provisioning**: parent extracts the .zip on the kid's PC,
   right-clicks `Install.cmd` → Run as administrator (or double-clicks
   for the same effect via the script's self-elevation snippet). UAC
   prompts; on approval, install.ps1 runs as Administrator. install.ps1
   imports the CA into `LocalMachine\Root`, silent-installs WireGuard,
   drops the conf, `wireguard.exe /installtunnelservice`, sets
   `LimitedOperatorUI=1`, registers the SYSTEM reconcile scheduled task,
   and stamps the `HKLM\Software\gdlf\Enrollment` registry key.

4. **Steady state**: SYSTEM scheduled task `gdlf-reconcile` runs on
   boot + every 5 minutes. Reads the enrollment registry key, re-asserts
   service Running + Automatic, re-checks the conf hash matches what
   was baked in, re-asserts LimitedOperatorUI.

5. **Containment**: the kid runs as **Standard User**, the parent is
   the sole local Administrator. The kid can't stop the WG service
   (ACL), can't `Unregister-ScheduledTask` (Admin-only), can't
   `certutil -delstore Root` (Admin-only), can't open
   `C:\ProgramData\gdlf\` (ACL we set in install.ps1).

### Module map (`windows_mdm/`)

| File                | Responsibility                                                          |
| ------------------- | ----------------------------------------------------------------------- |
| `package.py`        | `build_enroll_ppkg` / `build_revoke_ppkg` — assemble files, zip them via stdlib `zipfile`. Plus on-disk `packages_dir` + stash/unstash helpers for the download endpoint. |
| `scripts.py`        | `Install.cmd` / `Uninstall.cmd` (self-elevating UAC wrappers), `install.ps1` and `reconcile.ps1` (per-device, substituted at build time), `uninstall.ps1` (static). |
| `wireguard_conf.py` | Thin wrapper over `gdlf.wg.build_client_conf` so package.py doesn't have to reconstruct peer ids. |

### What's NOT in the Windows path (vs iOS / Android)

Don't look for these — they don't exist:

* No identity certs / mTLS — Windows never calls back, so there's nothing
  to authenticate.
* No `/mdm/checkin` / `/mdm/server` equivalents.
* No status sync loop — `mark-enrolled` is parent-attested via the
  dashboard.
* No command queue / response tables — policy is fully embedded in the
  .zip at build time; "pushing a change" means re-issuing the bundle.
* No customizations.xml / WCD / icd dependencies.
* No Authenticode signing — nothing to sign (it's a plain zip).

### Gotchas

* **No external runtime deps.** Just Python stdlib `zipfile` + `cryptography`
  (already a base dep). The earlier `gcab` / `osslsigncode` packages have
  been removed from the Dockerfile.
* **The `package_id` GUID must stay stable per (kid, device)** — derived
  via UUIDv5 of the peer_id in `_stable_package_id`. Bumping the
  namespace UUID would orphan every previously-recorded `WindowsMdmState`
  in kids.yaml.
* **`package_version` is bookkeeping-only on the zip path.** There's no
  Windows-side "is this newer" check — install.ps1 is idempotent and
  always overwrites. The version is kept so the dashboard can show "last
  built v1.0.…" for parent awareness.
* **PowerShell here-strings have ugly `$` quirks** — `scripts.py` uses
  plain `__NAME__` placeholders rather than f-strings so the raw `.ps1`
  stays readable. Adding a new placeholder also needs a `_substitute`
  line.
* **install.ps1 phones home at the end.** Last step POSTs
  `<dashboard>/api/dl/<shortlink>/windows-mdm/mark-enrolled`
  so the parent doesn't have to click Mark Applied. Best-effort: 5
  attempts with 3s sleep then gives up. The dashboard URL is captured
  from the `Origin` header of the build request (whatever URL the
  parent's browser is on); the shortlink is looked up (or minted) by
  `api_shortlinks`. Falls back gracefully on any failure — manual
  Mark Applied button stays as the safety net. uninstall.ps1 does the
  same thing first-thing, before tearing down the tunnel (it reads
  DashboardBaseUrl + Shortlink from the registry stamp install.ps1 left).
* **install.ps1's `$assetDir` is `$PSScriptRoot`.** `Install.cmd` does
  `cd /d "%~dp0"` before invoking the script, but the script itself is
  also free-standing — `$PSScriptRoot` always points to its own folder
  whether invoked via the .cmd wrapper or directly via `powershell -File`.
* **Why .zip not .ppkg.** `.ppkg` is a WIM archive (`MSWIM` magic) with
  an undocumented multi-XML compiled structure including per-setting
  SettingsGroup GUIDs. Hand-built .ppkg files (any container) fail with
  "Enter the package password" because Windows' provisioning runtime
  treats unparseable contents as encrypted. icd.exe is the only known
  generator. See `windows_mdm/package.py` docstring for the autopsy.
* **Firefox uses its own cert store** (not `LocalMachine\Root`). HTTPS
  interception breaks the moment the kid switches browsers. Documented
  in [docs/setup-windows-mdm.md](../../docs/setup-windows-mdm.md);
  parent's responsibility to either pin Edge/Chrome or flip Firefox's
  `security.enterprise_roots.enabled` via a policies.json.

## Adding a new page

1. New file under `web/src/routes/` (e.g. `web/src/routes/foo.tsx`). Export
   a `Route = createFileRoute('/foo')({ component: FooPage })`. The router
   plugin picks it up on next `vite` run.
2. Need new data? Add a `/api/<name>` endpoint in a new or existing
   `api_*.py` router and `app.include_router(...)` it in `main.py`. Mirror
   the DTO shape with a zod schema in `web/src/lib/schemas.ts`.
3. Need a query? Add to `web/src/lib/queries.ts` (TanStack Query).
4. Need a mutation? Add to `web/src/lib/mutations.ts` and invalidate the
   relevant query keys in `onSuccess`.

## Adding a kids.yaml field

1. Add to `schema.py` with sensible default.
2. Anywhere that reads it via `store.load()` gets it for free.
3. If a DTO consumer needs it, surface it in `dto.py` and add it to the
   matching zod schema in `web/src/lib/schemas.ts`.
4. **Don't backfill the YAML** — pydantic defaults handle absent fields.

## Tests / smoke

```bash
# Activity ingest + counter consistency tests
cd services/rules-svc && pip install -e ".[dev]" && pytest tests/

# Decision API end-to-end (must stay byte-identical)
curl -s -X POST http://localhost:8080/api/decision -H 'content-type: application/json' \
  -d '{"client_ip":"10.13.13.3","host":"youtube.com","path":"/shorts/x"}'

# Activity SSE stream
curl -N http://localhost:8080/api/activity/stream -H 'Cookie: gdlf_auth=...'

# Kids list
curl -s http://localhost:8080/api/kids -H 'Cookie: gdlf_auth=...' | jq

# Rule eval
docker exec gdlf-rules python3 -c "
from gdlf.rules import evaluate
from gdlf.schema import Kid, URLRule
k = Kid(name='t', url_rules=[URLRule(action='block', match='youtube.com/shorts/*')])
print(evaluate(k, 'youtube.com', '/shorts/x'))"
```
