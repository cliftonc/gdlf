"""TLS-failure tracking API.

Reads the same `event` table the activity feed does, filtered to
`decision='tls_failed'`. The mitmproxy addon's `tls_failed_client`
hook emits these via the unified `POST /api/events` ingest — there's
no separate write path anymore.

Under splice-by-default this stream is normally near-empty: we only
present a MITM cert for SNIs in the inspect list, so a non-inspect-listed
pinned app never fails — it splices. Rows here mean the parent
inspect-listed a domain whose app pins, and that's actionable: surface
it so they can remove it.

The `POST` and `DELETE` routes are kept as backwards-compat no-ops for
one release so a stale addon (or SPA) doesn't error against the new
backend.
"""
from __future__ import annotations

import tldextract
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from . import db, store

# In-memory snapshot of the public-suffix list (no network on container
# start). The bundled snapshot is fine for grouping.
_extract = tldextract.TLDExtract(suffix_list_urls=())


def registrable_domain(host: str) -> str:
    """eTLD+1 for grouping (`gql-fed.reddit.com` → `reddit.com`)."""
    parsed = _extract(host or "")
    if parsed.domain and parsed.suffix:
        return f"{parsed.domain}.{parsed.suffix}".lower()
    return (host or "").lower()


router = APIRouter(prefix="/api/tls-failures", tags=["tls-failures"])


class IngestBody(BaseModel):
    client_ip: str
    host: str
    error: str = ""


@router.post("")
def ingest(body: IngestBody) -> dict:
    """Legacy endpoint — kept as a no-op for one release.

    The mitmproxy addon now emits TLS failures via the unified
    `POST /api/events` channel (with `decision='tls_failed'`), which goes
    through the same dedup upsert as every other event.
    """
    if not (body.host or "").strip() or not (body.client_ip or "").strip():
        raise HTTPException(400, "client_ip and host required")
    return {"ok": True, "deprecated": True}


@router.get("")
def list_failures(kid: str | None = None) -> dict:
    """Grouped TLS failures for the Inspect tab.

    Response shape unchanged from the old `tls_failures`-backed version
    so the SPA doesn't need updates:

      {"groups": [
        {"registrable": "examplebank.com", "kid": "Clifton",
         "count": 17, "ts_last": "...", "error": "...",
         "children": [{"id": 12, "host": "api.examplebank.com",
                       "count": 9, "error": "...",
                       "ts_first": "...", "ts_last": "...",
                       "device": "phone"}, ...]
        }, ...]}
    """
    where = ["decision = 'tls_failed'"]
    params: dict = {}
    if kid is not None:
        where.append("kid = :kid")
        params["kid"] = kid
    sql = text(
        "SELECT id, kid, device, client_ip, host, "
        "COALESCE(registrable, host) AS registrable, "
        "SUM(hit_count) AS count, MIN(ts) AS ts_first, MAX(ts_last) AS ts_last, "
        "note AS error "
        "FROM event "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY kid, COALESCE(host,''), client_ip "
        "ORDER BY ts_last DESC"
    )
    with db.session() as s:
        rows = s.connection().execute(sql, params).all()

    grouped: dict[tuple[str | None, str], dict] = {}
    for r in rows:
        rid, rkid, device, client_ip, host, registrable, count, ts_first, ts_last, error = r
        key = (rkid, registrable)
        g = grouped.get(key)
        if g is None:
            g = {
                "registrable": registrable,
                "kid": rkid,
                "count": 0,
                "ts_last": None,
                "error": None,
                "children": [],
            }
            grouped[key] = g
        g["count"] += int(count or 0)
        if g["ts_last"] is None or (ts_last and ts_last > g["ts_last"]):
            g["ts_last"] = ts_last
            g["error"] = error
        g["children"].append({
            "id": int(rid) if rid is not None else None,
            "host": host,
            "device": device,
            "client_ip": client_ip,
            "count": int(count or 0),
            "error": error,
            "ts_first": ts_first.isoformat() if ts_first else None,
            "ts_last": ts_last.isoformat() if ts_last else None,
        })
    out = []
    for g in grouped.values():
        g["children"].sort(key=lambda c: c["host"] or "")
        g["ts_last"] = g["ts_last"].isoformat() if g["ts_last"] else None
        out.append(g)
    out.sort(key=lambda g: g["registrable"] or "")
    # Make sure the kid filter we got is also valid against kids.yaml so a
    # typo returns an empty list rather than misleading data.
    if kid is not None:
        cfg = store.load()
        if not any(k.name == kid for k in cfg.kids):
            return {"groups": []}
    return {"groups": out}


@router.delete("/{failure_id}", status_code=204)
def dismiss(failure_id: int):
    """Dismiss a single tls_failed row from the `event` table.

    The Inspect tab uses this to clear an entry once the parent has
    removed the host from the MITM list (or accepted that it'll keep
    failing). The row stays gone until a new failure repopulates it on
    the next handshake.
    """
    from sqlmodel import delete
    with db.session() as s:
        ev = s.get(db.Event, failure_id)
        if not ev or ev.decision != "tls_failed":
            raise HTTPException(404, "unknown failure")
        s.exec(delete(db.Event).where(db.Event.id == failure_id))
        s.commit()
    return None
