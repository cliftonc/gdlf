"""rules-svc entrypoint.

The HTTP surface is split into two:

  * `/api/*`   — JSON, consumed by the React SPA *and* the mitmproxy addon
                (POST /api/decision, POST /api/events).
  * everything else — the SPA shell. Vite-fingerprinted assets live under
                /assets; the catch-all serves index.html so the client
                router can handle deep links.

Non-API utility routes (`/ca.pem`, `/ca/qr`, `/devices/{ip}/conf`,
`/devices/{ip}/qr`, `/healthz`) return binary / SVG / plaintext and stay
where they are.
"""
from __future__ import annotations

import asyncio
import io
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import (
    adguard,
    aggregates,
    alerts,
    api_activity,
    api_android_mdm,
    api_auth,
    api_devices,
    api_kids,
    api_mdm,
    api_rules,
    api_services,
    api_settings,
    api_shortlinks,
    api_stats,
    api_tls_failures,
    api_windows_mdm,
    auth,
    bulk_cdns,
    db,
    pubsub,
    store,
    wg,
)
from .api_tls_failures import registrable_domain
from .amapi import orchestrator as amapi_orchestrator
from .rules import evaluate
from .settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    wg.ensure_server_keys()
    try:
        cfg = store.load(force=True)
        wg.write_wg0_conf(cfg)
    except FileNotFoundError:
        pass
    db.engine()
    try:
        n = api_shortlinks.ensure_shortlinks_for_all_devices()
        if n:
            import logging
            logging.getLogger("gdlf.shortlinks").info("backfilled %d device shortlink(s)", n)
    except Exception as e:
        import logging
        logging.getLogger("gdlf.shortlinks").warning("shortlink backfill failed: %s", e)
    store.bind_event_loop(asyncio.get_running_loop())
    sync_task = asyncio.create_task(adguard.sync_loop())
    prune_task = asyncio.create_task(_prune_loop())
    flush_task = asyncio.create_task(aggregates.flush_loop())
    amapi_task = asyncio.create_task(amapi_orchestrator.status_sync_loop())
    amapi_watch_task = asyncio.create_task(_amapi_policy_watch_loop())
    try:
        yield
    finally:
        sync_task.cancel()
        prune_task.cancel()
        flush_task.cancel()
        amapi_task.cancel()
        amapi_watch_task.cancel()


async def _amapi_policy_watch_loop():
    """Debounced kids.yaml -> AMAPI policy sync.

    Waits for `store.mutation_event()`, then sleeps a short cool-down so a
    burst of saves (e.g. parent toggling several rules quickly) collapses
    into one round of `enterprises.policies.patch` calls. No-op while AMAPI
    isn't configured.
    """
    from .amapi import client as _amapi_client
    import logging
    log = logging.getLogger("gdlf.amapi.watch")
    event = store.mutation_event()
    while True:
        try:
            await event.wait()
            event.clear()
            # Debounce ~2s; if more saves arrive in that window we'll still
            # only run sync_all_policies once.
            await asyncio.sleep(2.0)
            event.clear()
            if not _amapi_client.is_configured():
                continue
            res = await asyncio.to_thread(amapi_orchestrator.sync_all_policies)
            if res["ok"] or res["errors"]:
                log.info("amapi policy resync: ok=%d errors=%d",
                         len(res["ok"]), len(res["errors"]))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("amapi policy watch failed: %s", e)
            await asyncio.sleep(5.0)


async def _prune_loop():
    """Trim the events table hourly; VACUUM once a day."""
    import logging
    log = logging.getLogger("gdlf.prune")
    runs = 0
    while True:
        try:
            res = db.prune(
                settings.retention_days,
                settings.max_events,
                stats_retention_days=settings.stats_retention_days,
            )
            if res["age_deleted"] or res["cap_deleted"] or res.get("stats_deleted"):
                log.info(
                    "pruned: age=%d cap=%d stats=%d",
                    res["age_deleted"], res["cap_deleted"], res.get("stats_deleted", 0),
                )
            runs += 1
            if runs % 24 == 0:
                db.vacuum()
                log.info("vacuumed db")
        except Exception as e:
            log.warning("prune failed: %s", e)
        await asyncio.sleep(3600)


