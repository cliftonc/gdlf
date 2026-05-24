"""MDM HTTP surface — admin enrolment endpoint + public Apple endpoints.

  POST /api/devices/{ip}/mdm/enroll-token  (admin: dashboard, cookie-auth)
       Issues a one-time token + the URL the parent feeds into Apple
       Configurator 2 on a Mac.

  GET  /mdm/enroll/{token}                 (public)
       Mints a per-device identity cert, builds a fresh .mobileconfig,
       records the identity CN on the Device.mdm, and returns the profile
       with the Apple-aspen-config content type.

  POST /mdm/checkin                        (public; gated by mTLS at Caddy)
       Apple's Authenticate / TokenUpdate / CheckOut messages.

  POST /mdm/server                         (public; gated by mTLS at Caddy)
       Phase 3: command poll + response. Stubbed to 204 for now so
       enrolled devices stop seeing 502s.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import select

from . import db, store
from .schema import MdmState
from .settings import settings
from .mdm import apns, checkin, commands, enrollment, orchestrator, server

router = APIRouter(tags=["mdm"])

ENROLL_TOKEN_TTL = timedelta(minutes=30)


# --- Admin: issue an enrollment token --------------------------------------


class EnrollTokenResponse(BaseModel):
    token: str
    enroll_url: str       # full URL the parent opens (or imports into Configurator)
    expires_at: datetime


@router.post("/api/devices/{ip}/mdm/enroll-token")
def create_enroll_token(ip: str) -> EnrollTokenResponse:
    if not settings.mdm_base_url:
        raise HTTPException(503, "MDM_BASE_URL not configured")

    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")

    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires_at = now + ENROLL_TOKEN_TTL
    with db.session() as s:
        s.add(db.MdmEnrollToken(
            token=token,
            wg_ip=ip,
            created_at=now,
            expires_at=expires_at,
        ))
        s.commit()

    # Mark the device as enrollment-pending so the dashboard reflects state.
    # The actual MdmState is overwritten when the profile is fetched (we
    # only know the identity_cn / serial after minting).
    def mark_pending(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip and d.mdm is None:
                    d.mdm = MdmState(
                        identity_cn=f"pending-{token[:12]}",
                        identity_cert_serial="",
                        status="pending",
                    )
    store.mutate(mark_pending)

    return EnrollTokenResponse(
        token=token,
        enroll_url=f"{settings.mdm_base_url}/mdm/enroll/{token}",
        expires_at=expires_at,
    )


# --- Public: serve the .mobileconfig ---------------------------------------


@router.get("/mdm/enroll/{token}")
def serve_enrollment_profile(token: str) -> Response:
    """Apple Configurator (or Safari) fetches this. We mint a fresh identity
    cert per request and burn the token on first successful fetch."""
    if not settings.mdm_base_url:
        raise HTTPException(503, "MDM_BASE_URL not configured")

    now = datetime.utcnow()
    with db.session() as s:
        row = s.exec(
            select(db.MdmEnrollToken).where(db.MdmEnrollToken.token == token)
        ).first()
        if not row:
            raise HTTPException(404, "unknown enrolment token")
        if row.used_at is not None:
            raise HTTPException(410, "enrolment token already used")
        if row.expires_at < now:
            raise HTTPException(410, "enrolment token expired")
        wg_ip = row.wg_ip
        # Mark used BEFORE we mint, so a flaky retry can't double-issue.
        row.used_at = now
        s.add(row)
        s.commit()

    cfg = store.load(force=True)
    if not cfg.device_by_ip(wg_ip):
        raise HTTPException(404, "device removed since token issued")

    profile = enrollment.build(wg_ip=wg_ip, mdm_base_url=settings.mdm_base_url)

    def stash_identity(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == wg_ip:
                    d.mdm = MdmState(
                        identity_cn=profile.identity_cn,
                        identity_cert_serial=profile.identity_serial_hex,
                        status="pending",
                    )
    store.mutate(stash_identity)

    return Response(
        content=profile.plist_xml,
        media_type="application/x-apple-aspen-config",
        headers={
            "Content-Disposition": (
                f'attachment; filename="gdlf-enroll-{wg_ip}.mobileconfig"'
            ),
        },
    )


# --- Public: Apple check-in (mTLS via Caddy) -------------------------------


@router.post("/mdm/checkin")
async def mdm_checkin(request: Request) -> Response:
    body = await request.body()
    subject = request.headers.get("X-Mdm-Client-Subject")
    try:
        resp = checkin.handle(body, subject)
    except checkin.CheckinError as e:
        raise HTTPException(e.status, str(e))
    return Response(content=resp, status_code=200)


# --- Public: Apple command channel -----------------------------------------


@router.post("/mdm/server")
async def mdm_server(request: Request) -> Response:
    """Apple's MDM command channel. Devices POST here after an APNs
    wake-up; we respond with the next pending command (or empty 200)."""
    body = await request.body()
    subject = request.headers.get("X-Mdm-Client-Subject")
    try:
        resp = server.handle(body, subject)
    except server.ServerError as e:
        raise HTTPException(e.status, str(e))
    # Apple expects application/x-apple-aspen-mdm for command bodies, but
    # an empty body is fine to return as text/plain — the device just sees
    # no Command key and goes back to sleep.
    media = "application/x-apple-aspen-mdm" if resp else "text/plain"
    return Response(content=resp, media_type=media, status_code=200)


# --- Admin: trigger pushes + enqueue commands ------------------------------


class EnqueueBody(BaseModel):
    """One of these must be set. `request_type` for simple parameterless
    commands; `command_dict` for callers that already built the dict
    (e.g. InstallProfile from the Phase 4 orchestrator)."""
    request_type: str | None = None
    command_dict: dict | None = None


def _enrolled_or_404(ip: str):
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    kid, device = found
    if not device.mdm or device.mdm.status != "enrolled":
        raise HTTPException(409, "device not MDM-enrolled")
    if not device.mdm.push_token or not device.mdm.push_magic:
        raise HTTPException(409, "device missing APNs push credentials")
    return cfg, kid, device


@router.post("/api/devices/{ip}/mdm/push")
def mdm_push(ip: str) -> dict:
    """Wake the device via APNs without enqueueing anything. Useful for
    flushing already-queued commands or just verifying the push path works."""
    _, _, device = _enrolled_or_404(ip)
    try:
        apns.send_push(
            push_token_b64=device.mdm.push_token,
            push_magic=device.mdm.push_magic,
        )
    except apns.ApnsError as e:
        raise HTTPException(502, f"APNs: {e}")
    return {"ok": True}


@router.post("/api/devices/{ip}/mdm/command")
def mdm_enqueue_command(ip: str, body: EnqueueBody) -> dict:
    """Enqueue a command + fire an APNs wake-up. Returns the CommandUUID
    so the dashboard can correlate the eventual response."""
    _, _, device = _enrolled_or_404(ip)

    # Pick a builder for known RequestTypes; fall back to caller-provided dict.
    builders = {
        "DeviceInformation": commands.device_information,
        "InstalledApplicationList": commands.installed_application_list,
        "DeviceLock": commands.device_lock,
    }
    if body.command_dict:
        command = body.command_dict
    elif body.request_type and body.request_type in builders:
        command = builders[body.request_type]()
    elif body.request_type:
        # Unknown RequestType but caller didn't supply a full dict —
        # most likely user error. Reject explicitly.
        raise HTTPException(400, f"unknown request_type {body.request_type!r}")
    else:
        raise HTTPException(400, "either request_type or command_dict required")

    if "RequestType" not in command:
        raise HTTPException(400, "command dict missing RequestType")

    command_uuid = commands.enqueue(
        identity_cn=device.mdm.identity_cn,
        command=command,
    )

    # Fire push so the device pulls it now. Don't fail the whole request if
    # the push errors — the command is queued and will be picked up on the
    # next poll (devices poll periodically even without push wakeups).
    push_error = None
    try:
        apns.send_push(
            push_token_b64=device.mdm.push_token,
            push_magic=device.mdm.push_magic,
        )
    except apns.ApnsError as e:
        push_error = str(e)

    return {
        "command_uuid": command_uuid,
        "request_type": command["RequestType"],
        "push_error": push_error,
    }


@router.post("/api/devices/{ip}/mdm/install-policy")
def mdm_install_policy(ip: str) -> dict:
    """Rebuild the baseline policy profile (VPN + CA + restrictions) from
    current state and push it to the device. Used to manually re-sync
    after a WG key rotation, mitmproxy CA refresh, or a policy tweak."""
    _, kid, device = _enrolled_or_404(ip)
    try:
        return orchestrator.deploy_baseline(kid, device)
    except FileNotFoundError as e:
        raise HTTPException(500, f"required cert/key missing: {e}")


@router.get("/api/devices/{ip}/mdm/commands")
def mdm_list_commands(ip: str) -> dict:
    """Dashboard view of the queue + recent responses for a device."""
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    _, device = found
    if not device.mdm:
        return {"queue": [], "responses": []}
    return {
        "queue": commands.queue_for_device(device.mdm.identity_cn),
        "responses": commands.responses_for_device(device.mdm.identity_cn),
    }
