"""Apple .mobileconfig enrollment profile generator.

Produces the XML plist that Apple Configurator 2 streams into a supervised
iPhone during the "Prepare" ceremony. Two payloads in one profile:

  1. com.apple.security.pkcs12 — the device identity (mints a fresh cert
     signed by the gdlf MDM CA, wrapped in a password-protected PKCS12).
     This becomes the TLS client identity for all subsequent MDM traffic.
  2. com.apple.mdm — the MDM enrollment payload itself, referencing the
     PKCS12 by its PayloadUUID via IdentityCertificateUUID.

Profile is unsigned for v1. iOS shows an "Unverified" warning during
install (still works). Signing with an Apple Developer cert would remove
the warning — deferred to a follow-up.
"""
from __future__ import annotations

import plistlib
import uuid
from dataclasses import dataclass

from . import apns, identity

# Tells iOS the MDM server may issue every documented command. Lifted from
# the Apple MDM Protocol Reference — bitmask of granted rights.
# Reference: https://developer.apple.com/documentation/devicemanagement/mdm
# 8191 = all bits set across the documented range; conservative apps narrow
# this, but for a parental-control appliance we want full access.
MDM_ACCESS_RIGHTS = 8191


@dataclass(frozen=True)
class EnrollmentProfile:
    """The bytes we hand back to Apple Configurator + the metadata we keep."""
    plist_xml: bytes              # Content-Type: application/x-apple-aspen-config
    identity_cn: str              # CN on the minted cert (lookup key on mTLS)
    identity_serial_hex: str      # for revocation tracking on the Device.mdm record


def build(*, wg_ip: str, mdm_base_url: str, organization: str = "gdlf") -> EnrollmentProfile:
    """Build a complete enrollment .mobileconfig for the device at `wg_ip`.

    `mdm_base_url` is the public origin (scheme + host + port) of the MDM
    endpoints; e.g. "https://gdlf.cliftonc.nl:8443". The CheckInURL and
    ServerURL are derived as `<base>/mdm/checkin` and `<base>/mdm/server`.
    """
    ident = identity.mint_device_identity(wg_ip=wg_ip)

    pkcs12_uuid = str(uuid.uuid4()).upper()
    mdm_uuid = str(uuid.uuid4()).upper()
    profile_uuid = str(uuid.uuid4()).upper()

    pkcs12_payload = {
        "PayloadType": "com.apple.security.pkcs12",
        "PayloadVersion": 1,
        "PayloadUUID": pkcs12_uuid,
        "PayloadIdentifier": f"nl.cliftonc.gdlf.identity.{wg_ip}",
        "PayloadDisplayName": f"gdlf MDM Identity ({wg_ip})",
        "PayloadDescription": "Device identity certificate for gdlf MDM.",
        "PayloadOrganization": organization,
        "PayloadContent": ident.pkcs12_bytes,             # raw bytes → Data
        "Password": ident.pkcs12_password,
    }

    mdm_payload = {
        "PayloadType": "com.apple.mdm",
        "PayloadVersion": 1,
        "PayloadUUID": mdm_uuid,
        "PayloadIdentifier": f"nl.cliftonc.gdlf.mdm.{wg_ip}",
        "PayloadDisplayName": "gdlf MDM",
        "PayloadDescription": "Enrols this device with the gdlf MDM server.",
        "PayloadOrganization": organization,
        # The identity payload above is referenced by UUID — Apple uses the
        # PKCS12 it installed as the TLS client identity for every check-in.
        "IdentityCertificateUUID": pkcs12_uuid,
        "Topic": apns.push_cert_topic(),
        "CheckInURL": f"{mdm_base_url}/mdm/checkin",
        "ServerURL": f"{mdm_base_url}/mdm/server",
        # SignMessage tells Apple to CMS-sign every check-in/command response
        # with the device identity cert. Lets us verify it server-side.
        "SignMessage": True,
        # CheckOutWhenRemoved: Apple sends a CheckOut message if the profile
        # is removed; we use it to mark the Device.mdm.status = checked_out.
        "CheckOutWhenRemoved": True,
        "AccessRights": MDM_ACCESS_RIGHTS,
        "ServerCapabilities": ["com.apple.mdm.per-user-connections"],
    }

    profile = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadUUID": profile_uuid,
        "PayloadIdentifier": f"nl.cliftonc.gdlf.enroll.{wg_ip}",
        "PayloadDisplayName": f"gdlf Enrollment ({wg_ip})",
        "PayloadDescription": (
            "Enrols this iPhone into the gdlf parental-controls MDM. "
            "Installing this profile lets gdlf push the WireGuard always-on "
            "VPN, TLS interception CA, and screen-time restrictions."
        ),
        "PayloadOrganization": organization,
        # Order matters: pkcs12 first so the identity exists by the time
        # the mdm payload references it. Apple processes them in order.
        "PayloadContent": [pkcs12_payload, mdm_payload],
    }

    plist_xml = plistlib.dumps(profile, fmt=plistlib.FMT_XML, sort_keys=False)
    return EnrollmentProfile(
        plist_xml=plist_xml,
        identity_cn=ident.identity_cn,
        identity_serial_hex=ident.serial_hex,
    )
