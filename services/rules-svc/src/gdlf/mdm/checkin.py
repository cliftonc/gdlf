"""MDM /mdm/checkin message handlers.

Apple's MDM client posts plist-encoded messages here during enrollment and
lifecycle events. The three we handle:

  * Authenticate — first message after profile install. The device tells us
                   its UDID and asks the server to acknowledge enrollment.
  * TokenUpdate  — sent right after Authenticate and again whenever the
                   APNs push token rotates. This is what lets us wake the
                   device later via api.push.apple.com.
  * CheckOut     — sent if the user removes the MDM profile (when
                   CheckOutWhenRemoved=True in the original enrollment).

The wire format is documented in Apple's "Mobile Device Management Protocol
Reference". Every body is a plist with a `MessageType` key whose value
selects the handler.

Authentication: Caddy validates the client cert chain against the gdlf MDM
CA (verify_if_given) and forwards the cert subject as X-Mdm-Client-Subject.
We extract the CN, look the Device up, and only proceed if a matching
MdmState exists.
"""
from __future__ import annotations

import logging
import plistlib
from datetime import datetime

from .. import store
from ..schema import MdmState
from . import identity

log = logging.getLogger("gdlf.mdm.checkin")


class CheckinError(Exception):
    """Recoverable client-facing error during check-in handling."""
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def handle(body: bytes, subject_header: str | None) -> bytes:
    """Dispatch a check-in plist by MessageType. Returns the response body.

    For Authenticate / TokenUpdate / CheckOut, Apple expects an empty 200
    on success — we return b"" (FastAPI route wraps that as the body).
    Errors raise CheckinError which the route maps to an HTTP status.
    """
    cn = identity.cn_from_subject_header(subject_header)
    if not cn:
        # We accept Authenticate without a cert (per Apple — the device
        # may not yet have applied the identity), but everything else
        # MUST present the device cert.
        pass

    try:
        msg = plistlib.loads(body)
    except Exception as e:
        raise CheckinError(f"malformed plist: {e}") from None

    mtype = msg.get("MessageType")
    udid = msg.get("UDID")
    if not mtype or not udid:
        raise CheckinError("missing MessageType / UDID")

    if mtype == "Authenticate":
        _handle_authenticate(cn=cn, udid=udid, msg=msg)
    elif mtype == "TokenUpdate":
        _handle_token_update(cn=cn, udid=udid, msg=msg)
    elif mtype == "CheckOut":
        _handle_checkout(cn=cn, udid=udid)
    else:
        # GetBootstrapToken / SetBootstrapToken arrive here too; safe to
        # ignore for v1 (those are macOS-specific). Logged so we notice
        # if iOS surprises us.
        log.info("ignored checkin MessageType=%s udid=%s", mtype, udid)

    return b""  # Apple expects empty 200 on success


def _find_device_or_raise(cn: str | None):
    """Look the Device up by identity CN. Raises CheckinError if unknown."""
    if not cn:
        raise CheckinError("no client cert presented", status=401)
    cfg = store.load(force=True)
    found = cfg.device_by_mdm_identity(cn)
    if not found:
        raise CheckinError(f"unknown identity CN {cn!r}", status=404)
    return cfg, found[0], found[1]


def _handle_authenticate(*, cn: str | None, udid: str, msg: dict) -> None:
    """Apple's first check-in. Record UDID + supervised flag on the Device.

    The cert may or may not be presented depending on iOS version timing;
    we accept both. The lookup uses the enrollment token's wg_ip mapping
    when no CN is available — but for now we require CN (the enrollment
    profile installs the PKCS12 before sending Authenticate, in practice)."""
    _, kid, device = _find_device_or_raise(cn)
    topic = msg.get("Topic")
    log.info("MDM Authenticate udid=%s device=%s/%s topic=%s",
             udid, kid.name, device.name, topic)

    def upd(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.mdm and d.mdm.identity_cn == cn:
                    d.mdm = d.mdm.model_copy(update={
                        "udid": udid,
                        "push_cert_topic": topic,
                        # Apple sends OSVersion / BuildVersion / etc — we
                        # don't persist them here; Phase 4 can query
                        # DeviceInformation later if needed.
                    })
                    return

    store.mutate(upd)


def _handle_token_update(*, cn: str | None, udid: str, msg: dict) -> None:
    """Records / refreshes the APNs push credentials we use to wake the
    device for command delivery.

    If this is the FIRST TokenUpdate for the device (i.e. status was not
    yet `enrolled`), we trigger the orchestrator to push the baseline
    policy profile right away. Re-runs of TokenUpdate (token rotation)
    just refresh the credentials.
    """
    _, kid, device = _find_device_or_raise(cn)
    push_token = msg.get("Token")
    push_magic = msg.get("PushMagic")
    if push_token is None or push_magic is None:
        raise CheckinError("TokenUpdate missing Token / PushMagic")

    # Token comes through as bytes (plist Data); store as base64 to round-trip
    # cleanly through YAML.
    import base64
    token_b64 = base64.b64encode(push_token).decode("ascii") if isinstance(push_token, bytes) else str(push_token)

    log.info("MDM TokenUpdate udid=%s device=%s/%s", udid, kid.name, device.name)

    was_already_enrolled = device.mdm.status == "enrolled"

    def upd(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.mdm and d.mdm.identity_cn == cn:
                    d.mdm = d.mdm.model_copy(update={
                        "udid": udid,
                        "push_token": token_b64,
                        "push_magic": push_magic,
                        "status": "enrolled",
                        "enrolled_at": d.mdm.enrolled_at or datetime.utcnow(),
                        "last_checkin_at": datetime.utcnow(),
                    })
                    return

    store.mutate(upd)

    # Deploy the baseline policy on first-time enrollment. Wrapped in a
    # broad except so a profile-build failure (e.g. mitmproxy CA missing)
    # doesn't break the check-in handshake — the admin can re-trigger via
    # POST /api/devices/{ip}/mdm/install-policy.
    if not was_already_enrolled:
        try:
            # Re-read so we pass the fully-populated MdmState (with the
            # push token + magic we just stored) to the orchestrator.
            cfg = store.load(force=True)
            found = cfg.device_by_mdm_identity(cn)
            if found:
                from . import orchestrator   # local import avoids cycle
                kid_now, device_now = found
                orchestrator.deploy_baseline(kid_now, device_now)
        except Exception as e:
            log.warning("baseline deploy on enrollment failed: %s", e)


def _handle_checkout(*, cn: str | None, udid: str) -> None:
    """User removed the MDM profile. Mark the Device as checked-out but keep
    the record (so the dashboard can show the history). Re-enrolling will
    overwrite the state."""
    try:
        _, kid, device = _find_device_or_raise(cn)
    except CheckinError:
        log.info("MDM CheckOut for unknown udid=%s cn=%s — ignoring", udid, cn)
        return

    log.info("MDM CheckOut udid=%s device=%s/%s", udid, kid.name, device.name)

    def upd(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.mdm and d.mdm.identity_cn == cn:
                    d.mdm = d.mdm.model_copy(update={
                        "status": "checked_out",
                        "last_checkin_at": datetime.utcnow(),
                    })
                    return

    store.mutate(upd)
