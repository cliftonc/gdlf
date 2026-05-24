"""MDM command builders + queue helpers.

A "command" is what the MDM server sends a device — DeviceInformation,
InstallProfile, RemoveProfile, DeviceLock, etc. Each command has a
CommandUUID (we generate), a RequestType (Apple-defined), and command-specific
parameters.

Flow:
  1. enqueue(identity_cn, request_type, params) writes a row to
     mdm_command_queue with status=pending.
  2. When the device polls /mdm/server, next_for_device() pops the
     oldest pending command for that identity and marks it sent.
  3. The device executes it and POSTs back a response; record_response()
     stores it in mdm_command_responses and flips the queue row to
     acknowledged|error|notnow.

Apple's full command reference:
  https://developer.apple.com/documentation/devicemanagement
"""
from __future__ import annotations

import plistlib
import uuid
from datetime import datetime

from sqlmodel import select

from .. import db


# ---------------------------------------------------------------------------
# Command plist builders.
# Each returns the {"Command": {...}} dict; the outer CommandUUID is added
# at dispatch time by next_for_device().

DEFAULT_DEVICE_INFO_QUERIES = [
    "UDID", "DeviceName", "OSVersion", "BuildVersion",
    "ProductName", "Model", "ModelName", "SerialNumber",
    "BatteryLevel", "AvailableDeviceCapacity", "DeviceCapacity",
    "IsSupervised", "IsMultiUser", "IsDeviceLocatorServiceEnabled",
    "WiFiMAC", "BluetoothMAC", "PhoneNumber", "ICCID", "IMEI",
]


def device_information(queries: list[str] | None = None) -> dict:
    return {
        "RequestType": "DeviceInformation",
        "Queries": queries or DEFAULT_DEVICE_INFO_QUERIES,
    }


def installed_application_list() -> dict:
    """Lists all third-party apps installed on the device (supervised only
    for full visibility; unsupervised returns just managed apps)."""
    return {"RequestType": "InstalledApplicationList"}


def install_profile(profile_plist: bytes) -> dict:
    """Push a configuration profile (.mobileconfig bytes) to the device.

    The PayloadType is whatever the profile contains — VPN, restrictions,
    cert trust, etc. This is the workhorse command Phase 4 will use to push
    the WireGuard always-on payload + restrictions when kids.yaml changes.
    """
    return {
        "RequestType": "InstallProfile",
        "Payload": profile_plist,
    }


def remove_profile(profile_identifier: str) -> dict:
    """Remove a profile previously installed by us. `profile_identifier`
    matches the PayloadIdentifier set in the profile."""
    return {
        "RequestType": "RemoveProfile",
        "Identifier": profile_identifier,
    }


def device_lock(message: str | None = None) -> dict:
    """Lock the screen now. Optional message shown on the lock screen."""
    cmd = {"RequestType": "DeviceLock"}
    if message:
        cmd["Message"] = message
    return cmd


def erase_device() -> dict:
    """Factory-wipe. Destructive — never enqueue without explicit user intent."""
    return {"RequestType": "EraseDevice"}


# ---------------------------------------------------------------------------
# Queue helpers.


def enqueue(*, identity_cn: str, command: dict) -> str:
    """Persist a pending command for the device identified by `identity_cn`.

    Returns the CommandUUID — the dashboard can poll mdm_command_responses
    by that UUID to surface results to the parent.
    """
    command_uuid = str(uuid.uuid4()).upper()
    payload_xml = plistlib.dumps(command, fmt=plistlib.FMT_XML).decode("utf-8")
    with db.session() as s:
        s.add(db.MdmCommandQueue(
            identity_cn=identity_cn,
            command_uuid=command_uuid,
            request_type=command["RequestType"],
            payload=payload_xml,
            status="pending",
        ))
        s.commit()
    return command_uuid


