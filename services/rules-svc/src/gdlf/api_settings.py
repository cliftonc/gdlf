"""Settings + retention endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from . import browsers, db, dto, store
from .schema import BrowserPolicy
from .settings import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _ca_present() -> bool:
    return Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem").exists()


@router.get("")
def get_settings() -> dict:
    s = db.stats()
    cfg = store.load()
    return {
        "ca_present": _ca_present(),
        "ca_url": "/ca.pem",
        "ca_qr_url": "/ca/qr",
        "retention_days": settings.retention_days,
        "max_events": settings.max_events,
        "tz": settings.tz,
        "wg_host": settings.wg_host,
        "wg_port": settings.wg_port,
        "adguard_ui_port": settings.adguard_ui_port,
        "adguard_admin_user": "admin",
        "adguard_admin_password": settings.adguard_admin_password,
        "internal_url": settings.internal_url,
        "db_stats": {
            "events": s["events"],
            "oldest": s["oldest"].isoformat() if s["oldest"] else None,
            "newest": s["newest"].isoformat() if s["newest"] else None,
            "db_path": s["db_path"],
            "db_bytes": s["db_bytes"],
        },
        "browser_policy": dto.browser_policy_dto(cfg.browser_policy),
        "available_browsers": browsers.catalog_for_api(),
    }


@router.put("/browser-policy")
def put_browser_policy(policy: BrowserPolicy) -> dict:
    """Replace the global browser policy. FastAPI validates the body
    against the pydantic model — unknown keys (`extra=forbid`) fail with
    422. `store.mutate` fires the mutation_event, which triggers the iOS
    orchestrator re-push and the AMAPI policy-watch debounce."""
    @store.mutate
    def _apply(cfg):
        cfg.browser_policy = policy
    return dto.browser_policy_dto(policy)


@router.post("/prune")
def prune_now() -> dict:
    res = db.prune(
        settings.retention_days,
        settings.max_events,
        stats_retention_days=settings.stats_retention_days,
    )
    db.vacuum()
    return {
        "age_deleted": res["age_deleted"],
        "cap_deleted": res["cap_deleted"],
        "stats_deleted": res.get("stats_deleted", 0),
    }
