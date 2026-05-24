"""Cookie-based admin auth.

A single session cookie holds `<issued_at>.<hmac_sha256>`, signed with a key
derived from `RULES_SVC_ADMIN_PASSWORD`. No DB, no per-request DB hit. The
secret is rotated implicitly when the admin password changes — all sessions
invalidate at once.

Routes are protected by `require_auth` middleware in `main.py`; the public
allow-list is defined there. When `settings.admin_password` is empty the
middleware is a no-op so dev / first-boot stays unblocked.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from .settings import settings

COOKIE = "gdlf_auth"
MAX_AGE = 30 * 24 * 3600  # 30 days


def _secret() -> bytes:
    # Domain-separated SHA-256 of the admin password. Empty password gives
    # an unusable but deterministic key; auth is disabled in that case anyway.
    return hashlib.sha256(b"gdlf-session-key|" + settings.admin_password.encode()).digest()


def make_token(ts: int | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    msg = str(ts).encode()
    sig = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def check_token(tok: str | None) -> bool:
    if not tok:
        return False
    try:
        ts_str, sig = tok.split(".", 1)
        ts = int(ts_str)
    except (ValueError, AttributeError):
        return False
    expected = hmac.new(_secret(), ts_str.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return 0 <= (time.time() - ts) < MAX_AGE


def check_password(pw: str) -> bool:
    expected = settings.admin_password
    if not expected:
        return False
    return secrets.compare_digest(pw.encode(), expected.encode())
