"""APNs MDM Push Certificate helpers.

Two responsibilities:
  * Surface the Topic string (UID in the push cert's subject DN) so the
    enrollment profile + every push can carry it verbatim.
  * Send MDM wake-up pushes to api.push.apple.com via HTTP/2 with mTLS
    using the APNs cert + key pair.

When this module sends a push, it's not delivering payload to the device —
it's just nudging Apple to tell the device "your MDM server has something
for you". The device then connects back to /mdm/server to pull pending
commands.
"""
from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.x509.oid import NameOID

from ..settings import settings

log = logging.getLogger("gdlf.mdm.apns")

# Production APNs gateway. The "development" gateway at
# api.development.push.apple.com is for apps in Xcode debug builds; MDM
# always uses production.
APNS_HOST = "api.push.apple.com"


def _push_cert_path() -> Path:
    return settings.state_dir / "apns" / "push.pem"


def _push_key_path() -> Path:
    return settings.state_dir / "apns" / "push.key"


@lru_cache(maxsize=1)
def push_cert_topic() -> str:
    """Return the MDM Topic string (com.apple.mgmt.External.<uuid>).

    Apple embeds it as the UID component of the push cert's subject DN;
    we need it verbatim for the enrollment profile's Topic field and for
    every APNs push later.
    """
    cert = x509.load_pem_x509_certificate(_push_cert_path().read_bytes())
    uid_attrs = cert.subject.get_attributes_for_oid(NameOID.USER_ID)
    if not uid_attrs:
        raise RuntimeError(
            f"APNs cert at {_push_cert_path()} has no UID — wrong cert?"
        )
    return uid_attrs[0].value


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    """Long-lived HTTP/2 client for APNs. Cached so we reuse the TLS+H2
    connection across pushes — APNs strongly prefers this."""
    cert = (str(_push_cert_path()), str(_push_key_path()))
    return httpx.Client(http2=True, cert=cert, timeout=10.0)


class ApnsError(Exception):
    """Push failed. `.status` is the HTTP status; `.reason` is APNs's
    `reason` field when present (e.g. BadDeviceToken, Unregistered)."""
    def __init__(self, status: int, reason: str | None = None) -> None:
        super().__init__(f"APNs {status} {reason or ''}".strip())
        self.status = status
        self.reason = reason


def send_push(*, push_token_b64: str, push_magic: str) -> None:
    """Send an MDM wake-up push to one device.

    `push_token_b64` is the base64-encoded token from TokenUpdate (we stored
    it that way in MdmState). APNs's URL wants it as hex — we decode +
    re-encode.

    `push_magic` is the per-enrollment opaque string from TokenUpdate; it
    goes in the body so the device knows the push really came from us
    (not just any APNs sender with our topic).

    Raises ApnsError on non-200 response. Unregistered tokens (410) and
    BadDeviceToken (400) mean the device's MDM enrollment is gone — the
    caller should clear the MdmState in that case.
    """
    token_hex = base64.b64decode(push_token_b64).hex()
    url = f"https://{APNS_HOST}/3/device/{token_hex}"
    headers = {
        "apns-topic": push_cert_topic(),
        "apns-push-type": "mdm",
        "apns-priority": "10",      # immediate
        "apns-expiration": "0",     # don't store; if undeliverable, drop
    }
    body = json.dumps({"mdm": push_magic}).encode()

    try:
        resp = _client().post(url, headers=headers, content=body)
    except httpx.HTTPError as e:
        raise ApnsError(0, str(e)) from None

    if resp.status_code != 200:
        reason = None
        try:
            reason = resp.json().get("reason")
        except Exception:
            pass
        log.warning("APNs push failed: status=%s reason=%s body=%s",
                    resp.status_code, reason, resp.text[:200])
        raise ApnsError(resp.status_code, reason)

    log.info("APNs push ok: token=%s... topic=%s",
             token_hex[:16], push_cert_topic())
