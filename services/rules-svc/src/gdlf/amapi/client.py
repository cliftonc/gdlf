"""AMAPI client + state-file paths.

Two files under <state_dir>/amapi/ drive everything:

  * service-account.json — GCP service account key the parent downloads
    once during `./gdlf amapi init`. Authenticates every API call.
  * enterprise.json      — `{name, project_id, signup_url_name}` recorded
    by `./gdlf amapi enterprise complete` after the parent completes the
    EMM signup flow in their browser.

Both files are read on first use and cached for the process lifetime.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..settings import settings

# Single AMAPI scope.
_SCOPE = "https://www.googleapis.com/auth/androidmanagement"

_lock = threading.RLock()
_cached_service: Any | None = None
_cached_enterprise: "EnterpriseConfig | None" = None


def amapi_dir() -> Path:
    d = settings.state_dir / "amapi"
    return d


def service_account_path() -> Path:
    return amapi_dir() / "service-account.json"


def enterprise_path() -> Path:
    return amapi_dir() / "enterprise.json"


def is_configured() -> bool:
    return service_account_path().exists() and enterprise_path().exists()


@dataclass(frozen=True)
class EnterpriseConfig:
    name: str                  # "enterprises/LCxxxxxxxx"
    project_id: str            # GCP project id
    signup_url_name: str | None = None
    display_name: str | None = None


class AmapiNotConfigured(RuntimeError):
    """Raised when the AMAPI state files are missing."""


def load_enterprise() -> EnterpriseConfig:
    global _cached_enterprise
    with _lock:
        if _cached_enterprise is not None:
            return _cached_enterprise
        p = enterprise_path()
        if not p.exists():
            raise AmapiNotConfigured(
                f"AMAPI not configured: missing {p}. "
                "Run `./gdlf amapi enterprise signup` and `./gdlf amapi enterprise complete`."
            )
        data = json.loads(p.read_text())
        _cached_enterprise = EnterpriseConfig(
            name=data["name"],
            project_id=data["project_id"],
            signup_url_name=data.get("signup_url_name"),
            display_name=data.get("display_name"),
        )
        return _cached_enterprise


def save_enterprise(cfg: EnterpriseConfig) -> None:
    global _cached_enterprise
    with _lock:
        amapi_dir().mkdir(parents=True, exist_ok=True)
        enterprise_path().write_text(json.dumps({
            "name": cfg.name,
            "project_id": cfg.project_id,
            "signup_url_name": cfg.signup_url_name,
            "display_name": cfg.display_name,
        }, indent=2))
        _cached_enterprise = cfg


def project_id() -> str:
    """The GCP project id, derived from the service account JSON if no
    enterprise.json exists yet (needed during signup, before we have one)."""
    if enterprise_path().exists():
        return load_enterprise().project_id
    sa = _load_service_account_json()
    return sa["project_id"]


def _load_service_account_json() -> dict:
    p = service_account_path()
    if not p.exists():
        raise AmapiNotConfigured(
            f"AMAPI not configured: missing {p}. "
            "Run `./gdlf amapi init` and place the GCP service-account JSON there."
        )
    return json.loads(p.read_text())


def service():
    """Return the cached googleapiclient `androidmanagement` v1 service.

    Built lazily so importing this module doesn't crash when AMAPI isn't
    configured yet (the rest of rules-svc must still boot in that case).
    """
    global _cached_service
    with _lock:
        if _cached_service is not None:
            return _cached_service
        # Defer google imports to first use so they don't slow boot.
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            str(service_account_path()),
            scopes=[_SCOPE],
        )
        # cache_discovery=False — discovery doc cache writes to /tmp and
        # spams warnings inside a container; we don't need the on-disk cache.
        _cached_service = build(
            "androidmanagement",
            "v1",
            credentials=creds,
            cache_discovery=False,
        )
        return _cached_service


def reset_cache() -> None:
    """Test/CLI helper: drop the cached service + enterprise. Next call
    rebuilds from the on-disk files."""
    global _cached_service, _cached_enterprise
    with _lock:
        _cached_service = None
        _cached_enterprise = None
