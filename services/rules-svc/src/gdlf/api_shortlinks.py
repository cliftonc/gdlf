"""Device enrolment shortlinks — `/dl/{code}`.

A shortcode is a 4-char base32 string bound to one device's `wg_ip`. It
authenticates the bearer for *that device only*, on the device-scoped
endpoints used by the enrolment page (handshake, mark-CA, MDM enroll-token
mints). The parent generates one from the dashboard, then opens the short
URL on the kid's device — no login required there.

  POST   /api/devices/{ip}/shortlink  (admin) — create or rotate.
  DELETE /api/devices/{ip}/shortlink  (admin) — revoke.
  GET    /api/devices/{ip}/shortlink  (admin) — current code, if any.
  GET    /api/dl/{code}/resolve       (public) — `{kid, ip}` so the SPA at
         /dl/{code} knows which enrolment to load.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from . import db, store

router = APIRouter(tags=["shortlinks"])

# Crockford-ish base32 without confusable chars (0/O, 1/I/L). 4 chars =
# ~1M codes; we collide-retry on insert so this is plenty.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 4


def _new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def shortlink_for_ip(ip: str) -> str | None:
    """Public helper for other modules / DTOs."""
    with db.session() as s:
        row = s.exec(
            select(db.DeviceShortlink).where(db.DeviceShortlink.wg_ip == ip)
        ).first()
        return row.code if row else None


def ensure_shortlinks_for_all_devices() -> int:
    """Mint a code for every device that doesn't have one yet.

    Run at startup so the invariant "every device has a /dl/<code>"
    holds even for devices created before the feature shipped. Returns
    the count of new shortlinks created."""
    cfg = store.load()
    minted = 0
    for _kid, device in cfg.all_devices():
        if shortlink_for_ip(device.wg_ip) is None:
            try:
                mint_shortlink(device.wg_ip)
                minted += 1
            except Exception:
                continue
    return minted


def mint_shortlink(ip: str) -> str:
    """Allocate and persist a fresh code for `ip`. Replaces any existing
    binding (so every call returns a brand-new code). Used both by the
    admin rotate endpoint and by `api_devices.create_device` so new
    devices get a shareable enrolment link straight away."""
    with db.session() as s:
        existing = s.exec(
            select(db.DeviceShortlink).where(db.DeviceShortlink.wg_ip == ip)
        ).first()
        if existing:
            s.delete(existing)
            s.commit()
        for _ in range(8):
            code = _new_code()
            if s.get(db.DeviceShortlink, code) is None:
                s.add(db.DeviceShortlink(code=code, wg_ip=ip))
                s.commit()
                return code
        raise RuntimeError("could not allocate a unique shortlink code")


def ip_for_code(code: str) -> str | None:
    with db.session() as s:
        row = s.get(db.DeviceShortlink, code)
        return row.wg_ip if row else None


def _device_or_404(ip: str):
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    return cfg, found[0], found[1]


class ShortlinkResponse(BaseModel):
    code: str
    url: str          # relative — /dl/<code>


def _dto(code: str) -> ShortlinkResponse:
    return ShortlinkResponse(code=code, url=f"/dl/{code}")


@router.post("/api/devices/{ip}/shortlink")
def create_or_rotate_shortlink(ip: str) -> ShortlinkResponse:
    """Mint a new code, replacing any existing one for this device."""
    _device_or_404(ip)
    try:
        return _dto(mint_shortlink(ip))
    except RuntimeError as e:
        raise HTTPException(500, str(e))


@router.get("/api/devices/{ip}/shortlink")
def get_shortlink(ip: str) -> ShortlinkResponse:
    _device_or_404(ip)
    code = shortlink_for_ip(ip)
    if not code:
        raise HTTPException(404, "no shortlink for this device")
    return _dto(code)


@router.delete("/api/devices/{ip}/shortlink", status_code=204)
def delete_shortlink(ip: str) -> None:
    _device_or_404(ip)
    with db.session() as s:
        row = s.exec(
            select(db.DeviceShortlink).where(db.DeviceShortlink.wg_ip == ip)
        ).first()
        if row:
            s.delete(row)
            s.commit()
    return None


class ResolveResponse(BaseModel):
    kid: str
    ip: str
    device_name: str


@router.get("/api/dl/{code}/resolve")
def resolve(code: str) -> ResolveResponse:
    """Public: the SPA at /dl/{code} calls this to discover the device + kid
    it should render the enrolment page for. The code itself remains the
    bearer auth for the subsequent `?dl=<code>` API calls."""
    ip = ip_for_code(code)
    if not ip:
        raise HTTPException(404, "unknown shortlink")
    cfg = store.load()
    found = cfg.device_by_ip(ip)
    if not found:
        # Device deleted underneath; orphan the shortlink lazily.
        with db.session() as s:
            row = s.get(db.DeviceShortlink, code)
            if row:
                s.delete(row)
                s.commit()
        raise HTTPException(404, "device no longer exists")
    kid, device = found
    return ResolveResponse(kid=kid.name, ip=ip, device_name=device.name)
