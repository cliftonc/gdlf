"""Activity feed JSON + SSE stream.

The SSE stream piggybacks on `pubsub.publish()` which is invoked from the
existing /api/events writer (mitmproxy addon). Polling still works against
GET /api/activity for clients that prefer it or fall back from a closed ES.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from . import db, pubsub
from .dto import event_dto

router = APIRouter(prefix="/api/activity", tags=["activity"])


def _filtered_events(kid, decision, include_sni, include_assets, limit=50):
    """Mirror the legacy /activity logic: over-fetch then filter so the
    visible 50 rows reflect 50 real page navigations rather than mixed noise."""
    raw_limit = limit if (include_sni and include_assets) else max(1000, limit * 20)
    events = db.recent_events(limit=raw_limit, kid=kid, decision=decision)
    if not include_sni:
        events = [e for e in events if e.decision != "sni_only"]
    if not include_assets:
        events = [e for e in events if (e.kind or "page") == "page"]
    return events[:limit]


@router.get("")
def list_activity(
    kid: str | None = None,
    decision: str | None = None,
    sni: bool = False,
    assets: bool = False,
    limit: int = 50,
) -> dict:
    events = _filtered_events(kid, decision, sni, assets, limit=limit)
    return {"events": [event_dto(e) for e in events]}


@router.get("/stream")
async def activity_stream(request: Request) -> EventSourceResponse:
    """Server-Sent Events: emits one `data:` per event as `publish()` fires.

    The keepalive ping keeps NAT and proxy timeouts at bay."""
    async def gen():
        async for ev in pubsub.subscribe():
            if await request.is_disconnected():
                break
            yield {"event": "activity", "data": json.dumps(ev)}

    return EventSourceResponse(gen(), ping=15)
