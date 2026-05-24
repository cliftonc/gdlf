# rules-svc

FastAPI app. It's the control plane, dashboard, and event sink — everything
that isn't directly forwarding packets goes through here.

## Responsibilities

1. **Dashboard** — Kids, Activity, Rules library, Settings (Jinja + HTMX).
2. **Source-of-truth I/O** — read & write `config/kids.yaml`.
3. **WireGuard control** — generate device keypairs, allocate `/32` IPs,
   render `wg0.conf`, reload the wg container.
4. **AdGuard sync** — push one `gdlf:<kid>:<device>` client per WG IP, with
   parental/safesearch/blocked-services flags. Reconciles every 60s.
5. **Decision API** for the mitmproxy addon (`POST /api/decision`).
6. **Event ingest** from the addon (`POST /api/events`) → SQLite.
7. **Alerting** — fire webhook + email on `flag` events.
8. **Retention** — prune the events table hourly, VACUUM daily.

## Module map

| File          | Responsibility                                                        |
| ------------- | --------------------------------------------------------------------- |
| `main.py`     | FastAPI app, routes, lifespan, background tasks. The HTTP surface.    |
| `schema.py`   | Pydantic v2 models for kids.yaml. Single source of contract.          |
| `store.py`    | Load/save kids.yaml. `filelock` + in-place write (see Gotchas).       |
| `settings.py` | Frozen dataclass from env vars. Imported as `from .settings import settings`. |
| `wg.py`       | X25519 keypair gen, IP allocation, wg0.conf rendering, docker exec via socket. |
| `rules.py`    | URL-rule evaluator — `evaluate(kid, host, path, query)` → `Decision`. |
| `db.py`       | SQLModel Event / Handshake / AlertLog; `recent_events`, `prune`, `vacuum`. |
| `adguard.py`  | REST-API client + 60s sync loop.                                      |
| `alerts.py`   | Webhook + SMTP dispatcher; logs each attempt to `AlertLog`.           |
| `addons/mitm_capture.py` | mitmproxy addon (mounted into the mitm container).         |
| `templates/`  | Jinja templates. `base.html` + per-page; `_activity_rows.html` is an HTMX fragment. |
| `static/app.css` | Dark UI. `main.container-wide` is `!important` everywhere because of cache surprises. |

## How it talks to other services

| Peer        | Direction      | Protocol                            | Why                                  |
| ----------- | -------------- | ----------------------------------- | ------------------------------------ |
| mitmproxy   | mitm → svc     | HTTP POST `/api/decision`           | Per-request allow/block/flag         |
| mitmproxy   | mitm → svc     | HTTP POST `/api/events`             | Log every request                    |
| AdGuard     | svc → adguard  | HTTP REST (`10.42.0.2:80`)          | Push per-client config every 60s     |
| wg (docker) | svc → docker.sock | `httpx` over `/var/run/docker.sock` | exec `wg syncconf`, restart on reload |
| kids.yaml   | both ways      | filesystem (`config/kids.yaml`)     | Source of truth                      |
| nftables    | none directly  | nft reads kids.yaml on its own loop | We don't push, we publish via yaml   |

## Gotchas

* **Bind-mounted single file can't be `os.replace()`'d.** `kids.yaml` is
  mounted file-by-file from the host, so the atomic rename pattern fails
  with `EBUSY`. `store.save()` writes in place under `filelock`. Tolerable
  because all writes are serialized.

* **Starlette's `TemplateResponse(name, ctx)` signature is deprecated.**
  Use `TemplateResponse(request, name, context)`. Helper: `_render(request,
  name, **extra)` in `main.py`.

* **Docker CLI is not installed in the rules-svc image** (the Debian
  `docker.io` package only ships `dockerd`, not `docker`). `wg.py` talks
  to the docker daemon directly via `httpx` over `/var/run/docker.sock`.
  The exec implementation handles the 8-byte multiplex frame headers
  manually — see `_docker_exec()`.

* **CSS cache-busting** — `templates.env.globals["css_v"]` returns the
  CSS file's mtime; `base.html` uses `?v={{ css_v() }}` so the browser
  always picks up the latest. Saves hard-refresh dances after layout changes.

* **WAL mode for SQLite.** Concurrent inserts (`/api/events`) and the
  hourly prune deadlocked occasionally in default journal mode. Enabled
  in `db.engine()`.

* **Detached-instance SQLAlchemy errors.** After `db.insert(ev)` the
  session is closed, so `ev.decision` etc. raise `DetachedInstanceError`
  on access. Use the request `payload` dict for flag-check logic, not the
  ORM instance.

* **Mitmproxy addon lives here, but runs there.** `addons/mitm_capture.py`
  is mounted read-only into `gdlf-mitm` at `/addons/`. Edit here, rebuild
  the mitm container (or just `docker restart gdlf-mitm` — mitmproxy
  watches the script and reloads).

## Adding a new dashboard page

1. New route in `main.py`. Use `_render(request, "x.html", foo=bar)`.
2. New template in `templates/`. Extends `base.html`. Set `{% set nav =
   "..." %}` to highlight the right top-nav item and `{% set wide = true %}`
   if you want the page to use the edge-to-edge layout.

## Adding a kids.yaml field

1. Add to `schema.py` with sensible default.
2. Anywhere that reads it via `store.load()` gets it for free.
3. If the nftables sidecar or adguard sync needs it, add to those.
4. **Don't backfill the YAML** — pydantic defaults handle absent fields,
   and the next mutate-and-save round-trip materializes it.

## Tests / smoke

There aren't formal tests yet. Quick checks:

```bash
# Decision API end-to-end
curl -s -X POST http://localhost:8080/api/decision -H 'content-type: application/json' \
  -d '{"client_ip":"10.13.13.3","host":"youtube.com","path":"/shorts/x"}'

# Rule eval
docker exec gdlf-rules python3 -c "
from gdlf.rules import evaluate
from gdlf.schema import Kid, URLRule
k = Kid(name='t', url_rules=[URLRule(action='block', match='youtube.com/shorts/*')])
print(evaluate(k, 'youtube.com', '/shorts/x'))"

# Storage stats
curl -s http://localhost:8080/settings | grep -A1 "Activity storage"
```
