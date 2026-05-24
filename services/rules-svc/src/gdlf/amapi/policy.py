"""Translate a kid + device into an AMAPI Policy JSON.

Mirrors `mdm.profiles.build_baseline_policy()` but emits Google's Policy
schema instead of an Apple .mobileconfig. The three things we enforce —
always-on WireGuard, system-trusted CA, lockdown restrictions — map to
distinct fields on the Policy resource.

The WireGuard Android app's `managedConfiguration` schema is documented
informally; the load-bearing key is `config` (the .conf contents). The
DPC injects the bundle into the WG app at install, so the kid never sees
an "import tunnel" step.
"""
from __future__ import annotations

import base64
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .. import wg
from ..schema import Device, Kid


# Package name of the official WireGuard Android client on Play.
WG_PACKAGE = "com.wireguard.android"

# Where the mitmproxy CA PEM is mounted into rules-svc. Same path used by
# the iOS profile builder (`mdm.profiles._load_mitm_ca_der`).
_MITM_CA_PEM = Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem")


def policy_id_for(kid: Kid, device: Device) -> str:
    """Stable per-device policy id within the enterprise namespace."""
    return f"{wg.slug(kid.name)}__{wg.slug(device.name)}"


def policy_name_for(enterprise_name: str, kid: Kid, device: Device) -> str:
    """Fully-qualified policy resource name (`enterprises/.../policies/...`)."""
    return f"{enterprise_name}/policies/{policy_id_for(kid, device)}"


def _load_ca_b64() -> str:
    """Read mitmproxy CA PEM and return base64-encoded DER bytes (wrapped
    into the `CACert` object form AMAPI expects, see `build_policy`)."""
    cert = x509.load_pem_x509_certificate(_MITM_CA_PEM.read_bytes())
    der = cert.public_bytes(serialization.Encoding.DER)
    return base64.b64encode(der).decode("ascii")


def _wg_conf_for(kid: Kid, device: Device) -> str:
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"
    priv = wg.load_peer_priv(peer_id)
    return wg.build_client_conf(device.name, priv, device.wg_ip)


def build_policy(kid: Kid, device: Device) -> dict:
    """The single declarative document that turns a stock Android phone
    into a gdlf-managed device.

    Re-buildable from kids.yaml + on-disk keys + mitmproxy CA — no per-device
    randomness, so a repeat patch with unchanged inputs is a true no-op.
    """
    ca_b64 = _load_ca_b64()
    wg_conf = _wg_conf_for(kid, device)

    return {
        # Force-install WireGuard with the kid's tunnel config baked in via
        # Android Enterprise managed config. The DPC injects this at the
        # moment WG is installed, so no manual "import" step is needed.
        "applications": [
            {
                "packageName": WG_PACKAGE,
                "installType": "FORCE_INSTALLED",
                "defaultPermissionPolicy": "GRANT",
                "managedConfiguration": {
                    "config": wg_conf,
                    "tunnel_name": "gdlf",
                },
            },
        ],
        # Pin WG as the always-on VPN. `lockdownEnabled` = "Block connections
        # without VPN" — same effect as the Settings toggle a parent would
        # otherwise enable manually, but enforced and irrevocable.
        "alwaysOnVpnPackage": {
            "packageName": WG_PACKAGE,
            "lockdownEnabled": True,
        },
        # System-trusted CA. Unlike a user-installed CA (which apps must
        # opt-in to via networkSecurityConfig), an MDM-installed CA is
        # trusted by every app — this is THE reason to use AMAPI over
        # "ask the parent to install the cert manually".
        #
        # AMAPI's public Policy schema has no top-level caCerts field
        # (despite older docs implying one). The supported path is
        # Chromium's Open Network Configuration (ONC) embedded in
        # `openNetworkConfiguration`: TrustBits=["Web"] makes the
        # Authority cert trusted for TLS verification, which is exactly
        # the system-trust behaviour we want.
        "openNetworkConfiguration": {
            "Type": "UnencryptedConfiguration",
            "NetworkConfigurations": [],
            "Certificates": [
                {
                    "GUID": "gdlf-mitmproxy-ca",
                    "Type": "Authority",
                    "X509": ca_b64,
                    "TrustBits": ["Web"],
                },
            ],
        },
        # Close the obvious bypass paths.
        "addUserDisabled": True,
        "factoryResetDisabled": True,
        "modifyAccountsDisabled": True,
        "vpnConfigDisabled": True,              # can't add another VPN
        "uninstallAppsDisabled": True,          # can't uninstall WG
        "installUnknownSourcesAllowed": False,
        # Useful telemetry — populates `Device.lastStatusReportTime` so the
        # status sync loop has something to read.
        "statusReportingSettings": {
            "deviceSettingsEnabled": True,
            "softwareInfoEnabled": True,
            "networkInfoEnabled": True,
            "memoryInfoEnabled": False,
            "powerManagementEventsEnabled": False,
            "hardwareStatusEnabled": False,
            "displayInfoEnabled": False,
            "applicationReportsEnabled": False,
        },
    }