app = FastAPI(title="gdlf rules-svc", lifespan=lifespan)

# JSON API surface.
app.include_router(api_auth.router)
app.include_router(api_kids.router)
app.include_router(api_devices.router)
app.include_router(api_rules.router)
app.include_router(api_services.router)
app.include_router(api_activity.router)
app.include_router(api_settings.router)
app.include_router(api_tls_failures.router)
app.include_router(api_mdm.router)
app.include_router(api_android_mdm.router)
app.include_router(api_windows_mdm.router)
app.include_router(api_shortlinks.router)
app.include_router(api_stats.router)

# Where the multi-stage Dockerfile lands the built SPA. The dev server
# (`./gdlf web-dev`) runs Vite separately and proxies /api back to here, so
# in dev this directory can be missing.
_SPA_ROOT = Path("/app/web")
_SPA_INDEX = _SPA_ROOT / "index.html"
_SPA_ASSETS = _SPA_ROOT / "assets"

if _SPA_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(_SPA_ASSETS)), name="spa-assets")


# ---------------------------------------------------------------------------
# Auth middleware.
# - Returns JSON 401 for /api/* (SPA handles redirect).
# - Returns the SPA shell (200 + index.html) for unauthenticated non-API
#   navigation, so the client router lands the user on /login itself.
# When RULES_SVC_ADMIN_PASSWORD is empty, auth is disabled (dev / first boot).

_PUBLIC_PATHS = {"/healthz", "/ca.pem", "/ca/qr"}
_PUBLIC_PREFIXES = ("/assets/",)
_PUBLIC_API_PATHS = {
    "/api/decision",
    "/api/events",
    "/api/passthrough",
    "/api/tls-failures",
    "/api/auth/login",
}
_PUBLIC_API_PREFIXES = ("/api/dl/",)
_PUBLIC_FILES = {"/favicon.png", "/logo-64.png", "/logo-256.png", "/gandalf.png"}

# Regex used by `_dl_path_ip` to confirm a `?dl=<code>` query parameter is
# being applied to an endpoint scoped to one device's wg_ip (the only thing
# the code is authorised for). Matches the two URL shapes the enrolment
# page uses: /api/devices/{ip}/... and /api/kids/{name}/devices/{ip}/....
import re as _re
_DL_PATH_RE = _re.compile(
    r"^/api/(?:devices/(?P<ip1>[0-9.]+)(?:/|$)"
    r"|kids/[^/]+/devices/(?P<ip2>[0-9.]+)(?:/|$))"
)


def _dl_path_ip(path: str) -> str | None:
    m = _DL_PATH_RE.match(path)
    if not m:
        return None
    return m.group("ip1") or m.group("ip2")


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS or path in _PUBLIC_API_PATHS or path in _PUBLIC_FILES:
        return True
    if any(path.startswith(p) for p in _PUBLIC_API_PREFIXES):
        return True
    if path.startswith("/devices/") and (
        path.endswith("/conf")
        or path.endswith("/qr")
        or path.endswith("/android-mdm/qr.png")
        or path.endswith("/windows-mdm/package.zip")
        or path.endswith("/windows-mdm/package.ppkg")  # legacy URL
    ):
        return True
    # MDM endpoints: device-presented at TLS layer (mTLS verified by Caddy),
    # never reached by a browser; bypass the cookie auth.
    if path.startswith("/mdm/"):
        return True
    # SPA shortlink page: served as the SPA shell to anyone with the URL.
    # The page authenticates subsequent API calls via `?dl=<code>`.
    if path.startswith("/dl/"):
        return True
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


