"""Policy .mobileconfig builders — the actual enforcement payloads pushed
to enrolled devices via MDM `InstallProfile` commands.

Three payloads bundled into a single "baseline" profile per device:

  1. com.apple.vpn.managed         — WireGuard always-on (OnDemand rules
                                     that trigger on any network)
  2. com.apple.security.root       — Trust the mitmproxy CA so HTTPS
                                     interception works without warnings
  3. com.apple.applicationaccess   — Restrictions: disallow VPN-config
                                     creation + profile install so the
                                     kid can't bypass

The profile itself is non-removable (`PayloadRemovalDisallowed=True`) and
non-replaceable by the user; on a supervised device, that means the only
escape hatch is re-supervising via Apple Configurator.

`build_baseline_policy(kid, device)` composes the three payloads into a
single .mobileconfig the orchestrator hands to `InstallProfile` commands.
"""
from __future__ import annotations

import plistlib
import uuid
from pathlib import Path

from .. import browsers, store, wg
from ..schema import ChromeManagedConfig, Device, IosBrowserPolicy, Kid
from ..settings import settings

ORG = "gdlf"


# ---------------------------------------------------------------------------
# Individual payload builders.


def _wg_quick_conf(device: Device) -> str:
    """Re-render the wg-quick conf for an existing device from disk state.

    We need the private key, which lives at
    <state_dir>/wg-keys/<peer_id>.priv — not in kids.yaml (only the public
    key is persisted there)."""
    peer_id = f"{wg.slug('CLIFTON_PLACEHOLDER')}__{wg.slug(device.name)}"
    # We don't actually have the kid name in this fn signature — callers
    # pass the prebuilt conf string in. Keep this internal helper for
    # documentation; build_baseline_policy reads via wg.load_peer_priv +
    # wg.build_client_conf directly.
    return ""


def vpn_payload(*, wg_quick_conf: str, display_name: str = "gdlf VPN") -> dict:
    """WireGuard VPN payload with OnDemand rules that keep the tunnel up
    on every network. On supervised devices, OnDemandUserOverrideDisabled
    prevents the kid from toggling it off via the WG app.

    The wg-quick conf goes verbatim into VendorConfig.WgQuickConfig — the
    WireGuard iOS app parses it on profile install.
    """
    return {
        "PayloadType": "com.apple.vpn.managed",
        "PayloadVersion": 1,
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadIdentifier": "nl.cliftonc.gdlf.payload.vpn",
        "PayloadDisplayName": display_name,
        "PayloadDescription": "gdlf WireGuard tunnel — always on.",
        "PayloadOrganization": ORG,
        "UserDefinedName": display_name,
        "VPNType": "VPN",
        "VPNSubType": "com.wireguard.ios",
        "VendorConfig": {
            "WgQuickConfig": wg_quick_conf,
        },
        "OnDemandEnabled": 1,
        "OnDemandRules": [
            # "Connect on any interface" — the simplest always-on rule.
            # iOS will auto-connect whenever any network comes up.
            {
                "Action": "Connect",
                "InterfaceTypeMatch": "Any",
            },
        ],
        # Supervised-only: user can't disable on-demand from Settings.
        "OnDemandUserOverrideDisabled": True,
    }


def ca_trust_payload(*, ca_der: bytes, common_name: str = "gdlf CA") -> dict:
    """Install the mitmproxy CA into the system keychain AND mark it as
    SSL-trusted. The SSL trust bit is the load-bearing piece — without
    it, iOS shows certificate warnings for every intercepted HTTPS site.

    MDM-pushed roots are auto-trusted for SSL (the manual Settings → About
    → Certificate Trust step that user-installed certs require is skipped
    when delivered via MDM)."""
    return {
        "PayloadType": "com.apple.security.root",
        "PayloadVersion": 1,
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadIdentifier": "nl.cliftonc.gdlf.payload.ca",
        "PayloadDisplayName": "gdlf TLS Inspection CA",
        "PayloadDescription": "Lets gdlf inspect HTTPS traffic for URL-rule enforcement.",
        "PayloadOrganization": ORG,
        "PayloadCertificateFileName": "gdlf-ca.cer",
        "PayloadContent": ca_der,         # plistlib serialises bytes as <data>
    }


