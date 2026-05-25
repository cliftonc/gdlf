"""Activity-ingest behaviour.

Pins the invariants the user asked for after simplifying:
  * SNI splices dedup within a session window — one row per (kid, host)
    per window, with hit_count counting the suppressed repeats.
  * MITM page navigations dedup by URL (path+query) within the window.
  * Assets / XHRs / WebSockets are dropped unless the decision is in the
    always-keep set (blocks, flags, TLS failures, DNS blocks).
  * `kind=unknown` is treated as a page navigation (deliberate — the
    classifier is unreliable, and over-logging is the right failure
    mode).
  * Counter helpers (`overview_for_kids`, `top_hosts_24h_for_kid`) are
    consistent with `SUM(hit_count)` over the same rows the activity
    feed returns. This is the cross-cutting invariant the simplification
    was supposed to deliver.
  * The SSE channel emits a single `changed` ping per accepted event.
  * Identical POST retries (network flake) are idempotent.
  * Prune uses `ts_last`, so long-running sessions aren't aged out
    mid-bump.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

# Helper to build a minimal event payload the addon would send.
def _payload(**overrides):
    base = {
        "client_ip": "10.13.13.10",
        "method": "GET",
        "host": "youtube.com",
        "path": "/",
        "query": None,
        "decision": "allow",
        "rule": None,
        "flag": False,
        "sni_only": False,
        "status": 200,
        "kind": "page",
    }
    base.update(overrides)
    return base


def _events_table(state_dir):
    """Read all rows directly via SQLite to assert without going through
    the API. Returns a list of dicts."""
    import sqlite3

    db_path = state_dir / "gdlf.db"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, kid, host, path, query, decision, kind, hit_count, "
            "ts, ts_last, bucket_ts, registrable FROM event"
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# SNI session-window dedup


def test_sni_first_seen_inserts(client, gdlf_env):
    r = client.post("/api/events", json=_payload(
        host="reddit.com", path=None, query=None,
        decision="passthrough", sni_only=True, kind="sni",
    ))
    assert r.status_code == 200
    assert r.json()["stored"] is True
    assert r.json()["new"] is True
    rows = _events_table(gdlf_env["state_dir"])
    assert len(rows) == 1
    assert rows[0]["hit_count"] == 1
    assert rows[0]["kind"] == "sni"
    assert rows[0]["kid"] == "alice"
    assert rows[0]["registrable"] == "reddit.com"


def test_sni_within_window_bumps_hit_count(client, gdlf_env):
    body = _payload(
        host="reddit.com", path=None, query=None,
        decision="passthrough", sni_only=True, kind="sni",
    )
    client.post("/api/events", json=body)
    r2 = client.post("/api/events", json=body)
    r3 = client.post("/api/events", json=body)
    assert r2.json()["new"] is False
    assert r3.json()["new"] is False

    rows = _events_table(gdlf_env["state_dir"])
    assert len(rows) == 1, "same-bucket repeats must collapse"
    assert rows[0]["hit_count"] == 3


def test_sni_new_bucket_inserts_new_row(client, gdlf_env):
    """Walking the bucket forward should split rows."""
    from gdlf import db

    body = _payload(
        host="reddit.com", path=None, query=None,
        decision="passthrough", sni_only=True, kind="sni",
    )
    # First emit in current bucket.
    client.post("/api/events", json=body)
    # Manually rewrite the row's bucket_ts back by 2 windows so the next
    # post lands in a new bucket — avoids waiting wallclock minutes.
    bucket = db.bucket_floor(datetime.utcnow())
    old = bucket - timedelta(seconds=600)
    with db.session() as s:
        s.connection().exec_driver_sql(
            f"UPDATE event SET bucket_ts = '{old.isoformat()}'"
        )
        s.commit()
    # Now post again — different bucket_ts → new row.
    client.post("/api/events", json=body)

    rows = _events_table(gdlf_env["state_dir"])
    assert len(rows) == 2
    assert all(r["hit_count"] == 1 for r in rows)


# ---------------------------------------------------------------------------
# MITM page-path dedup


def test_page_path_dedup(client, gdlf_env):
    """Two hits to the same URL collapse; different paths stay separate."""
    p1 = _payload(host="youtube.com", path="/watch", query="v=abc", kind="page")
    p2 = _payload(host="youtube.com", path="/watch", query="v=abc", kind="page")
    p3 = _payload(host="youtube.com", path="/watch", query="v=xyz", kind="page")

    client.post("/api/events", json=p1)
    client.post("/api/events", json=p2)
    client.post("/api/events", json=p3)

    rows = sorted(
        _events_table(gdlf_env["state_dir"]),
        key=lambda r: r["query"] or "",
    )
    assert [r["query"] for r in rows] == ["v=abc", "v=xyz"]
    assert [r["hit_count"] for r in rows] == [2, 1]


# ---------------------------------------------------------------------------
# Filtering


@pytest.mark.parametrize("kind", ["asset", "xhr", "ws"])
def test_asset_xhr_ws_dropped(kind, client, gdlf_env):
    r = client.post("/api/events", json=_payload(
        host="youtube.com", path="/assets/x.js", kind=kind, decision="allow",
    ))
    assert r.status_code == 200
    assert r.json()["stored"] is False
    assert _events_table(gdlf_env["state_dir"]) == []


def test_block_always_recorded_even_when_asset(client, gdlf_env):
    """A blocked tracker pixel is still recorded — blocks override the
    drop list. Without this, parents wouldn't see asset-level blocks at
    all."""
    r = client.post("/api/events", json=_payload(
        host="tracker.example.com", path="/pixel.gif",
        kind="asset", decision="block", rule="ads",
    ))
    assert r.json()["stored"] is True
    rows = _events_table(gdlf_env["state_dir"])
    assert len(rows) == 1
    assert rows[0]["decision"] == "block"
    assert rows[0]["kind"] == "asset"


def test_unknown_kind_kept(client, gdlf_env):
    """`unknown` is treated as a page navigation — the classifier is
    unreliable and we'd rather over-log."""
    r = client.post("/api/events", json=_payload(
        host="example.com", path="/login", kind="unknown",
    ))
    assert r.json()["stored"] is True
    rows = _events_table(gdlf_env["state_dir"])
    assert len(rows) == 1
    assert rows[0]["kind"] == "unknown"


