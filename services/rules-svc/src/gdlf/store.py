"""kids.yaml load/save with file lock and atomic rename.

ruamel.yaml is used (instead of PyYAML) so we preserve comments and ordering
when the dashboard writes the file back. Hand-edits and UI edits cooperate.
"""
from __future__ import annotations

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
            _cached = KidsConfig.model_validate(raw)
        _cached_mtime = mtime
        return _cached


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


def mutate(fn):
    """Context manager flavour: load, apply fn(cfg) -> cfg, save."""
    with _mem_lock:
        cfg = load(force=True).model_copy(deep=True)
        result = fn(cfg)
        if result is None:
            result = cfg
        save(result)
        return result
