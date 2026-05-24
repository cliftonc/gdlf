"""AdGuard blocked-services catalog passthrough.

The catalog (TikTok, Discord, ChatGPT, …) is global and curated by AdGuard
upstream; we proxy it so the SPA can render the same toggle grid AdGuard's
own UI shows. Per-kid blocked state lives in `kid.blocked_apps` and is
edited via `PUT /api/kids/{name}/blocked-apps`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from . import adguard

log = logging.getLogger("gdlf.api_services")

router = APIRouter(tags=["services"])


@router.get("/api/services")
async def list_services() -> dict:
    try:
        data = await adguard.fetch_blocked_services_catalog()
    except Exception as e:
        log.warning("adguard services fetch failed: %s", e)
        raise HTTPException(502, f"adguard unavailable: {e}")
    return {
        "groups": data.get("groups") or [],
        "services": data.get("blocked_services") or [],
    }
