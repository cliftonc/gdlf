"""Per-device wg-quick conf for a Windows enrolment.

Thin wrapper around the existing `gdlf.wg.build_client_conf` so the rest of
the windows_mdm module doesn't have to know the (kid, device) -> peer_id
mapping.
"""
from __future__ import annotations

from .. import wg
from ..schema import Device, Kid


def render(kid: Kid, device: Device) -> str:
    """Return the .conf text for `device`. Raises FileNotFoundError if the
    peer's private key isn't on disk (device was never enrolled)."""
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"
    priv = wg.load_peer_priv(peer_id)
    return wg.build_client_conf(device.name, priv, device.wg_ip)


def tunnel_name(kid: Kid, device: Device) -> str:
    """The name WireGuard for Windows registers the per-tunnel service as
    (`WireGuardTunnel$<name>`). Stable per-(kid,device), filesystem-safe."""
    return f"gdlf-{wg.slug(kid.name)}-{wg.slug(device.name)}"
