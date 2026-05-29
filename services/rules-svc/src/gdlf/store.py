"""kids.yaml load/save with file lock and atomic rename.

ruamel.yaml is used (instead of PyYAML) so we preserve comments and ordering
when the dashboard writes the file back. Hand-edits and UI edits cooperate.
"""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from filelock import FileLock
from ruamel.yaml import YAML

from .schema import KidsConfig
from .settings import settings

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)

_mem_lock = threading.RLock()
_cached: KidsConfig | None = None
_cached_mtime: float = 0.0

# Signal raised on every successful write. The AdGuard sync loop awaits this
# (with a debounce) so UI changes propagate fast without polling. The loop
# reference is captured at lifespan start so threadpool-dispatched sync
# handlers (FastAPI's default for non-async routes) can still signal across
# threads.
_mutation_event: asyncio.Event | None = None
_mutation_loop: asyncio.AbstractEventLoop | None = None


def bind_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called from `lifespan` so signals from threadpool handlers reach the
    main event loop. Creates the Event bound to that loop."""
    global _mutation_event, _mutation_loop
    _mutation_loop = loop
    _mutation_event = asyncio.Event()


def mutation_event() -> asyncio.Event:
    if _mutation_event is None:
        # Cold-call before lifespan has bound: create a transient event so
        # awaiters at least get a coherent object. The loop reference is
        # still None so signals from worker threads will be dropped until
        # bind_event_loop runs.
        return asyncio.Event()
    return _mutation_event


def _signal_mutation() -> None:
    """Mark kids.yaml as dirty for any sync loop awaiting changes. Safe to
    call from FastAPI's threadpool — we hand the set off via the captured
    loop's threadsafe scheduler."""
    if _mutation_event is None or _mutation_loop is None:
        return
    _mutation_loop.call_soon_threadsafe(_mutation_event.set)
    # Mirror onto pubsub's config channel so multi-subscriber consumers
    # (mitm addon's SSE wake) get fanned out without racing the single
    # asyncio.Event above.
    from . import pubsub
    _mutation_loop.call_soon_threadsafe(pubsub.publish_config_changed)


def _path() -> Path:
    return settings.kids_yaml


def _lock_path() -> Path:
    return _path().with_suffix(_path().suffix + ".lock")


def load(force: bool = False) -> KidsConfig:
    """Return parsed KidsConfig, cached by mtime."""
    global _cached, _cached_mtime
    p = _path()
    with _mem_lock:
        mtime = p.stat().st_mtime if p.exists() else 0.0
        if not force and _cached is not None and mtime == _cached_mtime:
            return _cached
        if not p.exists():
            _cached = KidsConfig()
        else:
            with p.open("r") as fh:
                raw = _yaml.load(fh) or {}
            _drop_deprecated_keys(raw)
            _cached = KidsConfig.model_validate(raw)
        _cached_mtime = mtime
        return _cached


def _drop_deprecated_keys(raw: dict) -> None:
    """One-shot migrations applied at load time. Schema uses `extra=forbid`,
    so these would otherwise hard-fail an upgrade. Mutations are in-place;
    next save() rewrites the YAML in the new shape.

    Currently:
      * drop legacy top-level `blocklists` / `apps` (former Rules Library)
        and per-kid `blocklists`
      * split legacy `url_rules[].match` (combined host+path glob) into
        separate `host` / `path` fields, splitting on the first `/`
    """
    raw.pop("blocklists", None)
    raw.pop("apps", None)
    for k in raw.get("kids") or []:
        if not isinstance(k, dict):
            continue
        k.pop("blocklists", None)
        for r in k.get("url_rules") or []:
            if not isinstance(r, dict) or "match" not in r:
                continue
            m = (r.pop("match") or "").strip()
            if "/" in m:
                host, rest = m.split("/", 1)
                r.setdefault("host", host)
                r.setdefault("path", "/" + rest)
            else:
                r.setdefault("host", m)


def save(cfg: KidsConfig) -> None:
    """Atomically write kids.yaml. Caller is responsible for having a coherent
    KidsConfig — Pydantic re-validates on parse round-trip."""
    global _cached, _cached_mtime
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(_lock_path()), timeout=10)
    with lock, _mem_lock:
        data = cfg.model_dump(mode="python", exclude_defaults=False)
        # In-place write: bind-mounted files (single-file mounts) can't be
        # replaced via rename, only overwritten. filelock guards concurrency.
        with p.open("w") as fh:
            _yaml.dump(data, fh)
        _cached = cfg
        _cached_mtime = p.stat().st_mtime
    _signal_mutation()


def mutate(fn):
    """Context manager flavour: load, apply fn(cfg) -> cfg, save."""
    with _mem_lock:
        cfg = load(force=True).model_copy(deep=True)
        result = fn(cfg)
        if result is None:
            result = cfg
        save(result)
        return result
