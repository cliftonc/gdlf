"""Per-kid activity counters, derived from the `event` table.

There is no parallel in-memory accumulator: the `event` table is the only
source of truth, and counters are pure SQL aggregations over it. Each
event row carries a `hit_count` (number of identical hits collapsed into
that row by the session-window upsert), so the visible numbers always
match `SUM(hit_count)` of the rows shown in the activity feed.

A handful of legacy entry points are exported (`record`, `flush_now`,
`flush_loop`) so callers that haven't been updated yet are no-ops rather
than import errors.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import text

from . import db
from .settings import settings

log = logging.getLogger("gdlf.aggregates")


# ---------------------------------------------------------------------------
# Legacy no-ops. Kept so any straggling caller (or addon) doesn't crash at
# import. Deletion is safe once nothing references them.

def record(**_kwargs) -> None:
    """Deprecated — counters are derived from `event` rows; nothing to do."""
    return None


async def flush_now() -> int:
    return 0


async def flush_loop() -> None:
    """No-op loop. Kept so lifespan code that still schedules it stays a
    valid coroutine; cancellation just exits cleanly."""
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise


# ---------------------------------------------------------------------------
# Read helpers used by api_stats. Both run as a single SQLite GROUP BY
# against the `event` table.

_PAGE_KIND_SQL = "('page','iframe','unknown')"
_BLOCK_DECISION_SQL = "('block','flag')"


def _top_hosts(kid: str, cutoff: datetime, *, top_n: int) -> list[dict]:
    """`registrable` is set at insert time, so we can group on it directly.

    Rows from pre-migration installs may have NULL registrable; fall back
    to `host` so they still surface (just without subdomain collapsing).
    """
    sql = text(f"""
        SELECT COALESCE(registrable, host) AS host,
               SUM(hit_count) AS requests,
               SUM(CASE WHEN COALESCE(kind,'') IN {_PAGE_KIND_SQL}
                        THEN hit_count ELSE 0 END) AS pages,
               SUM(CASE WHEN decision IN {_BLOCK_DECISION_SQL}
                        THEN hit_count ELSE 0 END) AS blocked
        FROM event
        WHERE kid = :kid AND ts_last >= :cutoff
        GROUP BY COALESCE(registrable, host)
        ORDER BY requests DESC
        LIMIT :n
    """)
    with db.session() as s:
        rows = s.connection().execute(
            sql, {"kid": kid, "cutoff": cutoff, "n": top_n}
        ).all()
    return [
        {
            "host": r[0],
            "requests": int(r[1] or 0),
            "pages": int(r[2] or 0),
            "blocked": int(r[3] or 0),
        }
        for r in rows
    ]


def top_hosts_24h_for_kid(name: str, *, top_n: int = 20) -> list[dict]:
    """Top hosts (by request count) for one kid over the last 24 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    return _top_hosts(name, cutoff, top_n=top_n)


