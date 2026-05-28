"""HTTP surface for Android MDM (Android Management API).

  POST   /api/devices/{ip}/android-mdm/enroll-token   (admin)
         Sync policy + mint an AMAPI enrollment token + return the QR JSON.

  POST   /api/devices/{ip}/android-mdm/sync-policy    (admin)
         Re-push the policy for this device. Idempotent.

  POST   /api/devices/{ip}/android-mdm/sync-status    (admin)
         Pull latest device state from AMAPI on demand (otherwise polled
         every ~60s by the background loop in main.lifespan).

  DELETE /api/devices/{ip}/android-mdm                (admin)
         Unenroll: delete the AMAPI Device + clear android_mdm.

  GET    /devices/{ip}/android-mdm/qr.png             (public)
         Server-rendered QR PNG of the most recent enrollment token. Public
         so the dashboard can embed an <img>; expires when the token does.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime

import qrcode
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from . import api_shortlinks, store
from .amapi import client as amapi_client, enrollment, orchestrator, policy
from .schema import AndroidMdmState

log = logging.getLogger("gdlf.android_mdm")

router = APIRouter(tags=["android-mdm"])


# In-memory map of `wg_ip -> qr_code_string`. The QR JSON Google hands back
# is too long to round-trip via headers / query strings, but the dashboard
# wants to display it as a scannable PNG. Stored only until the token
# expires (1h); a process restart simply forces a fresh "Generate" click.
_qr_cache: dict[str, dict] = {}


def _require_amapi() -> None:
    if not amapi_client.is_configured():
        raise HTTPException(503, "AMAPI not configured — run ./gdlf amapi init")


def _device_or_404(ip: str):
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    return cfg, found[0], found[1]


# --- Admin: mint an enrollment token ---------------------------------------


class EnrollTokenResponse(BaseModel):
    token_name: str        # enterprises/{N}/enrollmentTokens/{id}
    qr_url: str            # /devices/{ip}/android-mdm/qr.png
    expires_at: datetime | None = None


def _create_enroll_token(ip: str, code: str | None = None) -> EnrollTokenResponse:
    _require_amapi()
    _, kid, device = _device_or_404(ip)

    # 1. Build + push the per-device Policy. AMAPI requires the policy to
    #    exist before an enrollment token can reference it.
    try:
        policy_name = orchestrator.sync_policy(kid, device)
    except FileNotFoundError as e:
        raise HTTPException(500, f"required cert/key missing: {e}")
    except Exception as e:
        log.exception("policy sync failed")
        raise HTTPException(502, f"AMAPI policy sync failed: {e}")

    # 2. Mint an enrollment token bound to that policy. Stuff the wg_ip into
    #    `additionalData` so the status poller can match the enrolled
    #    device back to a kids.yaml row.
    additional = json.dumps({"wg_ip": ip, "kid": kid.name, "device": device.name})
    try:
        tok = enrollment.mint(policy_name=policy_name, additional_data=additional)
    except Exception as e:
        log.exception("enrollment token mint failed")
        raise HTTPException(502, f"AMAPI enrollment token mint failed: {e}")

    qr_code = tok.get("qrCode")
    if not qr_code:
        raise HTTPException(502, "AMAPI returned no qrCode payload")

    expires_at = _parse_expires(tok.get("expirationTimestamp"))
    _qr_cache[ip] = {"qr_code": qr_code, "expires_at": expires_at}

    # 3. Stash the policy + token names on the device so the dashboard knows
    #    enrollment is in-flight.
    def mark_pending(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    prev = d.android_mdm or orchestrator.initial_state_for(k, d)
                    d.android_mdm = prev.model_copy(update={
                        "policy_name": policy_name,
                        "enrollment_token_name": tok.get("name"),
                        "status": "pending" if prev.status != "active" else prev.status,
                    })
    store.mutate(mark_pending)

    return EnrollTokenResponse(
        token_name=tok.get("name", ""),
        qr_url=f"/api/dl/{code}/android-mdm/qr.png" if code else f"/devices/{ip}/android-mdm/qr.png",
        expires_at=expires_at,
    )


@router.post("/api/devices/{ip}/android-mdm/enroll-token")
def create_enroll_token(ip: str) -> EnrollTokenResponse:
    return _create_enroll_token(ip)


@router.post("/api/dl/{code}/android-mdm/enroll-token")
def create_enroll_token_by_code(code: str) -> EnrollTokenResponse:
    _, device = api_shortlinks.device_for_code(code)
    return _create_enroll_token(device.wg_ip, code=code)


def _parse_expires(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


# --- Public: QR PNG --------------------------------------------------------


@router.get("/devices/{ip}/android-mdm/qr.png")
def enroll_qr_png(ip: str) -> Response:
    entry = _qr_cache.get(ip)
    if not entry:
        raise HTTPException(404, "no enrollment QR — generate one from the dashboard")
    img = qrcode.make(entry["qr_code"])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/api/dl/{code}/android-mdm/qr.png")
def enroll_qr_png_by_code(code: str) -> Response:
    _, device = api_shortlinks.device_for_code(code)
    return enroll_qr_png(device.wg_ip)


# --- Admin: re-push policy / refresh status --------------------------------


@router.post("/api/devices/{ip}/android-mdm/sync-policy")
def sync_policy(ip: str) -> dict:
    _require_amapi()
    _, kid, device = _device_or_404(ip)
    try:
        name = orchestrator.sync_policy(kid, device)
    except FileNotFoundError as e:
        raise HTTPException(500, f"required cert/key missing: {e}")
    except Exception as e:
        log.exception("policy sync failed")
        raise HTTPException(502, f"AMAPI: {e}")
    return {"ok": True, "policy_name": name}


@router.post("/api/devices/{ip}/android-mdm/sync-status")
def sync_status(ip: str) -> dict:
    _require_amapi()
    # `sync_device_status` updates every enrolled device in one pass; the
    # /sync-status endpoint is per-device only as a UI affordance.
    try:
        updated = orchestrator.sync_device_status()
    except Exception as e:
        log.exception("status sync failed")
        raise HTTPException(502, f"AMAPI: {e}")
    return {"ok": True, "updated": updated}


# --- Admin: unenroll --------------------------------------------------------


@router.delete("/api/devices/{ip}/android-mdm")
def unenroll(ip: str) -> dict:
    _require_amapi()
    cfg, _, device = _device_or_404(ip)
    state = device.android_mdm
    if not state:
        raise HTTPException(409, "device not Android-MDM-enrolled")

    svc = amapi_client.service()
    errors = []

    # Delete the device (only meaningful once enrolled).
    if state.device_name:
        try:
            svc.enterprises().devices().delete(name=state.device_name).execute()
        except Exception as e:
            log.warning("amapi devices.delete failed: %s", e)
            errors.append(f"device: {e}")

    # Revoke an outstanding enrollment token if it's still alive.
    if state.enrollment_token_name:
        try:
            enrollment.revoke(state.enrollment_token_name)
        except Exception as e:
            # Not fatal — token may already be redeemed/expired.
            log.debug("amapi enrollmentTokens.delete failed: %s", e)

    # Drop the policy too. Leaving it dangling is harmless but tidy is nicer.
    try:
        svc.enterprises().policies().delete(name=state.policy_name).execute()
    except Exception as e:
        log.debug("amapi policies.delete failed: %s", e)

    def clear(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    d.android_mdm = None
    store.mutate(clear)

    _qr_cache.pop(ip, None)
    return {"ok": True, "errors": errors}
