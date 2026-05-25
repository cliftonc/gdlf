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


def _group_patterns(registrable: str) -> tuple[str, str]:
    """The two fnmatch globs that cover a registrable domain — the apex itself
    and any subdomain. Mirrors `groupPatterns` in the SPA so on/off semantics
    stay in sync between server-side auto-enable and the toggle endpoint."""
    return (registrable, f"*.{registrable}")


def _auto_enable_passthrough(kid_name: str, registrable: str) -> None:
    """If `registrable` isn't on the kid's opt-out list, ensure both group
    patterns are present in their `mitm_passthrough_hosts`. Idempotent —
    only writes the YAML when something actually changes."""
    if not registrable:
        return
    cfg = store.load()
    kid = cfg.kid(kid_name)
    if not kid:
        return
    if registrable in kid.mitm_passthrough_disabled:
        return
    apex, wild = _group_patterns(registrable)
    have = set(kid.mitm_passthrough_hosts)
    if apex in have and wild in have:
        return

    def _add(c):
        k = c.kid(kid_name)
        if not k:
            return
        if registrable in k.mitm_passthrough_disabled:
            return
        merged = set(k.mitm_passthrough_hosts)
        merged.update(_group_patterns(registrable))
        k.mitm_passthrough_hosts = sorted(merged)

    store.mutate(_add)


class IngestBody(BaseModel):
    client_ip: str
    host: str


@router.post("")
def ingest(body: IngestBody) -> dict:
    """mitmproxy addon → here. Resolves the client IP to a kid/device via
    kids.yaml, bumps the row in `tls_failures`, and (unless the parent has
    opted the registrable out) auto-adds the apex + wildcard to the kid's
    passthrough list so the next handshake just works."""
    host = (body.host or "").strip().lower()
    client_ip = (body.client_ip or "").strip()
    if not host or not client_ip:
        raise HTTPException(400, "client_ip and host required")
    cfg = store.load()
    found = cfg.device_by_ip(client_ip)
    kid_name = found[0].name if found else None
    device_name = found[1].name if found else None
    reg = registrable_domain(host)
    db.upsert_tls_failure(
        kid=kid_name,
        device=device_name,
        client_ip=client_ip,
        host=host,
        registrable=reg,
    )
    if kid_name:
        _auto_enable_passthrough(kid_name, reg)
    return {"ok": True}


@router.get("")
def list_failures(kid: str | None = None) -> dict:
    """Grouped failures for the Passthrough tab.

    Backfills auto-passthrough for any observed registrable not on the kid's
    opt-out list — covers failures that pre-date the default-on behaviour so
    the parent doesn't have to wait for the next retry to see things working.

    Response shape:
      {"groups": [
        {"registrable": "reddit.com", "enabled": true,
         "ts_last": "...", "count": 17, "kid": "Clifton",
         "children": [{"id": 12, "host": "gql-fed.reddit.com",
                       "count": 9, "ts_first": "...", "ts_last": "...",
                       "device": "phone"}, ...]
        }, ...]}
    """
    rows = db.list_tls_failures(kid=kid)

    # Gather (kid, registrable) pairs and backfill any missing auto-passthrough
    # in a single mutate. Cheap when nothing's missing — we compare set-by-set
    # before deciding to write.
    observed: dict[str, set[str]] = {}
    for r in rows:
        if r.kid and r.registrable:
            observed.setdefault(r.kid, set()).add(r.registrable)
    if observed:
        _backfill_auto_passthrough(observed)

    cfg = store.load()
    disabled_by_kid: dict[str, set[str]] = {
        k.name: set(k.mitm_passthrough_disabled) for k in cfg.kids
    }

    grouped: dict[tuple[str | None, str], dict] = {}
    for r in rows:
        key = (r.kid, r.registrable)
        g = grouped.get(key)
        if g is None:
            enabled = not (r.kid and r.registrable in disabled_by_kid.get(r.kid, set()))
            g = {
                "registrable": r.registrable,
                "kid": r.kid,
                "enabled": enabled,
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
    out = []
    for g in grouped.values():
        g["children"].sort(key=lambda c: c["host"])
        g["ts_last"] = g["ts_last"].isoformat() if g["ts_last"] else None
        out.append(g)
    # Server-side sort is alphabetical by registrable; the SPA may re-sort
    # but defaulting here keeps non-SPA consumers tidy too.
    out.sort(key=lambda g: g["registrable"] or "")
    return {"groups": out}


def _backfill_auto_passthrough(observed: dict[str, set[str]]) -> None:
    """For every (kid, registrable) seen, ensure the apex + wildcard are in
    the kid's passthrough list — unless the parent explicitly disabled it.
    No-op when nothing's missing, so safe to call from every list request."""
    cfg = store.load()
    pending: dict[str, set[str]] = {}
    for kname, regs in observed.items():
        k = cfg.kid(kname)
        if not k:
            continue
        disabled = set(k.mitm_passthrough_disabled)
        have = set(k.mitm_passthrough_hosts)
        add: set[str] = set()
        for reg in regs:
            if reg in disabled:
                continue
            apex, wild = _group_patterns(reg)
            if apex not in have:
                add.add(apex)
            if wild not in have:
                add.add(wild)
        if add:
            pending[kname] = add
    if not pending:
        return

    def _apply(c):
        for kname, add in pending.items():
            k = c.kid(kname)
            if not k:
                continue
            disabled = set(k.mitm_passthrough_disabled)
            merged = set(k.mitm_passthrough_hosts)
            for pat in add:
                # Re-check disabled in case the parent toggled off between
                # the snapshot and the mutation.
                reg = pat[2:] if pat.startswith("*.") else pat
                if reg in disabled:
                    continue
                merged.add(pat)
            k.mitm_passthrough_hosts = sorted(merged)

    store.mutate(_apply)


@router.delete("/{failure_id}", status_code=204)
def dismiss(failure_id: int):
    if not db.delete_tls_failure(failure_id):
        raise HTTPException(404, "unknown failure")
    return None
