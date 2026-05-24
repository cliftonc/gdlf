"""AdGuard Home sync.

Each kid maps to one AdGuard "client" entry per device (keyed by WG IP).
Per-client blocklists are AdGuard's "Filtering > Custom rules" feature, but
those are global — so for per-kid filtering we lean on AdGuard's
`tags`/`blocked_services` per-client config plus the global blocklists
listed in kids.yaml.blocklists.

For v1 we keep this minimal: ensure one client per device with the kid's
name as the tag, and let AdGuard's UI/admin handle blocklist sources. The
sync is one-way (kids.yaml -> AdGuard), idempotent, runs every minute and
on kids.yaml change.

AdGuard REST API: https://github.com/AdguardTeam/AdGuardHome/wiki/API
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from . import store
from .settings import settings

log = logging.getLogger("gdlf.adguard")


def _auth() -> tuple[str, str] | None:
    if not settings.adguard_admin_password:
        return None
    return ("admin", settings.adguard_admin_password)


async def _list_clients(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(f"{settings.adguard_url}/control/clients", auth=_auth())
    r.raise_for_status()
    return r.json().get("clients") or []


async def _add_client(client: httpx.AsyncClient, body: dict) -> None:
    r = await client.post(
        f"{settings.adguard_url}/control/clients/add", json=body, auth=_auth()
    )
    if r.status_code >= 400:
        log.warning("adguard add_client %s -> %s %s", body.get("name"), r.status_code, r.text)


async def _update_client(client: httpx.AsyncClient, name: str, body: dict) -> None:
    r = await client.post(
        f"{settings.adguard_url}/control/clients/update",
        json={"name": name, "data": body},
        auth=_auth(),
    )
    if r.status_code >= 400:
        log.warning("adguard update_client %s -> %s %s", name, r.status_code, r.text)


async def _delete_client(client: httpx.AsyncClient, name: str) -> None:
    r = await client.post(
        f"{settings.adguard_url}/control/clients/delete",
        json={"name": name}, auth=_auth(),
    )
    if r.status_code >= 400:
        log.warning("adguard delete_client %s -> %s %s", name, r.status_code, r.text)


def _client_body(kid_name: str, device_name: str, wg_ip: str, blocked_services: list[str]) -> dict:
    return {
        "name": f"gdlf:{kid_name}:{device_name}",
        "ids": [wg_ip],
        "tags": [f"gdlf-kid-{kid_name}"],
        "use_global_settings": False,
        "filtering_enabled": True,
        "parental_enabled": True,
        "safebrowsing_enabled": True,
        "safe_search": {"enabled": True},
        "use_global_blocked_services": False,
        "blocked_services": blocked_services,
    }


async def sync_once() -> None:
    cfg = store.load()
    desired: dict[str, dict] = {}
    # Map kid blocklist names -> AdGuard's `blocked_services` ids. For now
    # we pass through as-is; the parent can curate the kids.yaml side.
    for kid in cfg.kids:
        blocked = list(kid.blocked_apps)  # tiktok, discord, ...
        for device in kid.devices:
            body = _client_body(kid.name, device.name, device.wg_ip, blocked)
            desired[body["name"]] = body

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            existing = await _list_clients(client)
        except Exception as e:
            log.warning("adguard list failed (is it set up?): %s", e)
            return
        existing_names = {c["name"] for c in existing if c["name"].startswith("gdlf:")}

        for name, body in desired.items():
            if name in existing_names:
                await _update_client(client, name, body)
            else:
                await _add_client(client, body)

        # Drop clients we manage but no longer want.
        for name in existing_names - set(desired):
            await _delete_client(client, name)


async def sync_loop(interval: int = 60) -> None:
    while True:
        try:
            await sync_once()
        except Exception as e:
            log.warning("sync_once raised: %s", e)
        await asyncio.sleep(interval)
