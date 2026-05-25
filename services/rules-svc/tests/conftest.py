"""Shared pytest fixtures.

The rules-svc state directory + kids.yaml are pointed at a tmp_path for
each test. The DB engine is reset between tests so each starts with a
fresh SQLite file and applies the live `_migrate()` against it.

Frozen-dataclass settings: `gdlf.settings.settings` is a frozen
dataclass, so we mutate it via `object.__setattr__`. This is the
intended escape hatch for tests — the freezing is only there to keep
production code honest about not mutating config at runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `src/gdlf/...` importable without an editable install.
SVC_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SVC_ROOT / "src"))

_EXAMPLE_KIDS_YAML = """
kids:
  - name: alice
    age: 10
    devices:
      - name: phone
        platform: android
        wg_ip: 10.13.13.10
        wg_public_key: aaaa
      - name: laptop
        platform: chromeos
        wg_ip: 10.13.13.11
        wg_public_key: bbbb
    schedule:
      weekday: { allowed: "07:00-21:00" }
      weekend: { allowed: "08:00-22:00" }
    blocked_apps: []
    url_rules: []
    keyword_flags: []
  - name: bob
    age: 13
    devices:
      - name: phone
        platform: ios
        wg_ip: 10.13.13.20
        wg_public_key: cccc
    schedule:
      weekday: { allowed: "07:00-21:00" }
      weekend: { allowed: "08:00-22:00" }
    blocked_apps: []
    url_rules: []
    keyword_flags: []
""".lstrip()


@pytest.fixture()
def gdlf_env(tmp_path, monkeypatch):
    """Per-test sandbox: tmp state dir, tmp kids.yaml, fresh DB engine,
    plus stubs for lifespan steps that would touch real-host resources
    (/etc/wireguard, AdGuard sync, AMAPI poll). Any test that spins up a
    TestClient should depend on this fixture so the lifespan runs cleanly.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    kids_yaml = tmp_path / "kids.yaml"
    kids_yaml.write_text(_EXAMPLE_KIDS_YAML)

    from gdlf import settings as settings_mod
    from gdlf import db, store

    # Frozen dataclass — bypass the freeze for tests.
    object.__setattr__(settings_mod.settings, "state_dir", state_dir)
    object.__setattr__(settings_mod.settings, "kids_yaml", kids_yaml)
    object.__setattr__(settings_mod.settings, "admin_password", "")  # disable auth
    # Smaller bucket so tests can cross a bucket boundary cheaply.
    object.__setattr__(settings_mod.settings, "stats_bucket_secs", 60)

    # Reset module-level singletons that cached state from a prior test.
    db._engine = None
    store._cached = None
    store._cached_mtime = 0.0

    # Stub the lifespan side effects.
    import asyncio as _asyncio
    from gdlf import main, wg, adguard, api_shortlinks
    from gdlf.amapi import orchestrator as amapi_orchestrator

    monkeypatch.setattr(wg, "ensure_server_keys", lambda: None)
    monkeypatch.setattr(wg, "write_wg0_conf", lambda *_a, **_k: None)
    monkeypatch.setattr(
        api_shortlinks, "ensure_shortlinks_for_all_devices", lambda: 0
    )

    async def _noop_loop():
        try:
            while True:
                await _asyncio.sleep(3600)
        except _asyncio.CancelledError:
            raise

    monkeypatch.setattr(adguard, "sync_loop", _noop_loop)
    monkeypatch.setattr(amapi_orchestrator, "status_sync_loop", _noop_loop)
    monkeypatch.setattr(main, "_prune_loop", _noop_loop)
    monkeypatch.setattr(main, "_amapi_policy_watch_loop", _noop_loop)

    yield {
        "tmp_path": tmp_path,
        "state_dir": state_dir,
        "kids_yaml": kids_yaml,
    }

    # Tear down: drop the engine so the next test gets a clean one.
    db._engine = None


@pytest.fixture()
def client(gdlf_env):
    """FastAPI TestClient wired to the sandbox."""
    from fastapi.testclient import TestClient
    from gdlf import main

    with TestClient(main.app) as c:
        yield c
