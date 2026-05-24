"""Mint enrollment tokens + render the QR JSON that Android Setup Wizard
expects when factory-reset → tap-6× → camera-scan.

The QR string Google generates already contains everything Android needs
(the DPC component name, signature checksum, download location, and the
embedded enrollment token). We just pass `qrCode` through verbatim.
"""
from __future__ import annotations

from . import client


# Token TTL — long enough to walk the kid through factory reset + setup,
# but short enough to be useless if it leaks. 1 hour matches the iOS one.
ENROLL_TOKEN_DURATION = "3600s"


def mint(*, policy_name: str, additional_data: str | None = None) -> dict:
    """Create a one-time enrollment token bound to `policy_name`.

    Returns the raw AMAPI EnrollmentToken: `{name, value, qrCode, ...}`.
    The `qrCode` value is a JSON string the device's setup wizard can scan
    directly — no extra wrapping required.
    """
    svc = client.service()
    enterprise = client.load_enterprise()
    body = {
        "policyName": policy_name,
        "duration": ENROLL_TOKEN_DURATION,
        # `additionalData` is round-tripped onto the resulting Device
        # resource — useful for cross-referencing back to our kid/device.
        "additionalData": additional_data or "",
    }
    return (
        svc.enterprises()
        .enrollmentTokens()
        .create(parent=enterprise.name, body=body)
        .execute()
    )


def revoke(token_name: str) -> None:
    """Delete an outstanding (unredeemed) enrollment token."""
    svc = client.service()
    svc.enterprises().enrollmentTokens().delete(name=token_name).execute()
