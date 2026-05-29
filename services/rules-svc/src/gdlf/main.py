"""rules-svc entrypoint.

The HTTP surface is split into two:

  * `/api/*`   — JSON, consumed by the React SPA *and* the mitmproxy addon
                (POST /api/decision, POST /api/events).
  * everything else — the SPA shell. Vite-fingerprinted assets live under
                /assets; the catch-all serves index.html so the client
                router can handle deep links.

Non-API utility routes (`/ca.pem`, `/ca/qr`, `/devices/{ip}/conf`,
`/devices/{ip}/qr`, `/healthz`) return binary / SVG / plaintext and stay
where they are. Device config routes require cookie auth; shared enrolment
uses code-only `/api/dl/{code}/conf` + `/api/dl/{code}/qr`.
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
from sse_starlette.sse import EventSourceResponse

from . import (
    adguard,
    alerts,
    api_activity,
    api_android_mdm,
    api_auth,
    api_devices,
    api_kids,
    api_mdm,
    api_resources,
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
    rules,
    store,
    wg,
)
from .api_tls_failures import registrable_domain
from .amapi import orchestrator as amapi_orchestrator
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
    adguard_watchdog_task = asyncio.create_task(adguard.health_watchdog_loop())
    prune_task = asyncio.create_task(_prune_loop())
    amapi_task = asyncio.create_task(amapi_orchestrator.status_sync_loop())
    amapi_watch_task = asyncio.create_task(_amapi_policy_watch_loop())
    try:
        yield
    finally:
        sync_task.cancel()
        adguard_watchdog_task.cancel()
        prune_task.cancel()
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
            res = db.prune(settings.retention_days, settings.max_events)
            if res["age_deleted"] or res["cap_deleted"]:
                log.info(
                    "pruned: age=%d cap=%d",
                    res["age_deleted"], res["cap_deleted"],
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
app.include_router(api_resources.router)

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
    "/api/passthrough/stream",
    "/api/tls-failures",
    "/api/auth/login",
}
_PUBLIC_API_PREFIXES = ("/api/dl/",)
_PUBLIC_FILES = {"/favicon.png", "/logo-64.png", "/logo-256.png", "/gandalf.png"}


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS or path in _PUBLIC_API_PATHS or path in _PUBLIC_FILES:
        return True
    if any(path.startswith(p) for p in _PUBLIC_API_PREFIXES):
        return True
    # MDM endpoints: device-presented at TLS layer (mTLS verified by Caddy),
    # never reached by a browser; bypass the cookie auth.
    if path.startswith("/mdm/"):
        return True
    # SPA shortlink page: served as the SPA shell to anyone with the URL.
    # Code-only download helpers under /dl/* are explicit routes declared
    # before the SPA fallback; the fallback itself handles /dl/<code>.
    if path.startswith("/dl/"):
        return True
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if not settings.admin_password:
        return await call_next(request)
    path = request.url.path
    if _is_public(path):
        return await call_next(request)
    if auth.check_token(request.cookies.get(auth.COOKIE)):
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


# Always recorded regardless of `kind`: blocks/flags/TLS failures/DNS
# blocks are low-volume and parents need to see every one.
_KEEP_DECISIONS = frozenset({"block", "flag", "tls_failed", "dns_block"})
# MITM navigation kinds — recorded with their URL path. `unknown` is
# included because the classifier in `addons/mitm_capture.py:_classify`
# is heuristic; under-logging real navigations is worse than the small
# amount of noise from misclassified XHRs that fall here.
_PAGE_KINDS = frozenset({"page", "iframe", "unknown"})


@app.post("/api/events")
async def post_event(request: Request):
    """mitmproxy addon posts here. Body is JSON describing the request.

    One ingest rule (no more split addon+server filtering):
      * Always keep blocks / flags / tls_failures / dns_blocks.
      * SNI splice events (`kind=sni`) → recorded; repeat visits to the
        same (kid, host) within `stats_bucket_secs` collapse into one row
        via `db.insert_or_bump`.
      * MITM page navigations (`kind in {page, iframe, unknown}`) →
        recorded with full path; same-URL refreshes inside the window
        collapse too.
      * Anything else (asset / xhr / ws / pinned with no decision) is
        dropped — sub-resource noise the dashboard would otherwise drown
        in.

    The SSE channel emits a single `{kind:"changed", kid}` ping after a
    successful insert; the SPA refetches `/api/activity` and `/api/stats`
    so the feed, counters, and per-kid tiles stay byte-for-byte in sync.
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

    keep = (
        decision in _KEEP_DECISIONS
        or kind == "sni"
        or kind == "pinned"
        or (kind or "") in _PAGE_KINDS
    )
    if not keep:
        return JSONResponse({"ok": True, "stored": False})

    event_id, was_new = db.insert_or_bump(
        source="mitmproxy",
        client_ip=client_ip,
        kid=kid_name,
        device=device_name,
        method=payload.get("method"),
        host=host,
        registrable=registrable_domain(host) if host else None,
        path=payload.get("path"),
        query=payload.get("query"),
        status=payload.get("status"),
        decision=decision,
        rule=payload.get("rule"),
        sni_only=bool(payload.get("sni_only", False)),
        kind=kind,
        note=payload.get("note"),
    )

    pubsub.publish({"kind": "changed", "kid": kid_name})

    # Alert only when a new row is created, not on every bump — otherwise a
    # single bad URL retried 50 times in a bucket would page the parent 50
    # times. `was_new=False` means the row already existed for this bucket
    # and we already alerted on it.
    if was_new and (payload.get("flag") or decision == "flag"):
        alert_ev = db.get_event(event_id)
        if alert_ev is not None:
            asyncio.create_task(alerts.fire_for_event(alert_ev))

    return JSONResponse({"ok": True, "stored": True, "event_id": event_id, "new": was_new})


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
    """Per-kid + global lists consumed by the mitmproxy addon.

    Splice-by-default: in `tls_clienthello` the addon only MITMs a flow when
    its SNI matches the inspect list; everything else is `ignore_connection`.
    The addon wakes on `/api/passthrough/stream` (SSE) and re-fetches here;
    falls back to a 30s poll if the stream drops.

      * `inspect_by_ip` — per-kid SNIs (globs) we DECRYPT for URL-path rules.
                          Built from `INSPECT_GLOBAL_DEFAULTS` ∪ each kid's
                          `mitm_inspect_hosts`.
      * `blocked_ips`   — mirror of nftables' `blocked_clients` set: the addon
                          refuses to splice for these so the connection falls
                          through to mitm and `/api/decision` serves the block
                          page.

    Legacy keys retained for one release as belt-and-suspenders (the addon
    treats passthrough as a hard override that wins over inspect):
      * `by_ip`   — per-kid opt-in passthrough list (`mitm_passthrough_hosts`).
      * `globals` — vendor bulk-content CDN globs (`BULK_CDN_PATTERNS`).

    Public-but-trusted: the mitm container runs in wg's netns and reaches
    rules-svc over the bridge with no cookie.
    """
    cfg = store.load()
    out: dict[str, list[str]] = {}
    inspect: dict[str, list[str]] = {}
    blocked: list[str] = []
    for kid in cfg.kids:
        # Effective MITM list = explicit inspect hosts ∪ any host with a
        # block/flag rule. Without the union, domain-only block rules would
        # silently never fire (the addon would splice, /api/decision never
        # called). See `rules.hosts_with_block_or_flag_rules` rationale.
        kid_inspect = rules.effective_inspect_hosts(kid)
        for d in kid.devices:
            if not d.wg_ip:
                continue
            if kid.mitm_passthrough_hosts:
                out[d.wg_ip] = list(kid.mitm_passthrough_hosts)
            inspect[d.wg_ip] = kid_inspect
            if _network_block_reason(kid, d):
                blocked.append(d.wg_ip)
    return JSONResponse({
        "by_ip": out,
        "inspect_by_ip": inspect,
        "globals": list(bulk_cdns.BULK_CDN_PATTERNS),
        "blocked_ips": blocked,
    })


@app.get("/api/passthrough/stream")
async def passthrough_stream(request: Request) -> EventSourceResponse:
    """SSE wake channel for the mitmproxy addon.

    Emits a `config-changed` ping per kids.yaml mutation so the addon can
    re-fetch `/api/passthrough` instantly instead of polling on a 30s
    cycle. The 15s heartbeat keeps the connection through aggressive
    intermediaries; the addon falls back to polling on disconnect.

    Same public-but-trusted stance as `/api/passthrough` (the mitm
    container reaches us over the gdlf bridge, no cookie).
    """
    async def gen():
        async for _ in pubsub.subscribe_config_changes():
            if await request.is_disconnected():
                break
            yield {"event": "config-changed", "data": "{}"}

    return EventSourceResponse(gen(), ping=15)


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
    decision = rules.evaluate(kid, host, payload.get("path", "/"), payload.get("query"))
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
            "rule": (
                (decision.rule.host + (decision.rule.path or ""))
                if decision.rule else None
            ),
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
