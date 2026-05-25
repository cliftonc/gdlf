"""Ephemeral SQLite for request events, AdGuard log entries, handshake state.

This is *not* the source of truth — kids.yaml is. The DB is fine to wipe.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import UniqueConstraint, text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .settings import settings


class Event(SQLModel, table=True):
    """A single request observed by mitmproxy (or AdGuard, if we ingest those)."""
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    source: str  # 'mitmproxy' | 'adguard'
    client_ip: str = Field(index=True)
    kid: str | None = Field(default=None, index=True)
    device: str | None = None
    method: str | None = None
    host: str
    path: str | None = None
    query: str | None = None
    status: int | None = None
    decision: str  # 'allow' | 'block' | 'flag' | 'sni_only' | 'dns_block'
    rule: str | None = None  # which rule matched, if any
    sni_only: bool = False
    note: str | None = None
    # Browser hint about what was requested:
    #   page  (Sec-Fetch-Dest=document/iframe) — a navigation, what we care about by default
    #   asset (image/script/style/font/video/...) — sub-resource
    #   xhr   (Sec-Fetch-Dest=empty) — fetch/XHR, often analytics beacons
    #   unknown — addon couldn't classify (SNI-only event)
    kind: str | None = Field(default=None, index=True)


class DomainStat(SQLModel, table=True):
    """Per-(kid, host, 5-min bucket) rollup of request counts.

    Populated by `aggregates.flush()` every ~30s from an in-memory accumulator
    fed by `/api/events`. Counters cover every request the addon sees — pages,
    sub-resources, XHRs, WebSockets — so the dashboard can show a true
    "what is each kid hitting right now" view without keeping a row per
    request in the events table.

    The raw `events` table only carries page navigations + blocks/flags now;
    counters here are the volume signal.
    """
    __tablename__ = "domain_stats"
    kid: str = Field(primary_key=True, index=True)
    host: str = Field(primary_key=True)
    bucket_ts: datetime = Field(primary_key=True, index=True)
    requests: int = 0       # all kinds (page + iframe + asset + xhr + ws)
    pages: int = 0          # kind in {page, iframe}
    blocked: int = 0        # decision in {block, flag}
    last_seen: datetime = Field(default_factory=datetime.utcnow)


class Handshake(SQLModel, table=True):
    """Track per-WG-peer most-recent handshake for the dashboard."""
    wg_ip: str = Field(primary_key=True)
    last_seen: datetime = Field(default_factory=datetime.utcnow)
    bytes_rx: int = 0
    bytes_tx: int = 0


class TlsFailure(SQLModel, table=True):
    """Per-(kid, host) observation of a TLS handshake failure.

    Populated by the mitmproxy addon. The Passthrough tab in the dashboard
    groups these by registrable domain (eTLD+1) so the parent can enable
    `*.reddit.com` style passthrough with one switch instead of allowing
    every subdomain separately.

    Distinct from `Event` because (a) it's a long-lived observation
    (count + first/last seen, not one-row-per-occurrence), and (b) we want
    it out of the activity log — pinned-cert apps would otherwise dominate.
    """
    __tablename__ = "tls_failures"
    __table_args__ = (
        UniqueConstraint("kid", "host", name="uq_tls_failures_kid_host"),
    )
    id: int | None = Field(default=None, primary_key=True)
    ts_first: datetime = Field(default_factory=datetime.utcnow)
    ts_last: datetime = Field(default_factory=datetime.utcnow, index=True)
    count: int = 1
    kid: str | None = Field(default=None, index=True)
    device: str | None = None
    client_ip: str
    host: str = Field(index=True)
    # Public-suffix-aware registrable domain (eTLD+1) used to group rows.
    registrable: str = Field(index=True)


class AlertLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    kid: str
    event_id: int | None = None
    channel: str  # 'webhook' | 'email'
    ok: bool
    detail: str | None = None


class MdmEnrollToken(SQLModel, table=True):
    """One-time token tying an enrolment URL to a specific device.

    Issued by the dashboard, embedded in the URL the parent opens in
    Safari / hands to Apple Configurator. Marked `used` on first fetch
    of the .mobileconfig; expires after a short TTL regardless.
    """
    __tablename__ = "mdm_enroll_tokens"
    token: str = Field(primary_key=True)            # random 32-byte urlsafe
    wg_ip: str = Field(index=True)                  # device this token enrols
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    used_at: datetime | None = None


class MdmCommandQueue(SQLModel, table=True):
    """Pending MDM commands per device. Phase 3 fills this in; Phase 2 just
    creates the table so we don't need a second migration later."""
    __tablename__ = "mdm_command_queue"
    id: int | None = Field(default=None, primary_key=True)
    identity_cn: str = Field(index=True)            # which device
    command_uuid: str = Field(index=True)
    request_type: str                                # InstallProfile, DeviceLock, etc.
    payload: str                                     # plist XML (the inner command)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: datetime | None = None
    completed_at: datetime | None = None
    status: str = "pending"                          # pending|sent|acknowledged|error