def overview_for_kids(
    kid_names: list[str],
    *,
    window_1h: timedelta = timedelta(hours=1),
    window_24h: timedelta = timedelta(hours=24),
    top_n: int = 5,
    sparkline_buckets: int = 12,
) -> dict[str, dict]:
    """Aggregate `event` rows into per-kid summary dicts.

    Returns `{kid_name: summary}` for every name in `kid_names`, including
    kids with zero activity (so the UI can render an empty card). All
    arithmetic is on `SUM(hit_count)`, which equals the visible activity
    feed for the same window.
    """
    if not kid_names:
        return {}
    now = datetime.utcnow()
    cutoff_24h = now - window_24h
    cutoff_1h = now - window_1h
    bucket_secs = max(60, settings.stats_bucket_secs)

    # Per-kid totals (1h + 24h) and last_seen in one pass. SQLAlchemy's IN
    # expansion via bindparam expanding=True is awkward with raw text(); a
    # parameter-per-name list keeps this query single-call without ORM glue.
    placeholders = ", ".join(f":n{i}" for i in range(len(kid_names)))
    totals_sql = text(f"""
        SELECT kid,
               SUM(hit_count),
               SUM(CASE WHEN COALESCE(kind,'') IN {_PAGE_KIND_SQL}
                        THEN hit_count ELSE 0 END),
               SUM(CASE WHEN decision IN {_BLOCK_DECISION_SQL}
                        THEN hit_count ELSE 0 END),
               SUM(CASE WHEN ts_last >= :cutoff_1h THEN hit_count ELSE 0 END),
               SUM(CASE WHEN ts_last >= :cutoff_1h AND COALESCE(kind,'') IN {_PAGE_KIND_SQL}
                        THEN hit_count ELSE 0 END),
               SUM(CASE WHEN ts_last >= :cutoff_1h AND decision IN {_BLOCK_DECISION_SQL}
                        THEN hit_count ELSE 0 END),
               MAX(ts_last)
        FROM event
        WHERE kid IN ({placeholders}) AND ts_last >= :cutoff_24h
        GROUP BY kid
    """)
    params: dict = {"cutoff_24h": cutoff_24h, "cutoff_1h": cutoff_1h}
    for i, n in enumerate(kid_names):
        params[f"n{i}"] = n

    with db.session() as s:
        rows = s.connection().execute(totals_sql, params).all()

    totals: dict[str, dict] = {}
    for r in rows:
        totals[r[0]] = {
            "requests_24h": int(r[1] or 0),
            "pages_24h": int(r[2] or 0),
            "blocked_24h": int(r[3] or 0),
            "requests_1h": int(r[4] or 0),
            "pages_1h": int(r[5] or 0),
            "blocked_1h": int(r[6] or 0),
            "last_seen": r[7],
        }

    # Sparkline: 12 buckets ending at the current bucket. Use SQLite's
    # strftime to floor `ts_last` onto the bucket grid in one GROUP BY.
    spark_end_epoch = (
        int((now - db._EPOCH).total_seconds()) // bucket_secs
    ) * bucket_secs
    spark_start_epoch = spark_end_epoch - bucket_secs * (sparkline_buckets - 1)
    spark_start = db._EPOCH + timedelta(seconds=spark_start_epoch)
    spark_sql = text(f"""
        SELECT kid,
               (CAST(strftime('%s', ts_last) AS INTEGER) / :bucket_secs) AS slot,
               SUM(hit_count)
        FROM event
        WHERE kid IN ({placeholders}) AND ts_last >= :spark_start
        GROUP BY kid, slot
    """)
    spark_params: dict = {"bucket_secs": bucket_secs, "spark_start": spark_start}
    for i, n in enumerate(kid_names):
        spark_params[f"n{i}"] = n
    with db.session() as s:
        spark_rows = s.connection().execute(spark_sql, spark_params).all()

    spark_by_kid: dict[str, list[int]] = defaultdict(
        lambda: [0] * sparkline_buckets
    )
    base_slot = spark_start_epoch // bucket_secs
    for kid, slot, total in spark_rows:
        idx = int(slot) - int(base_slot)
        if 0 <= idx < sparkline_buckets:
            spark_by_kid[kid][idx] += int(total or 0)

    out: dict[str, dict] = {}
    for name in kid_names:
        t = totals.get(name, {})
        last_seen = t.get("last_seen")
        # Raw text() queries hand back SQLite's TEXT storage as a str;
        # ORM-style selects would return a datetime. Normalise so the
        # API contract is always an ISO string regardless of path.
        if isinstance(last_seen, datetime):
            last_seen_iso = last_seen.isoformat()
        elif isinstance(last_seen, str):
            last_seen_iso = last_seen
        else:
            last_seen_iso = None
        out[name] = {
            "kid": name,
            "last_seen": last_seen_iso,
            "requests_1h": t.get("requests_1h", 0),
            "pages_1h": t.get("pages_1h", 0),
            "blocked_1h": t.get("blocked_1h", 0),
            "requests_24h": t.get("requests_24h", 0),
            "pages_24h": t.get("pages_24h", 0),
            "blocked_24h": t.get("blocked_24h", 0),
            "top_hosts_1h": _top_hosts(name, cutoff_1h, top_n=top_n),
            "sparkline_1h": spark_by_kid.get(name, [0] * sparkline_buckets),
            "bucket_secs": bucket_secs,
        }
    return out