def restrictions_payload(ios_policy: IosBrowserPolicy) -> dict:
    """Lock down the loopholes a determined kid would otherwise find:

      * allowVPNCreation=False — can't add a non-gdlf VPN that would
        take precedence on the routing table.
      * allowProfileInstallation=False — can't install another MDM /
        custom profile that overrides our restrictions.
      * allowEraseContentAndSettings=False — can't factory-reset to
        escape the enrollment.
      * allowSafari controlled by browser policy: Safari is removed
        from the device unless the parent explicitly chose it as the
        allowed browser.
      * blacklistedAppBundleIDs blocks every other known browser so the
        kid can't sideload a Chromium fork with its own DoH.

    Everything else stays default-allowed. We're a guardrail, not a
    lockdown appliance — kids should still be able to use their phones.
    """
    return {
        "PayloadType": "com.apple.applicationaccess",
        "PayloadVersion": 1,
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadIdentifier": "nl.cliftonc.gdlf.payload.restrictions",
        "PayloadDisplayName": "gdlf Restrictions",
        "PayloadDescription": "Closes common MDM-bypass paths.",
        "PayloadOrganization": ORG,
        "allowVPNCreation": False,
        "allowProfileInstallation": False,
        "allowEraseContentAndSettings": False,
        "allowSafari": ios_policy.allowed_browser == "safari",
        "blacklistedAppBundleIDs": browsers.ios_blocklist(ios_policy),
    }


def chrome_appconfig_payload(cfg: ChromeManagedConfig, bundle_id: str) -> dict:
    """App Configuration payload bound to whichever Chromium-based iOS
    browser the parent allowed. Disables Incognito / Sync / Sign-in per
    the global ChromeManagedConfig toggles.

    PayloadType `com.apple.app.managed` is the App Configuration payload
    (iOS 7+); Chromium browsers read the dict at `Configuration` using
    the standard Chrome enterprise policy keys.
    """
    return {
        "PayloadType": "com.apple.app.managed",
        "PayloadVersion": 1,
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadIdentifier": "nl.cliftonc.gdlf.payload.browser.appconfig",
        "PayloadDisplayName": "gdlf Browser Managed Config",
        "PayloadDescription": "Disables Incognito / Sync / Sign-in in the allowed browser.",
        "PayloadOrganization": ORG,
        "BundleID": bundle_id,
        "Configuration": browsers.chrome_cfg_dict(cfg),
    }


# ---------------------------------------------------------------------------
# Composite: the baseline policy profile.


def _load_mitm_ca_der() -> bytes:
    """The mitmproxy CA lives in /etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem
    (mounted into rules-svc). The file is PEM — strip the armour to get
    DER, which is what com.apple.security.root expects in PayloadContent."""
    from cryptography import x509
    pem_path = Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem")
    cert = x509.load_pem_x509_certificate(pem_path.read_bytes())
    from cryptography.hazmat.primitives import serialization
    return cert.public_bytes(serialization.Encoding.DER)


def build_baseline_policy(*, kid: Kid, device: Device) -> bytes:
    """Compose the WG + CA + Restrictions payloads into one
    .mobileconfig the device installs in a single `InstallProfile` command.

    Returns the raw plist bytes (suitable for the `Payload` field of
    InstallProfile). Re-render any time the device's WG keypair, the
    mitmproxy CA, or the restrictions list changes.
    """
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"
    priv = wg.load_peer_priv(peer_id)
    wg_conf = wg.build_client_conf(device.name, priv, device.wg_ip)

    policy = store.load().browser_policy
    payloads = [
        vpn_payload(wg_quick_conf=wg_conf),
        ca_trust_payload(ca_der=_load_mitm_ca_der()),
        restrictions_payload(policy.ios),
    ]
    chrome_bundle_id = browsers.ios_allowed_bundle_id(policy.ios)
    if chrome_bundle_id is not None:
        payloads.append(chrome_appconfig_payload(policy.chrome_managed_config, chrome_bundle_id))

    profile = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadIdentifier": f"nl.cliftonc.gdlf.policy.{device.wg_ip}",
        "PayloadDisplayName": f"gdlf Policy ({kid.name} / {device.name})",
        "PayloadDescription": (
            "WireGuard always-on + TLS interception CA + bypass restrictions. "
            "Re-installed on every policy change."
        ),
        "PayloadOrganization": ORG,
        # Non-removable: on supervised devices, the user can't uninstall.
        # Re-supervising via Apple Configurator is the only escape hatch.
        "PayloadRemovalDisallowed": True,
        "PayloadContent": payloads,
    }
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)