def test_tls_failed_upsert(client, gdlf_env):
    """Pinned-cert app retrying 50 times in a bucket → one row with
    hit_count=50."""
    body = _payload(
        host="examplebank.com", path=None, query=None,
        decision="tls_failed", sni_only=True, kind="pinned",
        note="alert unknown ca",
    )
    for _ in range(5):
        client.post("/api/events", json=body)

    rows = _events_table(gdlf_env["state_dir"])
    assert len(rows) == 1
    assert rows[0]["hit_count"] == 5
    assert rows[0]["decision"] == "tls_failed"


def test_post_retry_idempotent(client, gdlf_env):
    """Two identical posts (e.g. addon retried on a flake) → one row.

    The dedup window collapses them even if they arrive at the same
    millisecond — the unique-index conflict resolves to UPDATE, not a
    duplicate row.
    """
    body = _payload(host="reddit.com", path=None, decision="passthrough",
                    sni_only=True, kind="sni")
    client.post("/api/events", json=body)
    client.post("/api/events", json=body)

    rows = _events_table(gdlf_env["state_dir"])
    assert len(rows) == 1
    assert rows[0]["hit_count"] == 2


def test_unknown_client_ip_kept_with_null_kid(client, gdlf_env):
    """An event from an IP not in kids.yaml still records — kid=NULL is
    visible in the activity log as 'unknown', which is better than
    silently dropping."""
    r = client.post("/api/events", json=_payload(
        client_ip="10.99.99.99", host="example.com", path="/",
    ))
    assert r.json()["stored"] is True
    rows = _events_table(gdlf_env["state_dir"])
    assert len(rows) == 1
    assert rows[0]["kid"] is None


# ---------------------------------------------------------------------------
# Counters consistency


def test_counters_match_events(client, gdlf_env):
    """Per-kid counters (top_hosts_24h_for_kid, overview_for_kids) must
    equal SUM(hit_count) over the rows the activity feed returns.

    This is the cross-cutting invariant the simplification was supposed
    to deliver: counters and the log measure the same population, so
    the user can trust the numbers match what they see browsed.
    """
    from gdlf import aggregates

    # alice browses three pages on youtube.com (same URL twice -> dedup),
    # one passthrough SNI to reddit.com, one blocked tracker pixel.
    events = [
        _payload(host="youtube.com", path="/watch", query="v=a", kind="page"),
        _payload(host="youtube.com", path="/watch", query="v=a", kind="page"),
        _payload(host="youtube.com", path="/watch", query="v=b", kind="page"),
        _payload(host="reddit.com", path=None, query=None,
                 decision="passthrough", sni_only=True, kind="sni"),
        _payload(host="tracker.example.com", path="/pixel.gif",
                 kind="asset", decision="block", rule="ads"),
    ]
    for ev in events:
        client.post("/api/events", json=ev)

    # bob shouldn't appear in alice's totals.
    client.post("/api/events", json=_payload(
        client_ip="10.13.13.20", host="example.com", path="/", kind="page",
    ))

    rows = _events_table(gdlf_env["state_dir"])
    alice_rows = [r for r in rows if r["kid"] == "alice"]
    alice_hits = sum(r["hit_count"] for r in alice_rows)
    alice_blocks = sum(
        r["hit_count"] for r in alice_rows if r["decision"] in ("block", "flag")
    )

    overview = aggregates.overview_for_kids(["alice", "bob"])
    assert overview["alice"]["requests_24h"] == alice_hits
    assert overview["alice"]["blocked_24h"] == alice_blocks
    assert overview["bob"]["requests_24h"] == 1

    # Top-hosts should sum to the same total for alice.
    top = aggregates.top_hosts_24h_for_kid("alice")
    assert sum(h["requests"] for h in top) == alice_hits
    # youtube.com leads (3 hits across 2 rows: 2 + 1).
    by_host = {h["host"]: h for h in top}
    assert by_host["youtube.com"]["requests"] == 3


