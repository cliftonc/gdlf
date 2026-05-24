"""TLS-failure tracking API.

The mitmproxy addon posts here every time a client refuses our certificate.
We upsert into the `tls_failures` table (keyed by kid + host) and surface
the data to the dashboard as groups of registrable-domain children, so the
parent can enable passthrough for `*.reddit.com` with a single switch
instead of allowing each subdomain individually.

This stays out of the activity log on purpose — pinned-cert apps retry
constantly and would otherwise dominate the feed.
"""
from __future__ import annotations

import tldextract
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db, store

# Use the in-memory snapshot so we don't fetch the public-suffix list on
# every container start. `suffix_list_urls=()` disables network entirely;
# the package ships with a bundled snapshot that's good enough for our
# purposes (the PSL changes slowly and we only need it for grouping).
_extract = tldextract.TLDExtract(suffix_list_urls=())


def registrable_domain(host: str) -> str:
    """Return eTLD+1 for grouping (`gql-fed.reddit.com` → `reddit.com`,
    `news.bbc.co.uk` → `bbc.co.uk`). Falls back to the host itself if
    extraction can't find a registered domain (bare IPs, localhost, etc.)."""
    parsed = _extract(host or "")
    if parsed.domain and parsed.suffix:
        return f"{parsed.domain}.{parsed.suffix}".lower()
    return (host or "").lower()


router = APIRouter(prefix="/api/tls-failures", tags=["tls-failures"])


class IngestBody(BaseModel):
    client_ip: str
    host: str


@router.post("")
def ingest(body: IngestBody) -> dict:
    """mitmproxy addon → here. Resolves the client IP to a kid/device via
    kids.yaml, then bumps the row in `tls_failures`."""
    host = (body.host or "").strip().lower()
    client_ip = (body.client_ip or "").strip()
    if not host or not client_ip:
        raise HTTPException(400, "client_ip and host required")
    cfg = store.load()
    found = cfg.device_by_ip(client_ip)
    kid_name = found[0].name if found else None
    device_name = found[1].name if found else None
    db.upsert_tls_failure(
        kid=kid_name,
        device=device_name,
        client_ip=client_ip,
        host=host,
        registrable=registrable_domain(host),
    )
    return {"ok": True}


@router.get("")
def list_failures(kid: str | None = None) -> dict:
    """Grouped failures for the Passthrough tab.

    Response shape:
      {"groups": [
        {"registrable": "reddit.com",
         "ts_last": "...", "count": 17, "kid": "Clifton",
         "children": [{"id": 12, "host": "gql-fed.reddit.com",
                       "count": 9, "ts_first": "...", "ts_last": "...",
                       "device": "phone"}, ...]
        }, ...]}
    """
    rows = db.list_tls_failures(kid=kid)
    grouped: dict[tuple[str | None, str], dict] = {}
    for r in rows:
        key = (r.kid, r.registrable)
        g = grouped.get(key)
        if g is None:
            g = {
                "registrable": r.registrable,
                "kid": r.kid,
                "count": 0,
                "ts_last": None,
                "children": [],
            }
            grouped[key] = g
        g["count"] += r.count
        if g["ts_last"] is None or (r.ts_last and r.ts_last > g["ts_last"]):
            g["ts_last"] = r.ts_last
        g["children"].append({
            "id": r.id,
            "host": r.host,
            "device": r.device,
            "client_ip": r.client_ip,
            "count": r.count,
            "ts_first": r.ts_first.isoformat() if r.ts_first else None,
            "ts_last": r.ts_last.isoformat() if r.ts_last else None,
        })
    # Stringify the group ts_last for JSON, sort children newest-first,
    # sort groups by most recent activity.
    out = []
    for g in grouped.values():
        g["children"].sort(key=lambda c: c["ts_last"] or "", reverse=True)
        g["ts_last"] = g["ts_last"].isoformat() if g["ts_last"] else None
        out.append(g)
    out.sort(key=lambda g: g["ts_last"] or "", reverse=True)
    return {"groups": out}


@router.delete("/{failure_id}", status_code=204)
def dismiss(failure_id: int):
    if not db.delete_tls_failure(failure_id):
        raise HTTPException(404, "unknown failure")
    return None
