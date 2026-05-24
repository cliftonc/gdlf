"""Tiny in-process pub/sub for SSE fan-out.

The /api/events handler calls `publish(event_dto)` after persisting; every
connected SSE subscriber receives the event on its queue. Queues are bounded
— if a slow subscriber backs up we drop events for that subscriber rather
than block the writer.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

_subscribers: set[asyncio.Queue] = set()
_MAX_DEPTH = 100


def publish(event: dict) -> None:
    """Fan an event out to every subscriber, non-blocking. Drop on overflow."""
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


@asynccontextmanager
async def _subscription() -> AsyncIterator[asyncio.Queue]:
    q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_DEPTH)
    _subscribers.add(q)
    try:
        yield q
    finally:
        _subscribers.discard(q)


async def subscribe() -> AsyncIterator[dict]:
    """Yield events as they arrive until the consumer cancels."""
    async with _subscription() as q:
        while True:
            yield await q.get()
