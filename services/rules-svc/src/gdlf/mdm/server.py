"""POST /mdm/server handler — Apple's MDM command channel.

After an APNs wake-up, the device opens a TLS connection to /mdm/server
(via Caddy, mTLS-verified against the gdlf MDM CA) and POSTs a plist with:

  * Status (Idle / Acknowledged / Error / NotNow / CommandFormatError)
  * UDID
  * CommandUUID (when reporting back on a previously-sent command)
  * any command-specific result fields (e.g. QueryResponses for
    DeviceInformation, InstalledApplicationList for app inventory)

We persist the response (if any), then look for the next pending command
for this device and return it as a plist body. If the queue is empty,
return 200 with no body — Apple treats that as "nothing for me, go back
to sleep".
"""
from __future__ import annotations

import logging
import plistlib
from datetime import datetime

from .. import store
from . import commands, identity

log = logging.getLogger("gdlf.mdm.server")


class ServerError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def handle(body: bytes, subject_header: str | None) -> bytes:
    """Process a /mdm/server POST. Returns the body to send back."""
    cn = identity.cn_from_subject_header(subject_header)
    if not cn:
        raise ServerError("no client cert presented", status=401)

    cfg = store.load(force=True)
    found = cfg.device_by_mdm_identity(cn)
    if not found:
        raise ServerError(f"unknown identity CN {cn!r}", status=404)
    kid, device = found

    # Body may be empty on the device's very first poll after enrollment.
    response = None
    if body:
        try:
            response = plistlib.loads(body)
        except Exception as e:
            raise ServerError(f"malformed plist: {e}") from None

    if response:
        status = response.get("Status")
        log.info("mdm/server %s/%s Status=%s CommandUUID=%s",
                 kid.name, device.name, status, response.get("CommandUUID"))
        commands.record_response(identity_cn=cn, response=response)

    # Update last_checkin_at on every poll — useful for the dashboard.
    def touch(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.mdm and d.mdm.identity_cn == cn:
                    d.mdm = d.mdm.model_copy(update={
                        "last_checkin_at": datetime.utcnow(),
                    })
                    return
    store.mutate(touch)

    next_command = commands.next_for_device(cn)
    return next_command or b""