class MdmCommandResponse(SQLModel, table=True):
    """Device responses to MDM commands. Phase 3."""
    __tablename__ = "mdm_command_responses"
    id: int | None = Field(default=None, primary_key=True)
    identity_cn: str = Field(index=True)
    command_uuid: str = Field(index=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    status: str                                      # Acknowledged|Error|NotNow|CommandFormatError
    response_plist: str | None = None                # full response body, for debugging


class DeviceShortlink(SQLModel, table=True):
    """Short, parent-shareable code that authenticates a single device's
    enrolment page without a cookie session.

    The code is the auth token for the (wg_ip) it owns: presented as
    `?dl=<code>` on the existing `/api/devices/{ip}/...` and
    `/api/kids/{name}/devices/{ip}/...` endpoints, the auth middleware
    accepts it iff the path's `{ip}` matches `wg_ip`. The SPA route
    `/dl/{code}` resolves to that device's enrolment page.
    """
    __tablename__ = "device_shortlinks"
    code: str = Field(primary_key=True)            # 4-char base32, uppercase
    wg_ip: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WindowsEnrollToken(SQLModel, table=True):
    """One-time token tying a Windows provisioning package download to a
    specific device.

    The token + per-device .ppkg blob is built at mint time and cached
    on-disk under <state_dir>/windows/packages/<token>.ppkg. First fetch
    marks `used`; the file is unlinked at the same time so the blob can't
    be re-downloaded. Re-enrolling means a fresh token (and a fresh GUID-
    versioned package).
    """
    __tablename__ = "windows_enroll_tokens"
    token: str = Field(primary_key=True)
    wg_ip: str = Field(index=True)
    package_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    used_at: datetime | None = None
    # Set when the package is a revocation (uninstall) variant instead of
    # an enrollment one — same on-disk shape, different customizations.xml.
    revoke: bool = False


_engine = None


def _db_path() -> Path:
    p = settings.state_dir / "gdlf.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{_db_path()}", echo=False)
        # WAL = concurrent readers + a single writer don't block each other.
        # Matters because the prune task and the /api/events writer race.
        with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.exec_driver_sql("PRAGMA journal_mode=WAL")
            c.exec_driver_sql("PRAGMA synchronous=NORMAL")
        SQLModel.metadata.create_all(_engine)
    return _engine


def session() -> Session:
    return Session(engine())


def insert(obj) -> None:
    with session() as s:
        s.add(obj)
        s.commit()


def recent_events(limit: int = 200, kid: str | None = None, decision: str | None = None) -> list[Event]:
    with session() as s:
        stmt = select(Event).order_by(Event.ts.desc()).limit(limit)
        if kid:
            stmt = stmt.where(Event.kid == kid)
        if decision:
            stmt = stmt.where(Event.decision == decision)
        return list(s.exec(stmt).all())


def upsert_tls_failure(
    *,
    kid: str | None,
    device: str | None,
    client_ip: str,
    host: str,
    registrable: str,
) -> None:
    """Insert or bump-the-counter for a (kid, host) TLS failure observation.

    `kid` may be None when the source IP doesn't resolve to a known device
    (orphaned WG peer). We still store those — at least the parent can see
    something failed somewhere, and they get pruned naturally over time.
    """
    now = datetime.utcnow()
    with session() as s:
        stmt = select(TlsFailure).where(
            TlsFailure.kid == kid,
            TlsFailure.host == host,
        )
        existing = s.exec(stmt).first()
        if existing:
            existing.ts_last = now
            existing.count += 1
            existing.client_ip = client_ip
            if device:
                existing.device = device
            s.add(existing)
        else:
            s.add(
                TlsFailure(
                    kid=kid,
                    device=device,
                    client_ip=client_ip,
                    host=host,
                    registrable=registrable,
                    ts_first=now,
                    ts_last=now,
                )
            )
        s.commit()


def list_tls_failures(kid: str | None = None) -> list[TlsFailure]:
    with session() as s:
        stmt = select(TlsFailure).order_by(TlsFailure.ts_last.desc())
        if kid:
            stmt = stmt.where(TlsFailure.kid == kid)
        return list(s.exec(stmt).all())


