"""Glue between kids.yaml mutations and AMAPI.

Two responsibilities:

  1. `sync_policy(kid, device)` — push the rebuilt Policy for a single
     device after a kids.yaml change. Idempotent; called from the
     dashboard mutations and the kids.yaml mutation hook.

  2. `sync_device_status()` — periodic poll. Reads `enterprises.devices.list`
     and refreshes each Device.android_mdm.{status, model, last_status_at,
     applied_policy_version, device_name} from Google's view.

We deliberately avoid Pub/Sub: it requires a public callback URL and gives
us notifications faster than we need them. A 60s poll for a single-family
deployment costs ~1440 API calls/day per enrolled device — well within the
free tier.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from .. import store
from ..schema import AndroidMdmState, Device, Kid
from . import client, policy

log = logging.getLogger("gdlf.amapi.orchestrator")


# How often the background poller refreshes device state.
STATUS_POLL_INTERVAL = 60.0


def sync_policy(kid: Kid, device: Device) -> str:
    """Build + patch the AMAPI Policy for this device. Returns the policy
    resource name. Caller is responsible for surfacing failures."""
    svc = client.service()
    enterprise = client.load_enterprise()

    policy_name = policy.policy_name_for(enterprise.name, kid, device)
    body = policy.build_policy(kid, device)
    # `patch` with no updateMask replaces the whole policy — that's what we
    # want, since `build_policy` is fully deterministic from kids.yaml state.
    svc.enterprises().policies().patch(name=policy_name, body=body).execute()
    log.info("amapi sync_policy %s/%s -> %s", kid.name, device.name, policy_name)
    return policy_name


def sync_policy_for_ip(wg_ip: str) -> str | None:
    """Look up the kid+device by WG IP, then sync. No-op if the device has
    no android_mdm state yet (i.e. never enrolled)."""
    cfg = store.load(force=True)
    found = cfg.device_by_ip(wg_ip)
    if not found:
        return None
    kid, device = found
    if not device.android_mdm:
        return None
    return sync_policy(kid, device)


def sync_all_policies() -> dict:
    """Re-sync every Android-enrolled device. Used after broad kids.yaml
    changes. Errors per-device are logged but don't abort the loop."""
    cfg = store.load(force=True)
    results = {"ok": [], "errors": []}
    for kid in cfg.kids:
        for device in kid.devices:
            if not device.android_mdm:
                continue
            try:
                sync_policy(kid, device)
                results["ok"].append(device.wg_ip)
            except Exception as e:
                log.warning("sync_policy failed for %s: %s", device.wg_ip, e)
                results["errors"].append({"ip": device.wg_ip, "error": str(e)})
    return results


def sync_device_status() -> int:
    """One pass of the status poll. Returns the number of devices updated.

    Strategy: list all devices in the enterprise (cheap, single API call),
    match each back to a Device via `additionalData` (we stuff the wg_ip
    there at enrollment time), and write the mirrored fields back to
    kids.yaml.
    """
    svc = client.service()
    enterprise = client.load_enterprise()

    resp = (
        svc.enterprises()
        .devices()
        .list(parent=enterprise.name, pageSize=100)
        .execute()
    )
    devices = resp.get("devices") or []
    by_wg_ip: dict[str, dict] = {}
    for d in devices:
        meta = _parse_additional_data(d.get("additionalData"))
        wg_ip = meta.get("wg_ip")
        if wg_ip:
            by_wg_ip[wg_ip] = d

    updated = 0
    now = datetime.utcnow()

    def apply(cfg):
        nonlocal updated
        for kid in cfg.kids:
            for device in kid.devices:
                if not device.android_mdm:
                    continue
                amapi_dev = by_wg_ip.get(device.wg_ip)
                if not amapi_dev:
                    continue
                state = (amapi_dev.get("state") or "").lower() or "pending"
                # Normalise to our literal set.
                if state == "active":
                    norm = "active"
                elif state == "disabled":
                    norm = "disabled"
                elif state == "deleted":
                    norm = "deleted"
                else:
                    norm = device.android_mdm.status

                hw = amapi_dev.get("hardwareInfo") or {}
                model = hw.get("model") or device.android_mdm.model
                new_state = device.android_mdm.model_copy(update={
                    "device_name": amapi_dev.get("name") or device.android_mdm.device_name,
                    "status": norm,
                    "model": model,
                    "last_status_at": now,
                    "applied_policy_version": (
                        str(amapi_dev.get("appliedPolicyVersion"))
                        if amapi_dev.get("appliedPolicyVersion") is not None
                        else device.android_mdm.applied_policy_version
                    ),
                    "enrolled_at": (
                        device.android_mdm.enrolled_at
                        or _parse_iso(amapi_dev.get("enrollmentTime"))
                    ),
                })
                if new_state != device.android_mdm:
                    device.android_mdm = new_state
                    updated += 1
        return cfg

    if devices:
        store.mutate(apply)
    return updated


def _parse_additional_data(s: str | None) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return {}


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # AMAPI returns RFC3339 ("2026-05-24T...Z"); fromisoformat with the
        # 'Z' replaced handles it.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


async def status_sync_loop() -> None:
    """Background task started from main.lifespan.

    Silently no-ops while AMAPI isn't configured — so adding the loop is
    free until the parent runs `./gdlf amapi init`.
    """
    while True:
        try:
            if client.is_configured():
                n = await asyncio.to_thread(sync_device_status)
                if n:
                    log.info("amapi status sync updated %d device(s)", n)
        except Exception as e:
            log.warning("amapi status sync failed: %s", e)
        await asyncio.sleep(STATUS_POLL_INTERVAL)


def initial_state_for(kid: Kid, device: Device) -> AndroidMdmState:
    """Construct the AndroidMdmState we attach when the parent first mints
    an enrollment token. Filled in further by the status poll once the
    device actually enrols."""
    enterprise = client.load_enterprise()
    return AndroidMdmState(
        policy_name=policy.policy_name_for(enterprise.name, kid, device),
        status="pending",
    )
