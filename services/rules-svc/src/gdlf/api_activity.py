"""Activity feed JSON + SSE stream.

The SSE stream emits `{"kind":"changed","kid":<name>}` pings as
`pubsub.publish()` fires from the `/api/events` writer. The SPA
debounces and refetches `/api/activity` (and the stats endpoints) on
each ping. This keeps the live feed, the paged list, and the counter
tiles byte-for-byte in sync — they all read from the same `event`
table.

Polling fallback: `/api/activity` is a normal GET and the SPA also
polls every 5s if the SSE connection drops.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from . import db, pubsub
from .dto import event_dto

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("")
def list_activity(
    kid: str | None = None,
    decision: str | None = None,
    limit: int = 50,
) -> dict:
    """Most-recently-touched events first.

    Returns dedup'd rows: a single (kid, host, path, query, decision)
    inside one session window appears once with `hit_count` showing how
    many hits it absorbed. Both newly inserted and bumped rows surface
    here on each refetch.
    """
    events = db.recent_events(limit=limit, kid=kid, decision=decision)
    return {"events": [event_dto(e) for e in events]}


@router.get("/stream")
async def activity_stream(request: Request) -> EventSourceResponse:
    """Server-Sent Events: emits a `changed` ping per ingested event.

    Payload is a tiny `{"kind":"changed","kid":"<name>"}`. The SPA
    invalidates its activity + stats query caches on each ping. Coarse
    on purpose — the cost of refetching a 50-row list is negligible at
    this scale and the alternative (push individual rows into the
    cache) is what caused the SSE/paged-cache drift the user reported.
    """
    async def gen():
        async for ev in pubsub.subscribe():
            if await request.is_disconnected():
                break
            yield {"event": "activity", "data": json.dumps(ev)}

    return EventSourceResponse(gen(), ping=15)
