"""rules-svc entrypoint.

Routes:
  /                         -> redirect to /kids
  /kids                     -> kids list
  /kids/{name}              -> kid detail (tabs)
  /kids/{name}/devices/new  -> enrolment wizard (GET form + POST create)
  /devices/{ip}/conf        -> download .conf
  /devices/{ip}/qr          -> SVG QR
  /devices/{ip}/handshake   -> JSON: latest handshake (polled by wizard)
  /activity                 -> request feed
  /rules                    -> rules library
  /settings                 -> webhook/email/CA
  /api/events               -> POST from mitmproxy addon
  /api/decision             -> POST query (host/path/client_ip) -> JSON decision
  /healthz                  -> "ok"
"""
from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import qrcode
import qrcode.image.svg
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import adguard, alerts, auth, db, store, wg
from .rules import evaluate
from .schema import Device, Kid, URLRule
from .settings import settings
import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure server keys exist and wg0.conf reflects current kids.yaml.
    wg.ensure_server_keys()
    try:
        cfg = store.load(force=True)
        wg.write_wg0_conf(cfg)
    except FileNotFoundError:
        pass
    # Touch DB so tables get created.
    db.engine()
    # Background loops: AdGuard sync + event-table prune.
    sync_task = asyncio.create_task(adguard.sync_loop())
    prune_task = asyncio.create_task(_prune_loop())
    try:
        yield
    finally:
        sync_task.cancel()
        prune_task.cancel()


async def _prune_loop():
    """Trim the events table hourly; VACUUM once a day."""
    import logging
    log = logging.getLogger("gdlf.prune")
    runs = 0
    while True:
        try:
            res = db.prune(settings.retention_days, settings.max_events)
            if res["age_deleted"] or res["cap_deleted"]:
                log.info("pruned: age=%d cap=%d", res["age_deleted"], res["cap_deleted"])
            runs += 1
            if runs % 24 == 0:  # ~ once per day
                db.vacuum()
                log.info("vacuumed db")
        except Exception as e:
            log.warning("prune failed: %s", e)
        await asyncio.sleep(3600)


app = FastAPI(title="gdlf rules-svc", lifespan=lifespan)

_pkg_root = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(_pkg_root / "templates"))
app.mount("/static", StaticFiles(directory=str(_pkg_root / "static")), name="static")

# Cache-bust the static CSS by stamping the CSS file's mtime onto every link.
# Saves the user from having to hard-refresh after we ship layout changes.
_CSS_PATH = _pkg_root / "static" / "app.css"


def _css_v() -> str:
    try:
        return str(int(_CSS_PATH.stat().st_mtime))
    except OSError:
        return "1"


templates.env.globals["css_v"] = _css_v


# ---------------------------------------------------------------------------
# Helpers


def _ctx(**extra) -> dict:
    cfg = store.load()
    handshakes = wg.wg_show_handshakes()
    # `now` is the container's local time (TZ env honoured) so schedule /
    # bonus comparisons in templates match what the nftables sidecar sees.
    return {"cfg": cfg, "handshakes": handshakes, "now": datetime.now(), **extra}


def _render(request: Request, name: str, **extra) -> HTMLResponse:
    return templates.TemplateResponse(request, name, _ctx(**extra))


def _kid_or_404(name: str) -> Kid:
    cfg = store.load()
    kid = cfg.kid(name)
    if not kid:
        raise HTTPException(404, f"unknown kid {name}")
    return kid


# ---------------------------------------------------------------------------
# Auth: cookie-checked middleware. Public paths bypass it (mitmproxy API,
# health, CA download for the kid's device, the login page itself, static).
#
# When RULES_SVC_ADMIN_PASSWORD is empty, auth is disabled — first-boot &
# dev convenience.

_PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/ca.pem", "/ca/qr"}
_PUBLIC_PREFIXES = ("/static/", "/api/")


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if not settings.admin_password:
        return await call_next(request)
    if _is_public(request.url.path):
        return await call_next(request)
    if auth.check_token(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    # Preserve the requested URL so we can bounce back after login.
    nxt = request.url.path
    if request.url.query:
        nxt += "?" + request.url.query
    return RedirectResponse(f"/login?next={nxt}", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/kids", error: str = ""):
    # Render directly — _render() expects kids.yaml context we don't need here.
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": next, "error": error, "now": datetime.utcnow()},
    )


@app.post("/login")
def login_submit(password: str = Form(...), next: str = Form("/kids")):
    if not auth.check_password(password):
        return RedirectResponse(f"/login?next={next}&error=1", status_code=303)
    # Sanitize redirect: only allow same-origin paths.
    if not next.startswith("/") or next.startswith("//"):
        next = "/kids"
    resp = RedirectResponse(next, status_code=303)
    resp.set_cookie(
        auth.COOKIE,
        auth.make_token(),
        max_age=auth.MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


# ---------------------------------------------------------------------------
# Dashboard pages


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/kids", status_code=302)


@app.get("/kids", response_class=HTMLResponse)
def kids_index(request: Request):
    return _render(request, "kids_index.html")


@app.post("/kids", response_class=HTMLResponse)
def kids_create(
    request: Request,
    name: str = Form(...),
    age: int | None = Form(None),
    schedule_weekday: str = Form("07:00-21:00"),
    schedule_weekend: str = Form("08:00-22:00"),
):
    name = name.strip()
    if not name:
        raise HTTPException(400, "name required")

    def add(cfg):
        if cfg.kid(name):
            raise HTTPException(400, f"kid {name!r} already exists")
        cfg.kids.append(
            Kid(
                name=name,
                age=age,
                schedule={  # type: ignore[arg-type]
                    "weekday": {"allowed": schedule_weekday},
                    "weekend": {"allowed": schedule_weekend},
                },
            )
        )

    store.mutate(add)
    return RedirectResponse(f"/kids/{name}", status_code=303)


@app.get("/kids/{name}", response_class=HTMLResponse)
def kid_detail(request: Request, name: str, tab: str = "devices"):
    kid = _kid_or_404(name)
    return _render(request, "kid_detail.html", kid=kid, tab=tab)


@app.post("/kids/{name}/schedule")
def kid_schedule_update(
    name: str,
    schedule_weekday: str = Form(...),
    schedule_weekend: str = Form(...),
):
    """Update a kid's allowed-hours windows. The nftables sidecar picks the
    new schedule up on its next reconcile cycle (~30s)."""
    from .schema import Schedule, ScheduleWindow

    # Validate format up front so a typo doesn't poison kids.yaml.
    try:
        Schedule(
            weekday=ScheduleWindow(allowed=schedule_weekday.strip()),
            weekend=ScheduleWindow(allowed=schedule_weekend.strip()),
        )
    except Exception as e:
        raise HTTPException(400, f"invalid schedule: {e}")

    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.schedule.weekday.allowed = schedule_weekday.strip()
        kid.schedule.weekend.allowed = schedule_weekend.strip()

    store.mutate(upd)
    return RedirectResponse(f"/kids/{name}?tab=schedule", status_code=303)


@app.post("/kids/{name}/bonus")
def kid_bonus(name: str, minutes: int = Form(...)):
    """Grant bonus time: extend the allowed window by `minutes` from now.
    Repeated calls extend cumulatively from whichever is later (current
    bonus_until or now)."""
    if minutes <= 0 or minutes > 24 * 60:
        raise HTTPException(400, "minutes must be 1..1440")

    def add(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        now = datetime.now()  # naive local — matches nftables sidecar TZ
        base = kid.bonus_until if kid.bonus_until and kid.bonus_until > now else now
        kid.bonus_until = base + timedelta(minutes=minutes)

    store.mutate(add)
    return RedirectResponse(f"/kids/{name}?tab=schedule", status_code=303)


@app.post("/kids/{name}/bonus/clear")
def kid_bonus_clear(name: str):
    def clr(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.bonus_until = None

    store.mutate(clr)
    return RedirectResponse(f"/kids/{name}?tab=schedule", status_code=303)


@app.post("/kids/{name}/block")
def kid_block(name: str, blocked: bool = Form(True)):
    """Toggle the kid-wide manual block — all their devices go offline."""
    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.manual_block = bool(blocked)

    store.mutate(upd)
    return RedirectResponse(f"/kids/{name}", status_code=303)


@app.post("/devices/{ip}/block")
def device_block(ip: str, blocked: bool = Form(True)):
    """Toggle the per-device manual block. nftables picks it up next cycle."""
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    kid_name = found[0].name

    def upd(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    d.manual_block = bool(blocked)

    store.mutate(upd)
    return RedirectResponse(f"/kids/{kid_name}", status_code=303)


@app.post("/kids/{name}/delete")
def kid_delete(name: str):
    def rm(cfg):
        cfg.kids = [k for k in cfg.kids if k.name != name]

    store.mutate(rm)
    # Re-render wg0 because their peers are gone.
    wg.write_wg0_conf(store.load(force=True))
    wg.reload_wg()
    return RedirectResponse("/kids", status_code=303)


# ---------------------------------------------------------------------------
# Device enrolment wizard


@app.get("/kids/{name}/devices/new", response_class=HTMLResponse)
def device_new(request: Request, name: str):
    kid = _kid_or_404(name)
    return _render(request, "device_new.html", kid=kid)


@app.post("/kids/{name}/devices/new", response_class=HTMLResponse)
def device_create(
    request: Request,
    name: str,
    device_name: str = Form(...),
    platform: str = Form(...),
):
    kid = _kid_or_404(name)
    device_name = device_name.strip()
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device_name)}"

    cfg = store.load(force=True)
    ip = wg.allocate_ip(cfg)
    priv, pub = wg.generate_keypair()
    wg.save_peer_keys(peer_id, priv, pub)

    def add(cfg):
        k = cfg.kid(kid.name)
        if any(d.wg_ip == ip for _, d in cfg.all_devices()):
            raise HTTPException(500, f"IP collision on {ip}")
        k.devices.append(
            Device(name=device_name, platform=platform, wg_ip=ip, wg_public_key=pub)
        )

    store.mutate(add)

    # Write the conf next to the peer keys for download.
    conf = wg.build_client_conf(peer_id, priv, ip)
    (settings.state_dir / "wg-keys" / f"{peer_id}.conf").write_text(conf)

    # Live-reload the server so the new peer can connect.
    wg.write_wg0_conf(store.load(force=True))
    wg.reload_wg()

    return RedirectResponse(
        f"/kids/{kid.name}/devices/{ip}/enrol", status_code=303
    )


@app.get("/kids/{name}/devices/{ip}/enrol", response_class=HTMLResponse)
def device_enrol(request: Request, name: str, ip: str):
    kid = _kid_or_404(name)
    device = next((d for d in kid.devices if d.wg_ip == ip), None)
    if not device:
        raise HTTPException(404, "unknown device")
    return _render(request, "device_enrol.html", kid=kid, device=device)


@app.get("/devices/{ip}/conf", response_class=PlainTextResponse)
def device_conf(ip: str):
    cfg = store.load()
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    _kid, device = found
    peer_id = f"{wg.slug(_kid.name)}__{wg.slug(device.name)}"
    conf_p = settings.state_dir / "wg-keys" / f"{peer_id}.conf"
    if not conf_p.exists():
        raise HTTPException(500, "client conf missing — re-enrol device")
    return PlainTextResponse(
        conf_p.read_text(),
        headers={"Content-Disposition": f'attachment; filename="{peer_id}.conf"'},
    )


@app.get("/devices/{ip}/qr")
def device_qr(ip: str):
    cfg = store.load()
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    _kid, device = found
    peer_id = f"{wg.slug(_kid.name)}__{wg.slug(device.name)}"
    conf_p = settings.state_dir / "wg-keys" / f"{peer_id}.conf"
    img = qrcode.make(conf_p.read_text(), image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@app.get("/devices/{ip}/handshake", response_class=HTMLResponse)
def device_handshake(request: Request, ip: str):
    """HTML fragment for the enrolment wizard poller, or JSON for API callers."""
    hs = wg.wg_show_handshakes().get(ip, {})
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(hs)
    last = hs.get("last_handshake", 0)
    if last == 0:
        return HTMLResponse('<p class="muted">Waiting for first handshake…</p>')
    age = int(datetime.utcnow().timestamp() - last)
    return HTMLResponse(
        f'<p><span class="badge online">Connected</span> '
        f'last handshake {age}s ago · '
        f'{hs.get("rx",0)//1024} KiB rx / {hs.get("tx",0)//1024} KiB tx</p>'
    )


def _suggest_match(host: str, path: str) -> str:
    """Build a sensible match pattern from an observed host+path.

    Examples:
      youtube.com  /shorts/abc       -> youtube.com/shorts/*
      google.com   /search           -> google.com/search
      reddit.com   /r/teens/comments -> reddit.com/r/teens/*
      example.com  /                 -> example.com
      example.com  (no path)         -> example.com
    """
    host = (host or "").strip().lower()
    path = (path or "").strip() or "/"
    if path in ("", "/"):
        return host
    segs = [s for s in path.split("/") if s]
    if not segs:
        return host
    if len(segs) == 1:
        return f"{host}/{segs[0]}"
    # Walk up to two segments deep, then wildcard.
    take = segs[:2] if len(segs) > 2 else segs[:1]
    return f"{host}/{'/'.join(take)}/*"


@app.get("/kids/{name}/rules/new", response_class=HTMLResponse)
def kid_rule_new(
    request: Request,
    name: str,
    host: str = "",
    path: str = "",
    query: str = "",
):
    """Pre-filled new-rule form, typically arrived at from an Activity row."""
    kid = _kid_or_404(name)
    suggested = _suggest_match(host, path)
    return _render(
        request, "rule_new.html",
        kid=kid, suggested=suggested, src_host=host, src_path=path, src_query=query,
    )


@app.post("/kids/{name}/rules/add")
def kid_rule_add(
    name: str,
    action: str = Form(...),
    match: str = Form(...),
    query: str = Form(""),
    flag: bool = Form(False),
    note: str = Form(""),
):
    if action not in {"block", "allow", "flag"}:
        raise HTTPException(400, "action must be block|allow|flag")

    def add(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.url_rules.append(
            URLRule(
                action=action,
                match=match.strip(),
                query=(query.strip() or None),
                flag=bool(flag),
                note=(note.strip() or None),
            )
        )

    store.mutate(add)
    return RedirectResponse(f"/kids/{name}?tab=rules", status_code=303)


@app.post("/kids/{name}/rules/{idx}/delete")
def kid_rule_delete(name: str, idx: int):
    def rm(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        if 0 <= idx < len(kid.url_rules):
            del kid.url_rules[idx]

    store.mutate(rm)
    return RedirectResponse(f"/kids/{name}?tab=rules", status_code=303)


@app.post("/kids/{name}/rules/{idx}/move")
def kid_rule_move(name: str, idx: int, dir: str = Form(...)):
    """Re-order rules — first-match-wins semantics make order matter."""
    def mv(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        n = len(kid.url_rules)
        if not (0 <= idx < n):
            return
        new = idx - 1 if dir == "up" else idx + 1
        if 0 <= new < n:
            kid.url_rules[idx], kid.url_rules[new] = kid.url_rules[new], kid.url_rules[idx]

    store.mutate(mv)
    return RedirectResponse(f"/kids/{name}?tab=rules", status_code=303)


@app.post("/devices/{ip}/delete")
def device_delete(ip: str):
    """Remove a device: drop it from kids.yaml, delete its keys, reload wg."""
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    kid, device = found
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"
    kid_name = kid.name

    def remove(cfg):
        k = cfg.kid(kid_name)
        k.devices = [d for d in k.devices if d.wg_ip != ip]

    store.mutate(remove)
    # Delete on-disk keys + conf.
    for ext in ("priv", "pub", "conf"):
        p = settings.state_dir / "wg-keys" / f"{peer_id}.{ext}"
        if p.exists():
            p.unlink()
    wg.write_wg0_conf(store.load(force=True))
    wg.reload_wg()
    return RedirectResponse(f"/kids/{kid_name}", status_code=303)


@app.post("/devices/{ip}/regenerate")
def device_regenerate(ip: str):
    """Rotate the device's WireGuard keypair and re-render its .conf.

    Use this when the kid's device has a stale tunnel (e.g. WG_HOST
    changed, or you want to revoke and reissue without losing the slot).
    The existing peer-id stays the same so the dashboard URL still works.
    """
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    kid, device = found
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"

    priv, pub = wg.generate_keypair()
    wg.save_peer_keys(peer_id, priv, pub)

    def rotate(cfg):
        for d in cfg.kid(kid.name).devices:
            if d.wg_ip == ip:
                d.wg_public_key = pub
                d.mitm_ca_installed = False  # they'll re-enrol

    store.mutate(rotate)

    conf = wg.build_client_conf(peer_id, priv, ip)
    (settings.state_dir / "wg-keys" / f"{peer_id}.conf").write_text(conf)

    wg.write_wg0_conf(store.load(force=True))
    wg.reload_wg()

    return RedirectResponse(
        f"/kids/{kid.name}/devices/{ip}/enrol", status_code=303
    )


@app.post("/devices/{ip}/mitm-installed")
def device_mark_mitm(ip: str, installed: bool = Form(True)):
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    kid_name = found[0].name

    def mark(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    d.mitm_ca_installed = installed

    store.mutate(mark)
    return RedirectResponse(f"/kids/{kid_name}", status_code=303)


# ---------------------------------------------------------------------------
# Activity / rules / settings (stubs filled in later tasks)


def _activity_events(kid, decision, include_sni, include_assets):
    """Pull recent events, filter out sub-resources + SNI by default. We
    over-fetch when filtering so the visible 50 rows reflect 50 real events
    rather than 50 mixed (mostly-asset) ones."""
    raw_limit = 50 if (include_sni and include_assets) else 1000
    events = db.recent_events(limit=raw_limit, kid=kid, decision=decision)
    if not include_sni:
        events = [e for e in events if e.decision != "sni_only"]
    if not include_assets:
        # Hide assets, xhr, websocket and iframe noise — only true page
        # navigations are shown by default. NULL kind = legacy event
        # (pre-classifier) and we treat as page so nothing vanishes.
        events = [e for e in events if (e.kind or "page") == "page"]
    return events[:50]


@app.get("/activity", response_class=HTMLResponse)
def activity(
    request: Request,
    kid: str | None = None,
    decision: str | None = None,
    sni: bool = False,
    assets: bool = False,
):
    events = _activity_events(kid, decision, include_sni=sni, include_assets=assets)
    return _render(
        request, "activity.html",
        events=events, filter_kid=kid, filter_decision=decision,
        include_sni=sni, include_assets=assets,
    )


@app.get("/activity/rows", response_class=HTMLResponse)
def activity_rows(
    request: Request,
    kid: str | None = None,
    decision: str | None = None,
    sni: bool = False,
    assets: bool = False,
):
    """HTMX-polled fragment: just the table body rows. Cheap to render."""
    events = _activity_events(kid, decision, include_sni=sni, include_assets=assets)
    return _render(request, "_activity_rows.html", events=events)


@app.get("/rules", response_class=HTMLResponse)
def rules_index(request: Request):
    return _render(request, "rules_index.html")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    ca_path = Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem")
    ca_present = ca_path.exists()
    return _render(request, "settings.html",
                   ca_present=ca_present,
                   db_stats=db.stats(),
                   retention_days=settings.retention_days,
                   max_events=settings.max_events)


@app.post("/settings/prune-now")
def settings_prune_now():
    db.prune(settings.retention_days, settings.max_events)
    db.vacuum()
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------------------------
# JSON API for mitmproxy + nftables sidecar


@app.post("/api/events")
async def post_event(request: Request):
    """mitmproxy addon posts here. Body is JSON describing the request."""
    payload = await request.json()
    cfg = store.load()
    client_ip = payload.get("client_ip", "")
    found = cfg.device_by_ip(client_ip)
    kid_name = found[0].name if found else None
    device_name = found[1].name if found else None
    ev = db.Event(
        source="mitmproxy",
        client_ip=client_ip,
        kid=kid_name,
        device=device_name,
        method=payload.get("method"),
        host=payload.get("host", ""),
        path=payload.get("path"),
        query=payload.get("query"),
        status=payload.get("status"),
        decision=payload.get("decision", "allow"),
        rule=payload.get("rule"),
        sni_only=bool(payload.get("sni_only", False)),
        kind=payload.get("kind"),
    )
    db.insert(ev)
    # Use payload (not ev) to dodge SQLAlchemy detached-instance after commit.
    if payload.get("flag") or payload.get("decision") == "flag":
        asyncio.create_task(alerts.fire_for_event(ev))
    return JSONResponse({"ok": True})


def _network_block_reason(kid, device) -> str | None:
    """Return a short reason string when network access should be denied
    independent of URL rules — manual kid/device toggle, or out-of-schedule
    (unless bonus is active). Mirrors nftables/reconcile.py so the block
    page and the firewall stay in sync. None = no block reason."""
    if kid.manual_block:
        return "kid manually blocked"
    if device.manual_block:
        return "device manually blocked"

    now = datetime.now()
    bonus = kid.bonus_until
    if bonus is not None and bonus > now:
        return None  # bonus time overrides schedule

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
        # Unknown client — let it through, mark as unmapped.
        return JSONResponse({"action": "allow", "kid": None, "rule": None, "flag": False})
    kid, device = found

    # Schedule / manual blocks first — they trump URL rules. mitmproxy will
    # serve the block page over HTTPS for these.
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

    decision = evaluate(kid, payload.get("host", ""), payload.get("path", "/"), payload.get("query"))
    return JSONResponse(
        {
            "action": decision.action,
            "kid": kid.name,
            "kid_age": kid.age,
            "rule": (decision.rule.match if decision.rule else None),
            "flag": decision.flag,
        }
    )


@app.get("/ca.pem")
def download_ca():
    """Serve the mitmproxy CA so devices can install it during enrolment."""
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
    """QR code that, when scanned on a device, opens the CA download URL.

    Uses the request's Host header so the URL is what the parent can see —
    e.g. if they loaded the dashboard at http://192.168.178.128:8080 then
    the QR encodes http://192.168.178.128:8080/ca.pem, which the phone
    (on the same WiFi) can reach.
    """
    host = request.headers.get("host", "localhost:8080")
    url = f"{request.url.scheme}://{host}/ca.pem"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"