def delete_tls_failure(failure_id: int) -> bool:
    with session() as s:
        row = s.get(TlsFailure, failure_id)
        if not row:
            return False
        s.delete(row)
        s.commit()
        return True


def upsert_domain_stats(rows: list[dict]) -> None:
    """Bulk UPSERT counter rows from the in-memory accumulator.

    Each row must contain: kid, host, bucket_ts (datetime), requests, pages,
    blocked, last_seen (datetime). Uses SQLite's ON CONFLICT to add deltas
    onto an existing bucket. All rows go through in a single transaction so
    a 30s batch of (kid, host) updates costs one fsync.
    """
    if not rows:
        return
    sql = text(
        "INSERT INTO domain_stats "
        "(kid, host, bucket_ts, requests, pages, blocked, last_seen) "
        "VALUES (:kid, :host, :bucket_ts, :requests, :pages, :blocked, :last_seen) "
        "ON CONFLICT(kid, host, bucket_ts) DO UPDATE SET "
        "  requests = requests + excluded.requests, "
        "  pages    = pages    + excluded.pages, "
        "  blocked  = blocked  + excluded.blocked, "
        "  last_seen = MAX(last_seen, excluded.last_seen)"
    )
    with session() as s:
        s.connection().execute(sql, rows)
        s.commit()


def domain_stats_window(
    since: datetime,
    *,
    kid: str | None = None,
) -> list[DomainStat]:
    """Return raw bucket rows newer than `since`, optionally scoped to one kid."""
    with session() as s:
        stmt = select(DomainStat).where(DomainStat.bucket_ts >= since)
        if kid is not None:
            stmt = stmt.where(DomainStat.kid == kid)
        return list(s.exec(stmt).all())


def stats() -> dict:
    """Return event-table size info for the Settings page."""
    from sqlmodel import func
    p = _db_path()
    with session() as s:
        total = s.exec(select(func.count()).select_from(Event)).one()
        oldest = s.exec(select(func.min(Event.ts))).one()
        newest = s.exec(select(func.max(Event.ts))).one()
    return {
        "events": int(total or 0),
        "oldest": oldest,
        "newest": newest,
        "db_path": str(p),
        "db_bytes": p.stat().st_size if p.exists() else 0,
    }


def prune(retention_days: int, max_events: int, stats_retention_days: int | None = None) -> dict:
    """Delete events older than retention_days, then trim to max_events.

    Returns counts so the caller can log them. SQLite WAL serialises writes,
    so this is safe to run alongside the insert path."""
    from datetime import datetime, timedelta
    from sqlmodel import delete, func

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    age_deleted = 0
    cap_deleted = 0
    tls_deleted = 0
    stats_deleted = 0
    with session() as s:
        res = s.exec(delete(Event).where(Event.ts < cutoff))
        age_deleted = res.rowcount or 0
        s.commit()
        total = s.exec(select(func.count()).select_from(Event)).one()
        if total and total > max_events:
            overflow = total - max_events
            ids_to_drop = list(s.exec(
                select(Event.id).order_by(Event.id.asc()).limit(overflow)
            ).all())
            if ids_to_drop:
                s.exec(delete(Event).where(Event.id.in_(ids_to_drop)))
                cap_deleted = len(ids_to_drop)
                s.commit()
        # Same age cutoff for tls_failures — if a host hasn't failed in
        # `retention_days`, the parent has either fixed it via passthrough
        # or the app is gone. No cap on this table; it's tiny by design.
        res = s.exec(delete(TlsFailure).where(TlsFailure.ts_last < cutoff))
        tls_deleted = res.rowcount or 0
        s.commit()
        # Counter buckets get their own retention knob; default to `retention_days`
        # when not supplied so behaviour is unchanged for callers that don't pass it.
        days = stats_retention_days if stats_retention_days is not None else retention_days
        stats_cutoff = datetime.utcnow() - timedelta(days=days)
        res = s.exec(delete(DomainStat).where(DomainStat.bucket_ts < stats_cutoff))
        stats_deleted = res.rowcount or 0
        s.commit()
    return {
        "age_deleted": age_deleted,
        "cap_deleted": cap_deleted,
        "tls_deleted": tls_deleted,
        "stats_deleted": stats_deleted,
    }


def vacuum() -> None:
    """Reclaim disk space after large prunes. VACUUM can't run inside a
    transaction, so we drop to AUTOCOMMIT."""
    with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM")
