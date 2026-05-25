"""Shared DTO shaping for the JSON API.

These are plain dicts (not pydantic) — kids.yaml models already validate on
load, and the API just projects them. Centralising the shape here keeps the
SPA's zod schemas in sync with a single Python source.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import db, wg
from .schema import Device, Kid


def device_dto(d: Device, handshake: dict | None = None) -> dict[str, Any]:
    last = (handshake or {}).get("last_handshake", 0) or 0
    return {
        "name": d.name,
        "platform": d.platform,
        "wg_ip": d.wg_ip,
        "wg_public_key": d.wg_public_key,
        "mitm_ca_installed": d.mitm_ca_installed,
        "manual_block": d.manual_block,
        "last_handshake": last,
        "rx": (handshake or {}).get("rx", 0),
        "tx": (handshake or {}).get("tx", 0),
        "online": _is_online(last),
        "mdm": mdm_state_dto(d.mdm) if d.mdm else None,
        "android_mdm": android_mdm_state_dto(d.android_mdm) if d.android_mdm else None,
        "windows_mdm": windows_mdm_state_dto(d.windows_mdm) if d.windows_mdm else None,
    }


def mdm_state_dto(s) -> dict[str, Any]:
    """Small projection of MdmState — only what the dashboard renders."""
    return {
        "status": s.status,
        "udid": s.udid,
        "supervised": s.supervised,
        "enrolled_at": s.enrolled_at.isoformat() if s.enrolled_at else None,
        "last_checkin_at": s.last_checkin_at.isoformat() if s.last_checkin_at else None,
    }


def android_mdm_state_dto(s) -> dict[str, Any]:
    """Projection of AndroidMdmState for the dashboard."""
    return {
        "status": s.status,
        "model": s.model,
        "enrolled_at": s.enrolled_at.isoformat() if s.enrolled_at else None,
        "last_status_at": s.last_status_at.isoformat() if s.last_status_at else None,
        "applied_policy_version": s.applied_policy_version,
        "device_name": s.device_name,
    }


def windows_mdm_state_dto(s) -> dict[str, Any]:
    """Projection of WindowsMdmState for the dashboard."""
    return {
        "status": s.status,
        "package_id": s.package_id,
        "package_version": s.package_version,
        "enrolled_at": s.enrolled_at.isoformat() if s.enrolled_at else None,
        "last_built_at": s.last_built_at.isoformat() if s.last_built_at else None,
    }


def _is_online(last_handshake: int) -> bool:
    if not last_handshake:
        return False
    # WireGuard refreshes every 25s when keepalive is set; 3 minutes is a safe
    # online window before we mark stale.
    return (datetime.utcnow().timestamp() - last_handshake) < 180


def rule_dto(r) -> dict[str, Any]:
    return {
        "action": r.action,
        "match": r.match,
        "query": r.query,
        "flag": r.flag,
        "note": r.note,
    }


def kid_summary_dto(k: Kid, handshakes: dict[str, dict]) -> dict[str, Any]:
    devices = [device_dto(d, handshakes.get(d.wg_ip)) for d in k.devices]
    return {
        "name": k.name,
        "age": k.age,
        "manual_block": k.manual_block,
        "bonus_until": k.bonus_until.isoformat() if k.bonus_until else None,
        "schedule": {
            "weekday": k.schedule.weekday.allowed,
            "weekend": k.schedule.weekend.allowed,
        },
        "device_count": len(devices),
        "online_device_count": sum(1 for d in devices if d["online"]),
        "rule_count": len(k.url_rules),
        # Thin device list for the overview card's per-device block toggles.
        # Full device records (handshake/keys/MDM state) stay on the detail DTO.
        "devices": [
            {
                "name": d["name"],
                "platform": d["platform"],
                "wg_ip": d["wg_ip"],
                "online": d["online"],
                "manual_block": d["manual_block"],
            }
            for d in devices
        ],
    }


def kid_detail_dto(k: Kid, handshakes: dict[str, dict]) -> dict[str, Any]:
    return {
        "name": k.name,
        "age": k.age,
        "manual_block": k.manual_block,
        "bonus_until": k.bonus_until.isoformat() if k.bonus_until else None,
        "schedule": {
            "weekday": k.schedule.weekday.allowed,
            "weekend": k.schedule.weekend.allowed,
        },
        "blocked_apps": list(k.blocked_apps),
        "keyword_flags": list(k.keyword_flags),
        "mitm_passthrough_hosts": list(k.mitm_passthrough_hosts),
        "mitm_passthrough_disabled": list(k.mitm_passthrough_disabled),
        "devices": [device_dto(d, handshakes.get(d.wg_ip)) for d in k.devices],
        "rules": [rule_dto(r) for r in k.url_rules],
    }


def event_dto(e: db.Event) -> dict[str, Any]:
    return {
        "id": e.id,
        "ts": e.ts.isoformat() if e.ts else None,
        "source": e.source,
        "client_ip": e.client_ip,
        "kid": e.kid,
        "device": e.device,
        "method": e.method,
        "host": e.host,
        "path": e.path,
        "query": e.query,
        "status": e.status,
        "decision": e.decision,
        "rule": e.rule,
        "sni_only": e.sni_only,
        "kind": e.kind,
    }