def next_for_device(identity_cn: str) -> bytes | None:
    """Pop the oldest pending command for this device and return the full
    plist Apple expects on /mdm/server (CommandUUID + Command).

    Marks the row `sent` so concurrent polls don't double-deliver. If the
    device responds NotNow we re-enqueue (see record_response)."""
    with db.session() as s:
        row = s.exec(
            select(db.MdmCommandQueue)
            .where(db.MdmCommandQueue.identity_cn == identity_cn)
            .where(db.MdmCommandQueue.status == "pending")
            .order_by(db.MdmCommandQueue.created_at.asc())
            .limit(1)
        ).first()
        if not row:
            return None
        command_uuid = row.command_uuid
        payload_xml = row.payload
        row.status = "sent"
        row.sent_at = datetime.utcnow()
        s.add(row)
        s.commit()

    command_dict = plistlib.loads(payload_xml.encode("utf-8"))
    envelope = {
        "CommandUUID": command_uuid,
        "Command": command_dict,
    }
    return plistlib.dumps(envelope, fmt=plistlib.FMT_XML)


def record_response(*, identity_cn: str, response: dict) -> None:
    """Persist a device's response to a previously-sent command.

    Apple's Status values:
      Acknowledged       — command succeeded; result fields in the dict
      Error              — command failed; ErrorChain has details
      CommandFormatError — we sent a malformed command (bug on our side)
      NotNow             — device can't handle it right now (passcode locked,
                           low battery, etc.) — Apple's docs say to re-queue
      Idle               — no response (only seen when device is reporting
                           its first poll after waking with nothing queued)
    """
    command_uuid = response.get("CommandUUID")
    status = response.get("Status", "Unknown")
    if not command_uuid:
        # Idle reports come through here too — nothing to record.
        return

    with db.session() as s:
        # Persist the response regardless of which command it ties to —
        # the dashboard surfaces these for debugging.
        s.add(db.MdmCommandResponse(
            identity_cn=identity_cn,
            command_uuid=command_uuid,
            status=status,
            response_plist=plistlib.dumps(response, fmt=plistlib.FMT_XML).decode("utf-8"),
        ))

        row = s.exec(
            select(db.MdmCommandQueue)
            .where(db.MdmCommandQueue.command_uuid == command_uuid)
        ).first()
        if row:
            if status == "NotNow":
                # Re-queue: device will try again later. Reset sent_at so
                # we serve it on the next poll.
                row.status = "pending"
                row.sent_at = None
            elif status == "Acknowledged":
                row.status = "acknowledged"
                row.completed_at = datetime.utcnow()
            else:
                row.status = "error"
                row.completed_at = datetime.utcnow()
            s.add(row)
        s.commit()


def queue_for_device(identity_cn: str) -> list[dict]:
    """Dashboard view: pending + recently-sent commands for a device."""
    with db.session() as s:
        rows = s.exec(
            select(db.MdmCommandQueue)
            .where(db.MdmCommandQueue.identity_cn == identity_cn)
            .order_by(db.MdmCommandQueue.created_at.desc())
            .limit(50)
        ).all()
        # Snapshot inside the session to avoid DetachedInstanceError
        return [
            {
                "command_uuid": r.command_uuid,
                "request_type": r.request_type,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ]


def responses_for_device(identity_cn: str, limit: int = 20) -> list[dict]:
    """Dashboard view: most recent command responses for a device."""
    with db.session() as s:
        rows = s.exec(
            select(db.MdmCommandResponse)
            .where(db.MdmCommandResponse.identity_cn == identity_cn)
            .order_by(db.MdmCommandResponse.ts.desc())
            .limit(limit)
        ).all()
        return [
            {
                "command_uuid": r.command_uuid,
                "status": r.status,
                "ts": r.ts.isoformat(),
                # Truncated — full plist is large; UI shows summary.
                "response_excerpt": (r.response_plist or "")[:2000],
            }
            for r in rows
        ]