def test_top_hosts_uses_registrable(client, gdlf_env):
    """Subdomains collapse into eTLD+1 in the counter view."""
    from gdlf import aggregates

    client.post("/api/events", json=_payload(
        host="www.youtube.com", path="/", kind="page",
    ))
    client.post("/api/events", json=_payload(
        host="m.youtube.com", path="/", kind="page",
    ))

    top = aggregates.top_hosts_24h_for_kid("alice")
    assert len(top) == 1
    assert top[0]["host"] == "youtube.com"
    assert top[0]["requests"] == 2


# ---------------------------------------------------------------------------
# SSE


def test_ingest_publishes_change_ping(client, gdlf_env, monkeypatch):
    """A successful POST publishes a single `changed` ping carrying the
    resolved kid. The SPA debounces these and refetches the activity
    list + stats so everything stays in sync."""
    from gdlf import pubsub

    published: list[dict] = []
    monkeypatch.setattr(pubsub, "publish", lambda ev: published.append(ev))

    client.post("/api/events", json=_payload(
        host="youtube.com", path="/", kind="page",
    ))

    assert published == [{"kind": "changed", "kid": "alice"}]


def test_ingest_dropped_event_does_not_publish(client, gdlf_env, monkeypatch):
    """Dropped events (asset/xhr/ws with no actionable decision) must NOT
    publish — otherwise the SPA wakes up to refetch and finds the feed
    unchanged, defeating the dedup."""
    from gdlf import pubsub

    published: list[dict] = []
    monkeypatch.setattr(pubsub, "publish", lambda ev: published.append(ev))

    client.post("/api/events", json=_payload(
        host="youtube.com", path="/banner.png", kind="asset",
    ))

    assert published == []


def test_pubsub_delivers_ping_to_subscriber():
    """Smoke test for pubsub itself — kept here so the SSE invariant
    (publish → subscriber receives) isn't only validated by the cross-
    process HTTP path, which is fragile to test."""
    import asyncio
    from gdlf import pubsub

    received: list[dict] = []

    async def _run():
        async def consumer():
            async for msg in pubsub.subscribe():
                received.append(msg)
                return

        t = asyncio.create_task(consumer())
        # Let the consumer register on `_subscribers` before we publish.
        await asyncio.sleep(0)
        pubsub.publish({"kind": "changed", "kid": "alice"})
        await asyncio.wait_for(t, timeout=1.0)

    asyncio.run(_run())
    assert received == [{"kind": "changed", "kid": "alice"}]


# ---------------------------------------------------------------------------
# Prune


def test_prune_uses_ts_last(client, gdlf_env):
    """A long-running session row (old `ts`, fresh `ts_last`) survives
    retention pruning; an old `ts_last` is pruned."""
    from gdlf import db

    # Long session: first hit ages ago, last hit just now.
    body = _payload(host="youtube.com", path="/watch", query="v=z", kind="page")
    client.post("/api/events", json=body)
    long_ago = (datetime.utcnow() - timedelta(days=10)).isoformat()
    with db.session() as s:
        s.connection().exec_driver_sql(
            f"UPDATE event SET ts = '{long_ago}' "
            "WHERE query = 'v=z'"
        )
        s.commit()

    # Cold row: both ts and ts_last older than retention.
    cold = _payload(host="oldsite.com", path="/", kind="page")
    client.post("/api/events", json=cold)
    with db.session() as s:
        s.connection().exec_driver_sql(
            f"UPDATE event SET ts = '{long_ago}', ts_last = '{long_ago}' "
            "WHERE host = 'oldsite.com'"
        )
        s.commit()

    res = db.prune(retention_days=7, max_events=10_000)
    assert res["age_deleted"] == 1

    rows = _events_table(gdlf_env["state_dir"])
    hosts = {r["host"] for r in rows}
    assert "youtube.com" in hosts
    assert "oldsite.com" not in hosts


def test_prune_legacy_kwargs_swallowed(gdlf_env):
    """Older callers passed `stats_retention_days`; the signature now
    accepts and ignores it so an out-of-date caller doesn't crash on
    upgrade."""
    from gdlf import db

    res = db.prune(retention_days=7, max_events=10_000, stats_retention_days=14)
    assert "age_deleted" in res
