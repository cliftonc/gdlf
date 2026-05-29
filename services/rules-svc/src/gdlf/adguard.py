"""AdGuard Home sync.

Each kid maps to one AdGuard "client" entry per device (keyed by WG IP).
We push per-client `blocked_services` (from `kid.blocked_apps`) so AdGuard
filters DNS for that kid specifically. The sync is one-way
(kids.yaml -> AdGuard), idempotent, event-driven on `store.mutation_event`
with a 5-minute backstop that also refreshes the services-catalog index.

A reachability watchdog (`health_watchdog_loop`) probes `/control/status`
over the gdlf bridge every 30s — same path rules-svc itself uses — and
asks docker to restart `gdlf-adguard` after 3 consecutive failures.
Catches the boot race where AdGuard's loopback works but its bridge-IP
listener is wedged. Disabled cleanly if the docker socket isn't mounted.

We *also* keep a local index of which hostnames each AdGuard service covers
(parsed from the catalog's `||domain^` rules) so the mitmproxy decision path
can enforce blocks when a device has cached the IP and DNS is bypassed.

AdGuard REST API: https://github.com/AdguardTeam/AdGuardHome/wiki/API
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import httpx

from . import store
from .settings import settings

log = logging.getLogger("gdlf.adguard")

# service_id -> set of base domains (lowercase, no scheme/port). Refreshed
# by the sync loop; used by `host_blocked_service_for_kid()`.
_service_hosts: dict[str, set[str]] = {}

# Custom filtering rules we own + keep present in AdGuard's user_rules list.
# Reconciled (additive) on every sync — never clobbers rules the parent
# added manually. Marker comment lets us identify our rules on re-read.
_OWNED_RULE_MARKER = "! gdlf:owned"
GDLF_GLOBAL_RULES: tuple[tuple[str, str], ...] = (
    # Firefox auto-disables its TRR (DoH) when this canary resolves to
    # NXDOMAIN. See:
    # https://support.mozilla.org/en-US/kb/configuring-networks-disable-dns-over-https
    ("||use-application-dns.net^$dnstype=A,important",
     "Firefox DoH canary — keep DoH disabled on managed network"),
)

# AdGuard rule formats we care about for service catalog blocking. Anything
# more exotic (regex rules, modifier rules) is ignored — the catalog uses
# `||domain^` for the vast majority of entries and that's all we need.
_RULE_DOMAIN_RE = re.compile(r"^\|\|([a-z0-9.\-]+)\^?$")


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
    # No `tags`: AdGuard only accepts tags from its own fixed catalog
    # (user_admin / device_phone / …); custom strings fail with
    # `invalid tag`, and the client never lands — so blocked_services
    # never takes effect either.
    return {
        "name": f"gdlf:{kid_name}:{device_name}",
        "ids": [wg_ip],
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

        try:
            await _ensure_global_rules(client)
        except Exception as e:
            log.debug("global rule reconcile failed: %s", e)


async def _ensure_global_rules(client: httpx.AsyncClient) -> None:
    """Additively reconcile our owned rules into AdGuard's user_rules list.

    AdGuard's `/control/filtering/set_rules` replaces the whole list, so
    we GET it first, merge our entries (skipping any already present), and
    POST back. Parent-added rules survive untouched. Each owned rule is
    paired with a marker comment so future versions can identify its line.
    """
    r = await client.get(
        f"{settings.adguard_url}/control/filtering/status", auth=_auth()
    )
    r.raise_for_status()
    status = r.json() or {}
    current = [str(x) for x in (status.get("user_rules") or [])]
    present = set(current)
    added: list[str] = []
    for rule, note in GDLF_GLOBAL_RULES:
        if rule in present:
            continue
        added.append(f"{_OWNED_RULE_MARKER}: {note}")
        added.append(rule)
    if not added:
        return
    merged = current + added
    r = await client.post(
        f"{settings.adguard_url}/control/filtering/set_rules",
        json={"rules": merged},
        auth=_auth(),
    )
    if r.status_code >= 400:
        log.warning("adguard set_rules -> %s %s", r.status_code, r.text)


async def fetch_blocked_services_catalog() -> dict:
    """Pull the global services catalog from AdGuard.

    AdGuard ships a curated list of well-known services (TikTok, Discord,
    ChatGPT, …) grouped by category, each with id/name/SVG icon. We expose
    this verbatim so the dashboard can render the same toggle UI AdGuard
    itself does — kids' `blocked_apps` is just a per-kid subset of these ids.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            f"{settings.adguard_url}/control/blocked_services/all", auth=_auth()
        )
        r.raise_for_status()
        return r.json()


def _extract_domains(rules: list[str]) -> set[str]:
    """Parse AdGuard's `||domain^` style rules into a set of base domains."""
    out: set[str] = set()
    for raw in rules or []:
        m = _RULE_DOMAIN_RE.match((raw or "").strip().lower())
        if m:
            out.add(m.group(1))
    return out


