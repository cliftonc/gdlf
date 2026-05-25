"""Ephemeral SQLite for request events, handshake state, MDM bookkeeping.

This is *not* the source of truth — kids.yaml is. The DB is fine to wipe.

The `event` table is the single source of truth for activity logging. Both
the live feed (`/api/activity` + SSE) and the counter overview
(`/api/stats/*`) read from it directly. Rows are deduplicated within a
configurable session window (`settings.stats_bucket_secs`, default 300s)
keyed on (kid, host, path, query, decision, bucket_ts) — repeat visits
inside the window bump `hit_count` and `ts_last` on the existing row
instead of inserting a new one.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .settings import settings


class Event(SQLModel, table=True):
    """A network observation by mitmproxy (SNI splice, MITM nav, block, etc.).

    One row per (kid, host, path, query, decision, bucket_ts) — repeat
    hits in the same session window bump `hit_count` and `ts_last`.
    """
    id: int | None = Field(default=None, primary_key=True)
    # First time the row was created (within its bucket). For "newest
    # activity" use `ts_last` instead.
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    # Last hit observed for this (deduped) row. The feed and counter
    # queries both sort/window on this.
    ts_last: datetime = Field(default_factory=datetime.utcnow, index=True)
    # Number of hits collapsed into this row.
    hit_count: int = Field(default=1)
    # `ts` floored to settings.stats_bucket_secs. Part of the dedup key.
    bucket_ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    source: str  # 'mitmproxy' | 'adguard'
    client_ip: str = Field(index=True)
    kid: str | None = Field(default=None, index=True)
    device: str | None = None
    method: str | None = None
    host: str
    # eTLD+1 of `host`, set at insert time so counter queries can
    # `GROUP BY registrable` without re-parsing.
    registrable: str | None = Field(default=None, index=True)
    path: str | None = None
    query: str | None = None
    status: int | None = None
    decision: str  # 'allow' | 'block' | 'flag' | 'tls_failed' | 'dns_block' | 'passthrough'
    rule: str | None = None
    sni_only: bool = False
    note: str | None = None
    # Browser hint about what was requested:
    #   page   — navigation (Sec-Fetch-Dest=document)
    #   iframe — iframe / nested document
    #   sni    — SNI-only (spliced TLS, no decryption)
    #   pinned — MITM-inspected host whose app rejected our cert
    #   asset/xhr/ws — sub-resource noise (dropped at ingest unless
    #                  decision in {block,flag,tls_failed,dns_block})
    #   unknown — classifier was uncertain; treated as a page navigation
    kind: str | None = Field(default=None, index=True)


class Handshake(SQLModel, table=True):
    """Track per-WG-peer most-recent handshake for the dashboard."""
    wg_ip: str = Field(primary_key=True)
    last_seen: datetime = Field(default_factory=datetime.utcnow)
    bytes_rx: int = 0
    bytes_tx: int = 0


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
_EPOCH = datetime(1970, 1, 1)


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
        _migrate(_engine)
    return _engine


def _migrate(eng) -> None:
    """Idempotent schema migrations. Runs at every startup.

    Adds the dedup columns to `event` (wiping existing rows — they have no
    bucket_ts and can't reliably be coerced into the new dedup key) and
    drops the obsolete `domain_stats` / `tls_failures` rollup tables.
    """
    import logging
    log = logging.getLogger("gdlf.db")
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        cols = {row[0] for row in c.exec_driver_sql(
            "SELECT name FROM pragma_table_info('event')"
        )}
        if cols and "hit_count" not in cols:
            # Old schema: clear the events table so the new unique index can
            # be created without collisions. State is ephemeral by design
            # (see CLAUDE.md). The accumulator-backed domain_stats rollup
            # also goes away in this migration.
            (count,) = c.exec_driver_sql("SELECT COUNT(*) FROM event").one()
            if count:
                log.warning(
                    "wiping %d legacy events while migrating to dedup-based logging",
                    count,
                )
            c.exec_driver_sql("DELETE FROM event")
            c.exec_driver_sql("ALTER TABLE event ADD COLUMN hit_count INTEGER NOT NULL DEFAULT 1")
            c.exec_driver_sql("ALTER TABLE event ADD COLUMN ts_last DATETIME")
            c.exec_driver_sql("ALTER TABLE event ADD COLUMN bucket_ts DATETIME")
            c.exec_driver_sql("ALTER TABLE event ADD COLUMN registrable TEXT")

        # Indexes the SQLModel definition doesn't create directly.
        c.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_event_session ON event ("
            "COALESCE(kid,''), COALESCE(host,''), COALESCE(path,''), "
            "COALESCE(query,''), COALESCE(decision,''), bucket_ts)"
        )
        c.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_event_kid_ts_last ON event (kid, ts_last)"
        )
        c.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_event_registrable_ts_last "
            "ON event (registrable, ts_last)"
        )

        # Obsolete tables — dropped on first migration after the upgrade.
        c.exec_driver_sql("DROP TABLE IF EXISTS domain_stats")
        c.exec_driver_sql("DROP TABLE IF EXISTS tls_failures")


def session() -> Session:
    return Session(engine())


def insert(obj) -> None:
    """Synchronous insert — used for non-Event tables (handshake, alerts,
    MDM bookkeeping). Activity events go through `insert_or_bump` to get
    session-window dedup."""
    with session() as s:
        s.add(obj)
        s.commit()


def bucket_floor(ts: datetime) -> datetime:
    """Floor `ts` (naive UTC) to the session-window grid.

    Direct arithmetic against a fixed UTC epoch — `ts.timestamp()` on a
    naive datetime would interpret it in the container's local timezone
    (TZ=Europe/London), silently misaligning buckets by 1–2h vs the UTC
    values we read back.
    """
    bucket_secs = max(60, settings.stats_bucket_secs)
    epoch = int((ts - _EPOCH).total_seconds())
    return _EPOCH + timedelta(seconds=epoch - (epoch % bucket_secs))


def insert_or_bump(
    *,
    source: str,
    client_ip: str,
    kid: str | None,
    device: str | None,
    method: str | None,
    host: str,
    registrable: str | None,
    path: str | None,
    query: str | None,
    status: int | None,
    decision: str,
    rule: str | None,
    sni_only: bool,
    kind: str | None,
    note: str | None,
    ts: datetime | None = None,
) -> tuple[int, bool]:
    """Insert an event row, or bump (hit_count, ts_last) on an existing
    same-bucket row.

    Returns (event_id, was_new). `was_new=True` iff a fresh row was
    inserted; the caller uses this to fire alerts only once per session
    window per matching event.

    The unique key is (kid, host, path, query, decision, bucket_ts) — see
    `uq_event_session` in `_migrate()`. Fields that vary across hits
    (status, rule, note) are filled in on the first row that has them.
    """
    now = ts or datetime.utcnow()
    bucket = bucket_floor(now)
    sql = text(
        """
        INSERT INTO event (
          ts, ts_last, bucket_ts, hit_count, source, client_ip,
          kid, device, method, host, registrable, path, query,
          status, decision, rule, sni_only, kind, note
        ) VALUES (
          :ts, :ts, :bucket, 1, :source, :client_ip,
          :kid, :device, :method, :host, :registrable, :path, :query,
          :status, :decision, :rule, :sni_only, :kind, :note
        )
        ON CONFLICT (
          COALESCE(kid,''), COALESCE(host,''), COALESCE(path,''),
          COALESCE(query,''), COALESCE(decision,''), bucket_ts
        ) DO UPDATE SET
          hit_count = event.hit_count + 1,
          ts_last = excluded.ts_last,
          status = COALESCE(excluded.status, event.status),
          rule = COALESCE(excluded.rule, event.rule),
          note = COALESCE(excluded.note, event.note),
          device = COALESCE(excluded.device, event.device),
          registrable = COALESCE(excluded.registrable, event.registrable),
          method = COALESCE(excluded.method, event.method)
        RETURNING id, hit_count
        """
    )
    params = {
        "ts": now,
        "bucket": bucket,
        "source": source,
        "client_ip": client_ip,
        "kid": kid,
        "device": device,
        "method": method,
        "host": host,
        "registrable": registrable,
        "path": path,
        "query": query,
        "status": status,
        "decision": decision,
        "rule": rule,
        "sni_only": 1 if sni_only else 0,
        "kind": kind,
        "note": note,
    }
    with session() as s:
        row = s.connection().execute(sql, params).first()
        s.commit()
    if row is None:
        return (0, False)
    event_id, hit_count = int(row[0]), int(row[1])
    return (event_id, hit_count == 1)


def get_event(event_id: int) -> Event | None:
    with session() as s:
        return s.get(Event, event_id)


def recent_events(limit: int = 200, kid: str | None = None, decision: str | None = None) -> list[Event]:
    """Most-recently-touched events first (ordered by `ts_last`).

    Both newly-inserted rows and bumped rows surface here in real-time.
    """
    with session() as s:
        stmt = select(Event).order_by(Event.ts_last.desc()).limit(limit)
        if kid:
            stmt = stmt.where(Event.kid == kid)
        if decision:
            stmt = stmt.where(Event.decision == decision)
        return list(s.exec(stmt).all())


def stats() -> dict:
    """Return event-table size info for the Settings page."""
    from sqlmodel import func
    p = _db_path()
    with session() as s:
        total = s.exec(select(func.count()).select_from(Event)).one()
        oldest = s.exec(select(func.min(Event.ts_last))).one()
        newest = s.exec(select(func.max(Event.ts_last))).one()
    return {
        "events": int(total or 0),
        "oldest": oldest,
        "newest": newest,
        "db_path": str(p),
        "db_bytes": p.stat().st_size if p.exists() else 0,
    }


def prune(retention_days: int, max_events: int, **_legacy_kwargs) -> dict:
    """Delete events whose `ts_last` is older than `retention_days`, then
    trim to `max_events` (oldest `ts_last` first).

    Returns counts so the caller can log them. `**_legacy_kwargs` swallows
    deprecated arguments (e.g. `stats_retention_days`) from older callers.
    """
    from sqlmodel import delete, func

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    age_deleted = 0
    cap_deleted = 0
    with session() as s:
        res = s.exec(delete(Event).where(Event.ts_last < cutoff))
        age_deleted = res.rowcount or 0
        s.commit()
        total = s.exec(select(func.count()).select_from(Event)).one()
        if total and total > max_events:
            overflow = total - max_events
            ids_to_drop = list(s.exec(
                select(Event.id).order_by(Event.ts_last.asc()).limit(overflow)
            ).all())
            if ids_to_drop:
                s.exec(delete(Event).where(Event.id.in_(ids_to_drop)))
                cap_deleted = len(ids_to_drop)
                s.commit()
    return {"age_deleted": age_deleted, "cap_deleted": cap_deleted}


def vacuum() -> None:
    """Reclaim disk space after large prunes. VACUUM can't run inside a
    transaction, so we drop to AUTOCOMMIT."""
    with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM")
