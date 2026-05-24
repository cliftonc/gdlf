"""Settings + retention endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from . import db
from .settings import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _ca_present() -> bool:
    return Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem").exists()


@router.get("")
def get_settings() -> dict:
    s = db.stats()
    return {
        "ca_present": _ca_present(),
        "ca_url": "/ca.pem",
        "ca_qr_url": "/ca/qr",
        "retention_days": settings.retention_days,
        "max_events": settings.max_events,
        "tz": settings.tz,
        "wg_host": settings.wg_host,
        "wg_port": settings.wg_port,
        "db_stats": {
            "events": s["events"],
            "oldest": s["oldest"].isoformat() if s["oldest"] else None,
            "newest": s["newest"].isoformat() if s["newest"] else None,
            "db_path": s["db_path"],
            "db_bytes": s["db_bytes"],
        },
    }


@router.post("/prune")
def prune_now() -> dict:
    res = db.prune(settings.retention_days, settings.max_events)
    db.vacuum()
    return {
        "age_deleted": res["age_deleted"],
        "cap_deleted": res["cap_deleted"],
    }
