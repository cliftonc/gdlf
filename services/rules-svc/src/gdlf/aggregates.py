"""In-memory per-(kid, host, bucket) rollup of request counters.

Why this exists: the parent dashboard wants a near-real-time view of "what
is each kid hitting right now" without keeping a row-per-request in SQLite.
Every `/api/events` POST increments an in-process counter; a periodic
flush UPSERTs the deltas into `domain_stats` in one transaction.

Trade-off: a rules-svc crash loses up to STATS_FLUSH_SECS of counter data.
The raw `events` table is unaffected — its inserts are still synchronous.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from . import db
from .settings import settings

log = logging.getLogger("gdlf.aggregates")

_PAGE_KINDS = frozenset({"page", "iframe"})
_BLOCK_DECISIONS = frozenset({"block", "flag"})


_EPOCH = datetime(1970, 1, 1)


def _bucket_floor(ts: datetime, bucket_secs: int) -> datetime:
    """Floor `ts` (treated as naive UTC) to the nearest bucket boundary.

    Use direct arithmetic against a fixed UTC epoch — `ts.timestamp()` on a
    naive datetime would interpret it in the container's local timezone
    (TZ=Europe/London), which silently misaligns buckets by 1–2h vs the
    UTC values we read back when querying.
    """
    epoch = int((ts - _EPOCH).total_seconds())
    floored = epoch - (epoch % bucket_secs)
    return _EPOCH + timedelta(seconds=floored)


class _Accumulator:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # (kid, host, bucket_ts) -> {requests, pages, blocked, last_seen}
        self._buckets: dict[tuple[str, str, datetime], dict] = defaultdict(
            lambda: {"requests": 0, "pages": 0, "blocked": 0, "last_seen": None}
        )

    def record_sync(
        self,
        *,
        kid: str | None,
        host: str,
        kind: str | None,
        decision: str,
        ts: datetime | None = None,
    ) -> None:
        """Synchronous fast-path — call from request handlers without await.

        We don't take the asyncio.Lock here because dict-item mutation is
        atomic under CPython's GIL and the only racing reader is `flush()`
        which swaps the whole dict out before iterating. This keeps the
        `/api/events` write path lock-free.
        """
        if not kid or not host:
            return  # unknown peer — no kid to attribute counts to
        if ts is None:
            ts = datetime.utcnow()
        bucket = _bucket_floor(ts, settings.stats_bucket_secs)
        key = (kid, host, bucket)
        row = self._buckets[key]
        row["requests"] += 1
        if (kind or "") in _PAGE_KINDS:
            row["pages"] += 1
        if decision in _BLOCK_DECISIONS:
            row["blocked"] += 1
        prev = row["last_seen"]
        if prev is None or ts > prev:
            row["last_seen"] = ts

    async def flush(self) -> int:
        """Drain the accumulator into SQLite via bulk UPSERT.

        Returns the number of rows written (distinct keys, not request count).
        Errors are logged; on failure the drained batch is dropped — we'd
        rather lose 30s of counters than retry-loop a broken DB.
        """
        async with self._lock:
            if not self._buckets:
                return 0
            drained = self._buckets
            self._buckets = defaultdict(
                lambda: {"requests": 0, "pages": 0, "blocked": 0, "last_seen": None}
            )
        rows = []
        for (kid, host, bucket), row in drained.items():
            rows.append({
                "kid": kid,
                "host": host,
                "bucket_ts": bucket,
                "requests": row["requests"],
                "pages": row["pages"],
                "blocked": row["blocked"],
                "last_seen": row["last_seen"] or bucket,
            })
        try:
            await asyncio.to_thread(db.upsert_domain_stats, rows)
        except Exception as e:
            log.warning("domain_stats flush failed (%d rows lost): %s", len(rows), e)
            return 0
        return len(rows)


_accumulator = _Accumulator()


def record(
    *,
    kid: str | None,
    host: str,
    kind: str | None,
    decision: str,
    ts: datetime | None = None,
) -> None:
    """Cheap counter increment. Safe to call from anywhere — no await needed."""
    _accumulator.record_sync(kid=kid, host=host, kind=kind, decision=decision, ts=ts)


async def flush_now() -> int:
    """Force a flush — used at shutdown and from tests."""
    return await _accumulator.flush()


async def flush_loop() -> None:
    """Background task: flush every STATS_FLUSH_SECS seconds, plus once on exit."""
    interval = max(5, settings.stats_flush_secs)
    while True:
        try:
            await asyncio.sleep(interval)
            written = await _accumulator.flush()
            if written:
                log.debug("flushed %d domain_stats rows", written)
        except asyncio.CancelledError:
            # Final flush on shutdown so we don't drop the last partial bucket.
            try:
                await _accumulator.flush()
            except Exception as e:
                log.warning("final flush failed: %s", e)
            raise
        except Exception as e:
            log.warning("flush loop tick failed: %s", e)


# ---------------------------------------------------------------------------
# Read-side helpers used by api_stats.

def top_hosts_24h_for_kid(name: str, *, top_n: int = 20) -> list[dict]:
    """Top hosts (by request count) for one kid over the last 24 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    rows = db.domain_stats_window(cutoff, kid=name)
    by_host: dict[str, dict] = defaultdict(
        lambda: {"requests": 0, "pages": 0, "blocked": 0}
    )
    for r in rows:
        h = by_host[r.host]
        h["requests"] += r.requests
        h["pages"] += r.pages
        h["blocked"] += r.blocked
    return sorted(
        ({"host": host, **counts} for host, counts in by_host.items()),
        key=lambda h: h["requests"],
        reverse=True,
    )[:top_n]


