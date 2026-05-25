"""Device CRUD + enrolment endpoints for the SPA."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import api_shortlinks, db, store, wg
from .dto import device_dto
from .schema import Device, Platform
from .settings import settings

router = APIRouter(tags=["devices"])


class CreateDeviceBody(BaseModel):
    device_name: str
    platform: Platform


class BlockBody(BaseModel):
    blocked: bool


class MitmInstalledBody(BaseModel):
    installed: bool = True


def _device_or_404(ip: str):
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    return cfg, found[0], found[1]


def _ca_present() -> bool:
    return Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem").exists()


@router.post("/api/kids/{name}/devices", status_code=201)
def create_device(name: str, body: CreateDeviceBody) -> dict:
    cfg = store.load(force=True)
    kid = cfg.kid(name)
    if not kid:
        raise HTTPException(404, f"unknown kid {name}")
    device_name = body.device_name.strip()
    if not device_name:
        raise HTTPException(400, "device_name required")
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device_name)}"

    ip = wg.allocate_ip(cfg)
    priv, pub = wg.generate_keypair()
    wg.save_peer_keys(peer_id, priv, pub)

    def add(cfg):
        k = cfg.kid(kid.name)
        if any(d.wg_ip == ip for _, d in cfg.all_devices()):
            raise HTTPException(500, f"IP collision on {ip}")
        k.devices.append(
            Device(name=device_name, platform=body.platform, wg_ip=ip, wg_public_key=pub)
        )

    store.mutate(add)
    conf = wg.build_client_conf(peer_id, priv, ip)
    (settings.state_dir / "wg-keys" / f"{peer_id}.conf").write_text(conf)

    wg.write_wg0_conf(store.load(force=True))
    wg.reload_wg()

    # Auto-provision an enrolment shortlink so the parent can hand-off the
    # /dl/<code> URL straight from the "device created" toast — no separate
    # rotate step. Don't fail device creation if shortlink minting trips.
    try:
        api_shortlinks.mint_shortlink(ip)
    except Exception:
        pass

    cfg = store.load(force=True)
    _, device = cfg.device_by_ip(ip)
    return {
        "device": device_dto(device),
        "peer_id": peer_id,
        "wg_ip": ip,
        "qr_url": f"/devices/{ip}/qr",
        "conf_url": f"/devices/{ip}/conf",
    }


@router.get("/api/kids/{name}/devices/{ip}/enrolment")
def device_enrolment(name: str, ip: str) -> dict:
    cfg = store.load(force=True)
    kid = cfg.kid(name)
    if not kid:
        raise HTTPException(404, "unknown kid")
    device = next((d for d in kid.devices if d.wg_ip == ip), None)
    if not device:
        raise HTTPException(404, "unknown device")
    handshakes = wg.wg_show_handshakes()
    return {
        "device": device_dto(device, handshakes.get(ip)),
        "qr_url": f"/devices/{ip}/qr",
        "conf_url": f"/devices/{ip}/conf",
        "ca_url": "/ca.pem",
        "ca_qr_url": "/ca/qr",
        "ca_present": _ca_present(),
    }


@router.get("/api/devices/{ip}/handshake")
def device_handshake(ip: str) -> dict:
    hs = wg.wg_show_handshakes().get(ip, {})
    return {
        "last_handshake": hs.get("last_handshake", 0),
        "rx": hs.get("rx", 0),
        "tx": hs.get("tx", 0),
    }


@router.put("/api/devices/{ip}/block")
def device_block(ip: str, body: BlockBody) -> dict:
    _, _, _ = _device_or_404(ip)

    def upd(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    d.manual_block = body.blocked

    store.mutate(upd)
    return {"manual_block": body.blocked}


@router.delete("/api/devices/{ip}", status_code=204)
def device_delete(ip: str):
    _, kid, device = _device_or_404(ip)
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"
    kid_name = kid.name

    def remove(cfg):
        k = cfg.kid(kid_name)
        k.devices = [d for d in k.devices if d.wg_ip != ip]

    store.mutate(remove)
    for ext in ("priv", "pub", "conf"):
        p = settings.state_dir / "wg-keys" / f"{peer_id}.{ext}"
        if p.exists():
            p.unlink()
    # Drop the device's shortlink row so the code can't outlive the device.
    try:
        from sqlmodel import select as _select
        with db.session() as s:
            row = s.exec(
                _select(db.DeviceShortlink).where(db.DeviceShortlink.wg_ip == ip)
            ).first()
            if row:
                s.delete(row)
                s.commit()
    except Exception:
        pass
    wg.write_wg0_conf(store.load(force=True))
    wg.reload_wg()
    return None


@router.post("/api/devices/{ip}/regenerate")
def device_regenerate(ip: str) -> dict:
    _, kid, device = _device_or_404(ip)
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"

    priv, pub = wg.generate_keypair()
    wg.save_peer_keys(peer_id, priv, pub)

    def rotate(cfg):
        for d in cfg.kid(kid.name).devices:
            if d.wg_ip == ip:
                d.wg_public_key = pub
                d.mitm_ca_installed = False

    store.mutate(rotate)
    conf = wg.build_client_conf(peer_id, priv, ip)
    (settings.state_dir / "wg-keys" / f"{peer_id}.conf").write_text(conf)
    wg.write_wg0_conf(store.load(force=True))
    wg.reload_wg()

    return {
        "wg_ip": ip,
        "kid": kid.name,
        "peer_id": peer_id,
        "qr_url": f"/devices/{ip}/qr",
        "conf_url": f"/devices/{ip}/conf",
    }


@router.put("/api/devices/{ip}/mitm-installed")
def device_mark_mitm(ip: str, body: MitmInstalledBody) -> dict:
    _device_or_404(ip)

    def mark(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    d.mitm_ca_installed = body.installed

    store.mutate(mark)
    return {"mitm_ca_installed": body.installed}
