"""Per-kid activity overview, derived from the `event` table.

`aggregates.overview_for_kids` and `top_hosts_24h_for_kid` run pure SQL
GROUP BYs over `event` (no in-memory accumulator). Each row carries a
`hit_count` from the session-window upsert, so `SUM(hit_count)` here
equals the visible activity feed for the same window — the counters
and the log measure the same population.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import aggregates, store

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview")
def overview() -> dict:
    """One summary entry per kid in kids.yaml, even if zero activity.

    Shape:
      {
        "kids": [
          {
            "kid": str,
            "last_seen": iso8601 | null,
            "requests_1h": int, "pages_1h": int, "blocked_1h": int,
            "requests_24h": int, "pages_24h": int, "blocked_24h": int,
            "top_hosts_1h": [{ "host", "requests", "pages", "blocked" }, ...],
            "sparkline_1h": [int, ... 12 buckets, oldest first],
            "bucket_secs": int
          }, ...
        ]
      }
    """
    cfg = store.load()
    names = [k.name for k in cfg.kids]
    summaries = aggregates.overview_for_kids(names)
    return {"kids": [summaries[n] for n in names]}


@router.get("/kid/{name}")
def kid_stats(name: str) -> dict:
    """Richer single-kid view for the kid home page sidebar.

    Same shape as the overview entry plus `top_hosts_24h` and a longer
    top_hosts_1h list — the detail page has room for more rows than the card.
    """
    cfg = store.load()
    if not any(k.name == name for k in cfg.kids):
        raise HTTPException(404, "unknown kid")
    summaries = aggregates.overview_for_kids([name], top_n=20)
    extra = aggregates.top_hosts_24h_for_kid(name, top_n=20)
    out = summaries[name]
    out["top_hosts_24h"] = extra
    return out
