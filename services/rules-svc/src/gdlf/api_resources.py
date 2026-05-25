"""Per-container CPU / memory stats for the dashboard widget.

Queries the docker daemon directly over the mounted /var/run/docker.sock
(same approach as `wg.py`) — no docker CLI in the image. We list all
`gdlf-*` containers and ask each for a stats snapshot. Docker's
single-shot stats endpoint returns zeroed `precpu_stats` on the first
read, so we take two snapshots ~500ms apart and compute CPU% from the
delta.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter

log = logging.getLogger("gdlf.api_resources")

router = APIRouter(prefix="/api/resources", tags=["resources"])

_DOCKER_SOCK = "/var/run/docker.sock"
_NAME_PREFIX = "gdlf-"


def _async_client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCK)
    return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=10.0)


async def _list_containers(c: httpx.AsyncClient) -> list[dict]:
    r = await c.get(
        "/containers/json",
        params={"all": "true", "filters": json.dumps({"name": [_NAME_PREFIX]})},
    )
    r.raise_for_status()
    return r.json()


async def _stats_snapshot(c: httpx.AsyncClient, container_id: str) -> dict | None:
    try:
        r = await c.get(f"/containers/{container_id}/stats", params={"stream": "false"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug("stats fetch failed for %s: %s", container_id, e)
        return None


def _cpu_percent(curr: dict, prev: dict) -> float | None:
    """Docker's documented CPU% formula. Returns None if deltas are unusable."""
    try:
        cpu_delta = (
            curr["cpu_stats"]["cpu_usage"]["total_usage"]
            - prev["cpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            curr["cpu_stats"].get("system_cpu_usage", 0)
            - prev["cpu_stats"].get("system_cpu_usage", 0)
        )
    except (KeyError, TypeError):
        return None
    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0
    online_cpus = curr["cpu_stats"].get("online_cpus") or len(
        curr["cpu_stats"]["cpu_usage"].get("percpu_usage") or []
    ) or 1
    return (cpu_delta / system_delta) * online_cpus * 100.0


def _mem(curr: dict) -> tuple[int, int]:
    """(used_bytes, limit_bytes). Subtracts cache the way docker stats does."""
    ms = curr.get("memory_stats") or {}
    usage = ms.get("usage") or 0
    stats = ms.get("stats") or {}
    # cgroup v2 exposes "inactive_file"; v1 exposes "cache". Docker
    # canonicalizes by subtracting whichever is present.
    cache = stats.get("inactive_file", stats.get("cache", 0)) or 0
    used = max(0, usage - cache)
    limit = ms.get("limit") or 0
    return used, limit


def _short_name(names: list[str]) -> str:
    # Docker prepends a leading "/" to every name.
    for n in names or []:
        bare = n.lstrip("/")
        if bare.startswith(_NAME_PREFIX):
            return bare
    if names:
        return names[0].lstrip("/")
    return ""


def _docker_available() -> bool:
    return os.path.exists(_DOCKER_SOCK)


@router.get("")
async def list_resources() -> dict:
    """Snapshot of CPU% / memory for every gdlf-* container.

    Shape:
      {
        "containers": [
          {"name": "gdlf-rules", "state": "running",
           "cpu_percent": 1.23, "mem_used_bytes": 12345678,
           "mem_limit_bytes": 8589934592},
          ...
        ]
      }

    Stopped containers report state without stats. Returns an empty list
    if the docker socket isn't mounted (dev outside docker).
    """
    if not _docker_available():
        return {"containers": []}
    async with _async_client() as c:
        try:
            containers = await _list_containers(c)
        except Exception as e:
            log.warning("docker list failed: %s", e)
            return {"containers": []}

        running = [x for x in containers if x.get("State") == "running"]
        first = await asyncio.gather(*(_stats_snapshot(c, x["Id"]) for x in running))
        await asyncio.sleep(0.5)
        second = await asyncio.gather(*(_stats_snapshot(c, x["Id"]) for x in running))

    by_id: dict[str, dict[str, Any]] = {}
    for x, prev, curr in zip(running, first, second):
        entry: dict[str, Any] = {
            "name": _short_name(x.get("Names") or []),
            "state": "running",
            "cpu_percent": None,
            "mem_used_bytes": 0,
            "mem_limit_bytes": 0,
        }
        if curr:
            entry["cpu_percent"] = (
                _cpu_percent(curr, prev) if prev else None
            )
            used, limit = _mem(curr)
            entry["mem_used_bytes"] = used
            entry["mem_limit_bytes"] = limit
        by_id[x["Id"]] = entry

    for x in containers:
        if x["Id"] in by_id:
            continue
        by_id[x["Id"]] = {
            "name": _short_name(x.get("Names") or []),
            "state": x.get("State") or "unknown",
            "cpu_percent": None,
            "mem_used_bytes": 0,
            "mem_limit_bytes": 0,
        }

    out = [e for e in by_id.values() if e["name"]]
    out.sort(key=lambda e: e["name"])
    return {"containers": out}