def _dl_auth_ok(request: Request) -> bool:
    """Allow `?dl=<code>` to authenticate device-scoped /api/* requests.

    The code is bound to a single wg_ip in the `device_shortlinks` table;
    we accept the request iff the IP embedded in the URL path matches."""
    code = request.query_params.get("dl")
    if not code:
        return False
    ip = _dl_path_ip(request.url.path)
    if not ip:
        return False
    bound = api_shortlinks.ip_for_code(code)
    return bound is not None and bound == ip


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if not settings.admin_password:
        return await call_next(request)
    path = request.url.path
    if _is_public(path):
        return await call_next(request)
    if auth.check_token(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    if path.startswith("/api/") and _dl_auth_ok(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    # Non-API navigation: hand back the SPA so client-side routing handles
    # the /login redirect (keeps the URL the user typed in their address bar).
    if _SPA_INDEX.exists():
        return FileResponse(str(_SPA_INDEX), headers={"Cache-Control": "no-cache"})
    return PlainTextResponse("SPA not built. Run `npm run build` in web/.", status_code=503)


# ---------------------------------------------------------------------------
# Non-API utility routes: binary / SVG / plaintext.


def _render_client_conf(ip: str) -> tuple[str, str]:
    """Render a fresh wg-quick conf from the device's priv key + current
    settings (Endpoint, DNS, subnet). Always returns up-to-date config —
    don't read the cached `.conf` file written at create time, since
    WG_HOST / WG_PORT / WG_SUBNET may have changed since.
    Returns (peer_id, conf_text)."""
    cfg = store.load()
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    kid, device = found
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"
    try:
        priv = wg.load_peer_priv(peer_id)
    except FileNotFoundError:
        raise HTTPException(500, "private key missing — re-enrol device")
    return peer_id, wg.build_client_conf(device.name, priv, device.wg_ip)


@app.get("/devices/{ip}/conf", response_class=PlainTextResponse)
def device_conf(ip: str):
    peer_id, conf = _render_client_conf(ip)
    return PlainTextResponse(
        conf,
        headers={"Content-Disposition": f'attachment; filename="{peer_id}.conf"'},
    )


@app.get("/devices/{ip}/qr")
def device_qr(ip: str):
    _, conf = _render_client_conf(ip)
    img = qrcode.make(conf, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@app.get("/ca.pem")
def download_ca():
    p = Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem")
    if not p.exists():
        raise HTTPException(404, "CA not generated yet — run ./gdlf init")
    return Response(
        content=p.read_bytes(),
        media_type="application/x-x509-ca-cert",
        headers={"Content-Disposition": 'attachment; filename="gdlf-ca.pem"'},
    )


@app.get("/ca/qr")
def ca_qr(request: Request):
    host = request.headers.get("host", "localhost:8080")
    url = f"{request.url.scheme}://{host}/ca.pem"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


# ---------------------------------------------------------------------------
# JSON API for mitmproxy + nftables sidecar. These two must stay byte-stable.


_RAW_INSERT_DECISIONS = frozenset({"block", "flag", "tls_failed", "dns_block"})
_RAW_INSERT_KINDS = frozenset({"page", "iframe"})


@app.post("/api/events")
async def post_event(request: Request):
    """mitmproxy addon posts here. Body is JSON describing the request.

    Two write paths now:
      1. Counter increment (`aggregates.record`) for EVERY event — drives
         the overview dashboard's domain panels. Flushed in batch to the
         `domain_stats` table every ~30s.
      2. Raw insert into the `events` table only for high-signal rows:
         page/iframe navigations, or any block/flag/tls_failed/dns_block
         regardless of kind. Sub-resource noise stays out.

    SSE pubsub still fans out every event so the live activity stream
    stays responsive — the table simply ignores anything not in its
    default filter.
    """
    payload = await request.json()
    cfg = store.load()
    client_ip = payload.get("client_ip", "")
    found = cfg.device_by_ip(client_ip)
    kid_name = found[0].name if found else None
    device_name = found[1].name if found else None
    decision = payload.get("decision", "allow")
    kind = payload.get("kind")
    host = payload.get("host", "")

    # Counters are kept at registrable-domain (eTLD+1) level so the overview
    # collapses `www.example.com` and `api.example.com` into one `example.com`
    # row. Raw `events` keeps the full host for forensic context.
    aggregates.record(
        kid=kid_name,
        host=registrable_domain(host),
        kind=kind,
        decision=decision,
    )

    ev = db.Event(
        source="mitmproxy",
        client_ip=client_ip,
        kid=kid_name,
        device=device_name,
        method=payload.get("method"),
        host=host,
        path=payload.get("path"),
        query=payload.get("query"),
        status=payload.get("status"),
        decision=decision,
        rule=payload.get("rule"),
        sni_only=bool(payload.get("sni_only", False)),
        kind=kind,
    )
    should_persist = (
        decision in _RAW_INSERT_DECISIONS
        or (kind or "") in _RAW_INSERT_KINDS
    )
    if should_persist:
        db.insert(ev)
    # Fan out to SSE subscribers — build the DTO from the payload (not the
    # ORM instance) to avoid SQLAlchemy detached-instance access after commit.
    pubsub.publish({
        "id": None,
        "ts": datetime.utcnow().isoformat(),
        "source": "mitmproxy",
        "client_ip": client_ip,
        "kid": kid_name,
        "device": device_name,
        "method": payload.get("method"),
        "host": payload.get("host", ""),
        "path": payload.get("path"),
        "query": payload.get("query"),
        "status": payload.get("status"),
        "decision": payload.get("decision", "allow"),
        "rule": payload.get("rule"),
        "sni_only": bool(payload.get("sni_only", False)),
        "kind": payload.get("kind"),
    })
    if payload.get("flag") or payload.get("decision") == "flag":
        asyncio.create_task(alerts.fire_for_event(ev))
    return JSONResponse({"ok": True})


def _network_block_reason(kid, device) -> str | None:
    """Schedule / manual-toggle reason for blocking, independent of URL rules.
    Mirrors nftables/reconcile.py."""
    if kid.manual_block:
        return "kid manually blocked"
    if device.manual_block:
        return "device manually blocked"

    now = datetime.now()
    bonus = kid.bonus_until
    if bonus is not None and bonus > now:
        return None
    window = kid.schedule.weekend if now.weekday() >= 5 else kid.schedule.weekday
    if not _in_any_window(now, window.allowed):
        return "outside allowed hours"
    return None


def _in_any_window(now, spec: str) -> bool:
    """'07:00-21:00,22:00-23:00' → True if `now` falls in any window."""
    import re
    mins = now.hour * 60 + now.minute
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", chunk)
        if not m:
            continue
        a = int(m.group(1)) * 60 + int(m.group(2))
        b = int(m.group(3)) * 60 + int(m.group(4))
        if a <= b:
            if a <= mins < b:
                return True
        else:
            if mins >= a or mins < b:
                return True
    return False


@app.get("/api/passthrough")
def get_passthrough():
    """Per-kid + global passthrough lists consumed by the mitmproxy addon.

    The addon polls this every ~30s and consults it in `tls_clienthello`:
      * `by_ip`   — per-kid opt-in passthrough (pinned-cert apps that refuse
                    our CA, recorded via the TLS-failures table).
      * `globals` — vendor bulk-content CDNs that we never want to MITM
                    regardless of kid (game downloads, OS updates, etc.).
                    Sourced from `gdlf.bulk_cdns.BULK_CDN_PATTERNS`.

    Public-but-trusted: the mitm container runs in wg's netns and reaches
    rules-svc over the bridge with no cookie.
    """
    cfg = store.load()
    out: dict[str, list[str]] = {}
    blocked: list[str] = []
    for kid in cfg.kids:
        for d in kid.devices:
            if not d.wg_ip:
                continue
            if kid.mitm_passthrough_hosts:
                out[d.wg_ip] = list(kid.mitm_passthrough_hosts)
            # Mirror nftables' blocked_clients set so the addon can refuse to
            # passthrough for clients that are currently network-blocked. Without
            # this, TLS passthrough would silently bypass schedule / manual
            # blocks (since nft's :443 reject deliberately exempts mitm clients
            # and trusts mitmproxy to enforce policy at the decision layer).
            if _network_block_reason(kid, d):
                blocked.append(d.wg_ip)
    return JSONResponse({
        "by_ip": out,
        "globals": list(bulk_cdns.BULK_CDN_PATTERNS),
        "blocked_ips": blocked,
    })


@app.get("/api/bulk-cdns")
def get_bulk_cdns():
    """Grouped global CDN passthrough list — for display in the dashboard.

    The dashboard's Passthrough tab renders this read-only alongside the
    per-kid opt-in list, so the parent can see exactly which vendors are
    being skipped by default.
    """
    return JSONResponse({
        "groups": [
            {"vendor": vendor, "patterns": list(patterns)}
            for vendor, patterns in bulk_cdns.BULK_CDN_GROUPS.items()
        ],
        "total": len(bulk_cdns.BULK_CDN_PATTERNS),
    })


@app.post("/api/decision")
async def post_decision(request: Request):
    """mitmproxy asks: 'for this request, what should I do?'

    Body: {"client_ip": "10.13.13.10", "host": "youtube.com",
           "path": "/shorts/abc", "query": "v=x"}
    """
    payload = await request.json()
    cfg = store.load()
    found = cfg.device_by_ip(payload.get("client_ip", ""))
    if not found:
        return JSONResponse({"action": "allow", "kid": None, "rule": None, "flag": False})
    kid, device = found

    reason = _network_block_reason(kid, device)
    if reason:
        return JSONResponse(
            {
                "action": "block",
                "kid": kid.name,
                "kid_age": kid.age,
                "rule": reason,
                "flag": False,
            }
        )

    host = payload.get("host", "")
    decision = evaluate(kid, host, payload.get("path", "/"), payload.get("query"))
    if decision.action == "allow":
        # AdGuard handles DNS-layer blocking, but a device that cached an IP
        # (or uses DoH/DoT to bypass) reaches mitm directly. Enforce the same
        # blocked_apps list here using the catalog's host index.
        svc = adguard.host_blocked_service_for_kid(kid, host)
        if svc:
            return JSONResponse(
                {
                    "action": "block",
                    "kid": kid.name,
                    "kid_age": kid.age,
                    "rule": f"blocked service: {svc}",
                    "flag": False,
                }
            )
    return JSONResponse(
        {
            "action": decision.action,
            "kid": kid.name,
            "kid_age": kid.age,
            "rule": (decision.rule.match if decision.rule else None),
            "flag": decision.flag,
        }
    )


# ---------------------------------------------------------------------------
# SPA assets + catch-all index. Declared LAST so explicit routes above win.


def _spa_file(name: str) -> FileResponse:
    p = _SPA_ROOT / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p))


@app.get("/favicon.png")
def favicon():
    return _spa_file("favicon.png")


@app.get("/logo-64.png")
def logo_64():
    return _spa_file("logo-64.png")


@app.get("/logo-256.png")
def logo_256():
    return _spa_file("logo-256.png")


@app.get("/gandalf.png")
def gandalf():
    return _spa_file("gandalf.png")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    """Hand any unmatched non-API path to the SPA's index.html."""
    if full_path.startswith("api/"):
        raise HTTPException(404)
    if not _SPA_INDEX.exists():
        raise HTTPException(503, "SPA not built — run `npm run build` in web/")
    return FileResponse(str(_SPA_INDEX), headers={"Cache-Control": "no-cache"})