def overview_for_kids(
    kid_names: list[str],
    *,
    window_1h: timedelta = timedelta(hours=1),
    window_24h: timedelta = timedelta(hours=24),
    top_n: int = 5,
    sparkline_buckets: int = 12,
) -> dict[str, dict]:
    """Aggregate counter rows into per-kid summary dicts.

    Returns `{kid_name: summary}` for every name in `kid_names`, including
    kids with zero activity (so the UI can render an empty card).
    """
    now = datetime.utcnow()
    cutoff_24h = now - window_24h
    cutoff_1h = now - window_1h
    bucket_secs = settings.stats_bucket_secs

    rows = db.domain_stats_window(cutoff_24h)
    by_kid: dict[str, list] = defaultdict(list)
    for r in rows:
        by_kid[r.kid].append(r)

    # Sparkline: align to bucket grid ending at `now`. Older first.
    spark_end = _bucket_floor(now, bucket_secs)
    spark_buckets = [
        spark_end - timedelta(seconds=bucket_secs * (sparkline_buckets - 1 - i))
        for i in range(sparkline_buckets)
    ]

    out: dict[str, dict] = {}
    for name in kid_names:
        kid_rows = by_kid.get(name, [])
        req_1h = pages_1h = blocked_1h = 0
        req_24h = pages_24h = blocked_24h = 0
        host_1h: dict[str, dict] = defaultdict(
            lambda: {"requests": 0, "pages": 0, "blocked": 0}
        )
        spark = [0] * sparkline_buckets
        last_seen: datetime | None = None

        for r in kid_rows:
            req_24h += r.requests
            pages_24h += r.pages
            blocked_24h += r.blocked
            if last_seen is None or (r.last_seen and r.last_seen > last_seen):
                last_seen = r.last_seen
            if r.bucket_ts >= cutoff_1h:
                req_1h += r.requests
                pages_1h += r.pages
                blocked_1h += r.blocked
                h = host_1h[r.host]
                h["requests"] += r.requests
                h["pages"] += r.pages
                h["blocked"] += r.blocked
                # Map onto the sparkline grid if it lands in-window.
                for i, b in enumerate(spark_buckets):
                    if r.bucket_ts == b:
                        spark[i] += r.requests
                        break

        top_hosts = sorted(
            (
                {"host": host, **counts}
                for host, counts in host_1h.items()
            ),
            key=lambda h: h["requests"],
            reverse=True,
        )[:top_n]

        out[name] = {
            "kid": name,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "requests_1h": req_1h,
            "pages_1h": pages_1h,
            "blocked_1h": blocked_1h,
            "requests_24h": req_24h,
            "pages_24h": pages_24h,
            "blocked_24h": blocked_24h,
            "top_hosts_1h": top_hosts,
            "sparkline_1h": spark,
            "bucket_secs": bucket_secs,
        }
    return out
