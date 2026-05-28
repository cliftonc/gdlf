"""Device enrolment shortlinks — `/dl/{code}`.

A shortcode is an 8-char base32 string bound to one device's `wg_ip`. It
authenticates the bearer for code-only enrollment endpoints used by the
shared page. The IP may be displayed to help the parent debug enrollment,
but it is never accepted as authorization on public routes.

  POST   /api/devices/{ip}/shortlink  (admin) — create or rotate.
  DELETE /api/devices/{ip}/shortlink  (admin) — revoke.
  GET    /api/devices/{ip}/shortlink  (admin) — current code, if any.
  GET    /api/dl/{code}/resolve       (public) — `{kid, ip}` so the SPA at
         /dl/{code} knows which enrolment to load.
"""
from __future__ import annotations

import io
import secrets
from pathlib import Path

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import select

from . import db, store, wg
from .dto import device_dto

router = APIRouter(tags=["shortlinks"])

# Crockford-ish base32 without confusable chars (0/O, 1/I/L). 8 chars =
# ~40 bits. Short enough to hand-type, but no longer trivially enumerable
# on a home LAN when paired with narrow code-only endpoints.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8


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
        code = shortlink_for_ip(device.wg_ip)
        if code is None or len(code) < _CODE_LEN:
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


def device_for_code(code: str):
    """Resolve a shortlink to (kid, device), pruning stale rows lazily."""
    ip = ip_for_code(code)
    if not ip:
        raise HTTPException(404, "unknown shortlink")
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        with db.session() as s:
            row = s.get(db.DeviceShortlink, code)
            if row:
                s.delete(row)
                s.commit()
        raise HTTPException(404, "device no longer exists")
    return found[0], found[1]


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
    it should render. Subsequent shared-page calls use code-only /api/dl/*
    endpoints; the device IP is never an authorizer."""
    kid, device = device_for_code(code)
    return ResolveResponse(kid=kid.name, ip=device.wg_ip, device_name=device.name)


def _ca_present() -> bool:
    return Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem").exists()


def _render_client_conf(code: str) -> tuple[str, str]:
    kid, device = device_for_code(code)
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"
    try:
        priv = wg.load_peer_priv(peer_id)
    except FileNotFoundError:
        raise HTTPException(500, "private key missing — re-enrol device")
    return peer_id, wg.build_client_conf(device.name, priv, device.wg_ip)


@router.get("/api/dl/{code}/enrolment")
def code_enrolment(code: str) -> dict:
    """Code-only enrollment payload for the shared /dl/<code> page."""
    _, device = device_for_code(code)
    handshakes = wg.wg_show_handshakes()
    return {
        "device": device_dto(device, handshakes.get(device.wg_ip)),
        "qr_url": f"/api/dl/{code}/qr",
        "conf_url": f"/api/dl/{code}/conf",
        "ca_url": "/ca.pem",
        "ca_qr_url": "/ca/qr",
        "ca_present": _ca_present(),
    }


@router.get("/api/dl/{code}/conf", response_class=PlainTextResponse)
def code_device_conf(code: str):
    peer_id, conf = _render_client_conf(code)
    return PlainTextResponse(
        conf,
        headers={"Content-Disposition": f'attachment; filename="{peer_id}.conf"'},
    )


@router.get("/api/dl/{code}/qr")
def code_device_qr(code: str):
    _, conf = _render_client_conf(code)
    img = qrcode.make(conf, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@router.get("/api/dl/{code}/handshake")
def code_handshake(code: str) -> dict:
    _, device = device_for_code(code)
    hs = wg.wg_show_handshakes().get(device.wg_ip, {})
    return {
        "last_handshake": hs.get("last_handshake", 0),
        "rx": hs.get("rx", 0),
        "tx": hs.get("tx", 0),
    }


class MitmInstalledBody(BaseModel):
    installed: bool = True


@router.put("/api/dl/{code}/mitm-installed")
def code_mark_mitm(code: str, body: MitmInstalledBody) -> dict:
    _, device = device_for_code(code)
    ip = device.wg_ip

    def mark(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    d.mitm_ca_installed = body.installed

    store.mutate(mark)
    return {"mitm_ca_installed": body.installed}
