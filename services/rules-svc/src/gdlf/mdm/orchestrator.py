"""Bridges policy state (kids.yaml + wg keys + mitmproxy CA) to MDM commands.

For v1 there's no continuous watcher — most policy changes are enforced at
the network layer (nftables) and don't need MDM republishing. The two
triggers we DO care about:

  1. Initial enrollment completion: as soon as TokenUpdate flips a device
     to status=enrolled, push the baseline policy (VPN + CA + restrictions)
     so the kid's iPhone honours the rules immediately.

  2. Manual re-sync: when a parent rotates WG keys or wants to re-push
     restrictions, hit POST /api/devices/{ip}/mdm/install-policy.

Both call into `deploy_baseline()`. The baseline is the same profile in
both cases; iOS will simply replace whatever it already had under the same
PayloadIdentifier.
"""
from __future__ import annotations

import logging

from ..schema import Device, Kid
from . import apns, commands, profiles

log = logging.getLogger("gdlf.mdm.orchestrator")


def deploy_baseline(kid: Kid, device: Device) -> dict:
    """Build the baseline policy profile for this device, queue an
    InstallProfile command, and fire an APNs wake-up so the device pulls
    it now. Returns the command UUID + any push error.

    Caller is responsible for confirming device.mdm.status == "enrolled"
    and that push credentials are present. Errors raise; caller decides
    how to surface them (admin endpoint → HTTPException; checkin auto-
    push → log + swallow).
    """
    if not device.mdm or not device.mdm.identity_cn:
        raise RuntimeError(f"device {device.wg_ip} has no MDM identity")

    profile_bytes = profiles.build_baseline_policy(kid=kid, device=device)
    command = commands.install_profile(profile_bytes)
    command_uuid = commands.enqueue(
        identity_cn=device.mdm.identity_cn,
        command=command,
    )
    log.info("deploy_baseline queued command=%s for %s/%s (%s)",
             command_uuid, kid.name, device.name, device.wg_ip)

    push_error = None
    if device.mdm.push_token and device.mdm.push_magic:
        try:
            apns.send_push(
                push_token_b64=device.mdm.push_token,
                push_magic=device.mdm.push_magic,
            )
        except apns.ApnsError as e:
            push_error = str(e)
            log.warning("APNs push after deploy_baseline failed: %s", e)
    return {"command_uuid": command_uuid, "push_error": push_error}
