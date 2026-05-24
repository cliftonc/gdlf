"""Ephemeral SQLite for request events, AdGuard log entries, handshake state.

This is *not* the source of truth — kids.yaml is. The DB is fine to wipe.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

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


def prune(retention_days: int, max_events: int) -> dict:
    """Delete events older than retention_days, then trim to max_events.

    Returns counts so the caller can log them. SQLite WAL serialises writes,
    so this is safe to run alongside the insert path."""
    from datetime import datetime, timedelta
    from sqlmodel import delete, func

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    age_deleted = 0
    cap_deleted = 0
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
    return {"age_deleted": age_deleted, "cap_deleted": cap_deleted}


def vacuum() -> None:
    """Reclaim disk space after large prunes. VACUUM can't run inside a
    transaction, so we drop to AUTOCOMMIT."""
    with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM")