async def refresh_service_hosts_index() -> None:
    """Refresh the local service_id -> {hostnames} map from AdGuard's catalog.

    Failures are non-fatal: the previous index stays in place so the
    decision path keeps working through brief AdGuard outages.
    """
    global _service_hosts
    try:
        data = await fetch_blocked_services_catalog()
    except Exception as e:
        log.debug("service hosts refresh failed: %s", e)
        return
    idx: dict[str, set[str]] = {}
    for svc in data.get("blocked_services") or []:
        sid = svc.get("id")
        if not sid:
            continue
        idx[sid] = _extract_domains(svc.get("rules") or [])
    if idx:
        _service_hosts = idx


def host_blocked_service_for_kid(kid, host: str) -> str | None:
    """Return the service_id that blocks `host` for this kid, or None.

    Matches if `host` equals or is a subdomain of any domain in any of the
    kid's enabled `blocked_apps` services. The index is rebuilt every sync
    cycle; if it's empty (cold start / AdGuard down), this returns None and
    callers fall back to URL-rule-only enforcement.
    """
    if not host or not _service_hosts:
        return None
    host = host.lower().rstrip(".")
    for sid in kid.blocked_apps:
        for domain in _service_hosts.get(sid, ()):
            if host == domain or host.endswith("." + domain):
                return sid
    return None


async def sync_loop(
    debounce: float = 0.5,
    backstop: float = 300.0,
) -> None:
    """Event-driven sync.

    Wakes on either:
      * `store.mutation_event()` being set (a write to kids.yaml) — after
        `debounce` seconds of quiet, so a burst of edits coalesces into one
        AdGuard call.
      * `backstop` seconds elapsing with no mutations — a slow safety net
        that also refreshes the services catalog index periodically.
    """
    # Initial pass so AdGuard reflects kids.yaml from boot, and the host
    # index is ready for the decision path.
    await _run_pass()
    ev = store.mutation_event()
    while True:
        try:
            await asyncio.wait_for(ev.wait(), timeout=backstop)
        except asyncio.TimeoutError:
            pass
        ev.clear()
        # Debounce: absorb additional mutations that land while we wait.
        # Repeatedly extend the quiet window until nothing arrives during it.
        while True:
            await asyncio.sleep(debounce)
            if not ev.is_set():
                break
            ev.clear()
        await _run_pass()


async def _run_pass() -> None:
    try:
        await sync_once()
    except Exception as e:
        log.warning("sync_once raised: %s", e)
    try:
        await refresh_service_hosts_index()
    except Exception as e:
        log.debug("service hosts refresh raised: %s", e)


# ---- Reachability watchdog ----
#
# Observed on first boot: AdGuard's process starts and binds (loopback
# tests pass), but external connections to its bridge IP get refused or
# reset — most likely a race between AdGuard's IPv6 dual-stack listener
# and wg's interface bring-up inside the shared netns. A plain
# `docker restart gdlf-adguard` clears it. The watchdog probes AdGuard
# the same way the rest of rules-svc does (via the bridge IP, not
# loopback) and pokes docker if it stays unreachable.

_DOCKER_SOCK = "/var/run/docker.sock"


def _restart_container(name: str) -> None:
    """POST /containers/<name>/restart over the unix socket."""
    transport = httpx.HTTPTransport(uds=_DOCKER_SOCK)
    with httpx.Client(transport=transport, base_url="http://docker", timeout=20.0) as c:
        r = c.post(f"/containers/{name}/restart")
        r.raise_for_status()


async def _probe() -> None:
    """Raise if AdGuard's API isn't answering on its bridge IP."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            f"{settings.adguard_url}/control/status", auth=_auth()
        )
        # 401 still proves the listener is responding; anything 5xx or a
        # transport error means AdGuard is wedged.
        if r.status_code >= 500:
            raise RuntimeError(f"adguard /control/status -> {r.status_code}")


async def health_watchdog_loop(
    warmup: float = 60.0,
    probe_interval: float = 30.0,
    fail_threshold: int = 3,
    restart_cooldown: float = 300.0,
) -> None:
    """Restart AdGuard if it's been silently unreachable from rules-svc.

    Probes `/control/status` over the gdlf bridge every `probe_interval`s.
    After `fail_threshold` consecutive failures, asks docker to restart the
    AdGuard container, then sits out `restart_cooldown`s before probing
    again so we don't thrash if AdGuard is genuinely down.
    """
    if not os.path.exists(_DOCKER_SOCK):
        log.info("docker socket missing — adguard watchdog disabled")
        return
    container = os.environ.get("ADGUARD_CONTAINER", "gdlf-adguard")
    await asyncio.sleep(warmup)
    consecutive_failures = 0
    while True:
        try:
            await _probe()
        except Exception as e:
            consecutive_failures += 1
            log.warning(
                "adguard probe failed (%d/%d): %s",
                consecutive_failures, fail_threshold, e,
            )
            if consecutive_failures >= fail_threshold:
                log.error(
                    "adguard unreachable for %d probes — restarting %s",
                    consecutive_failures, container,
                )
                try:
                    _restart_container(container)
                except Exception as re_err:
                    log.error("adguard restart failed: %s", re_err)
                consecutive_failures = 0
                await asyncio.sleep(restart_cooldown)
                continue
        else:
            consecutive_failures = 0
        await asyncio.sleep(probe_interval)
