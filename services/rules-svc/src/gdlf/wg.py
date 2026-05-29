"""WireGuard key/IP management and wg0.conf rendering.

The rules-svc is the source of truth: kids.yaml lists peers, we render
wg0.conf, and ask the wg container to reload (SIGHUP / wg syncconf).

Keys live in <state_dir>/wg-keys/<peer-name>.{priv,pub}. Only public keys
appear in kids.yaml (so the file stays safe to share/back up). The server
private key lives at <state_dir>/wg-keys/_server.priv.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import secrets
import socket
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization

from .schema import KidsConfig
from .settings import settings


# ---- Docker socket helpers ----
# We talk to the docker daemon directly via the mounted /var/run/docker.sock
# rather than shipping the docker CLI in the image.

_DOCKER_SOCK = "/var/run/docker.sock"


def _docker_client() -> httpx.Client:
    transport = httpx.HTTPTransport(uds=_DOCKER_SOCK)
    return httpx.Client(transport=transport, base_url="http://docker", timeout=10.0)


def _docker_exec(container: str, cmd: list[str], timeout: float = 10.0) -> tuple[int, bytes]:
    """Run `cmd` in `container`, return (exit_code, stdout_bytes).

    Implements the multi-step Docker exec API: create exec instance, then
    start it with stdout streaming, then read multiplexed output, then
    inspect the exec instance for exit code.
    """
    with _docker_client() as c:
        r = c.post(
            f"/containers/{container}/exec",
            json={"AttachStdout": True, "AttachStderr": True, "Cmd": cmd, "Tty": False},
        )
        r.raise_for_status()
        exec_id = r.json()["Id"]
        # Start with streaming (Hijack=False => returns multiplexed stream).
        r = c.post(
            f"/exec/{exec_id}/start",
            json={"Detach": False, "Tty": False},
            timeout=timeout,
        )
        body = r.content
        # Strip 8-byte docker stream frame headers: [stream, 0, 0, 0, sz, sz, sz, sz, payload...]
        out = bytearray()
        i = 0
        while i + 8 <= len(body):
            sz = int.from_bytes(body[i + 4 : i + 8], "big")
            payload = body[i + 8 : i + 8 + sz]
            if body[i] == 1:  # stdout
                out.extend(payload)
            i += 8 + sz
        # Get exit code.
        r = c.get(f"/exec/{exec_id}/json")
        code = r.json().get("ExitCode", -1)
        return code, bytes(out)


def _docker_restart(container: str) -> None:
    with _docker_client() as c:
        c.post(f"/containers/{container}/restart", timeout=20.0)


def _docker_available() -> bool:
    return os.path.exists(_DOCKER_SOCK)


def _keys_dir() -> Path:
    d = settings.state_dir / "wg-keys"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def generate_keypair() -> tuple[str, str]:
    """Return (private_b64, public_b64) — WireGuard X25519 format."""
    priv = X25519PrivateKey.generate()
    pub = priv.public_key()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64(priv_raw), _b64(pub_raw)


def save_peer_keys(peer_id: str, priv_b64: str, pub_b64: str) -> None:
    d = _keys_dir()
    (d / f"{peer_id}.priv").write_text(priv_b64 + "\n")
    (d / f"{peer_id}.pub").write_text(pub_b64 + "\n")
    os.chmod(d / f"{peer_id}.priv", 0o600)


def load_peer_priv(peer_id: str) -> str:
    return (_keys_dir() / f"{peer_id}.priv").read_text().strip()


def ensure_server_keys() -> tuple[str, str]:
    """Generate the server keypair on first use; return (priv, pub)."""
    d = _keys_dir()
    priv_p = d / "_server.priv"
    pub_p = d / "_server.pub"
    if not priv_p.exists():
        priv, pub = generate_keypair()
        priv_p.write_text(priv + "\n")
        pub_p.write_text(pub + "\n")
        os.chmod(priv_p, 0o600)
    return priv_p.read_text().strip(), pub_p.read_text().strip()


def allocate_ip(cfg: KidsConfig) -> str:
    """Pick the next free /32 inside WG_SUBNET. Reserves .1 for the server."""
    net = ipaddress.ip_network(settings.wg_subnet, strict=False)
    used = {ipaddress.ip_address(d.wg_ip) for _, d in cfg.all_devices()}
    used.add(next(net.hosts()))  # .1 is the server
    for host in net.hosts():
        if host not in used:
            return str(host)
    raise RuntimeError(f"No free addresses in {settings.wg_subnet}")


def server_address() -> str:
    """The wg0 server-side address — first usable IP in the subnet."""
    net = ipaddress.ip_network(settings.wg_subnet, strict=False)
    return f"{next(net.hosts())}/{net.prefixlen}"


def render_wg0_conf(cfg: KidsConfig) -> str:
    server_priv, _ = ensure_server_keys()
    lines = [
        "# Rendered by gdlf rules-svc. Do not edit by hand.",
        "[Interface]",
        f"Address = {server_address()}",
        f"PrivateKey = {server_priv}",
        f"ListenPort = 51820",
        "",
        "# Forwarding is enabled at the container level (sysctl). The",
        "# nftables sidecar (sharing this netns) handles routing.",
        "",
    ]
    for kid, device in cfg.all_devices():
        if not device.wg_public_key:
            continue
        lines += [
            f"# {kid.name} / {device.name} ({device.platform})",
            "[Peer]",
            f"PublicKey = {device.wg_public_key}",
            f"AllowedIPs = {device.wg_ip}/32",
            "",
        ]
    return "\n".join(lines)


def write_wg0_conf(cfg: KidsConfig) -> Path:
    p = Path(os.environ.get("WG_CONF_PATH", "/etc/wireguard/wg0.conf"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_wg0_conf(cfg))
    os.chmod(p, 0o600)
    return p


# Containers that share wg's netns via `network_mode: "service:wg"`. When
# the wg container is restarted, its netns is destroyed and a new one
# created — sharers keep a dead pointer and lose all networking until
# they're restarted too. Mirrors `WG_DEPENDENTS` in the top-level `gdlf`
# script (single source of truth lives in compose's depends_on graph; this
# list is the tactical mirror used by the in-band reload path).
_WG_NETNS_SHARERS = ("gdlf-nft", "gdlf-mitm", "gdlf-blockpage", "gdlf-adguard")


def _restart_netns_sharers() -> None:
    """Restart wg's netns sharers so they re-attach to the new netns."""
    for name in _WG_NETNS_SHARERS:
        try:
            _docker_restart(name)
        except Exception:
            # Best-effort: a missing sharer (mdm profile off, mid-boot) is
            # fine, the next ./gdlf up will line things up.
            pass


def reload_wg() -> None:
    """Reload the wg container so it picks up the new wg0.conf.

    Tries `wg syncconf` first (no peer drops, no netns churn). Falls back
    to a container restart (~2s downtime) if syncconf fails or wg0 doesn't
    exist yet — in which case the netns gets rebuilt and we must also
    bounce every container sharing it, or they're left with stale routes.
    """
    container = os.environ.get("WG_CONTAINER", "gdlf-wg")
    if not _docker_available():
        return
    try:
        code, _ = _docker_exec(
            container,
            ["sh", "-c", "wg syncconf wg0 <(wg-quick strip /config/wg_confs/wg0.conf)"],
        )
        if code == 0:
            return
    except Exception:
        pass
    try:
        _docker_restart(container)
        _restart_netns_sharers()
    except Exception:
        pass


def build_client_conf(
    device_name: str,
    device_priv: str,
    device_ip: str,
) -> str:
    """The .conf to install on the kid's device."""
    _, server_pub = ensure_server_keys()
    subnet_prefix = ipaddress.ip_network(settings.wg_subnet, strict=False).prefixlen
    # DNS points at the wg gateway IP (10.13.13.1 by default), which is
    # always present inside the wg netns where AdGuard listens on 0.0.0.0:53.
    # This keeps the kid's source IP intact (no SNAT in the path) so per-kid
    # filtering in AdGuard works.
    net = ipaddress.ip_network(settings.wg_subnet, strict=False)
    server_ip = next(net.hosts())
    return (
        f"# gdlf client config for {device_name}\n"
        f"[Interface]\n"
        f"PrivateKey = {device_priv}\n"
        f"Address = {device_ip}/{subnet_prefix}\n"
        f"DNS = {server_ip}\n"
        f"\n"
        f"[Peer]\n"
        f"PublicKey = {server_pub}\n"
        f"AllowedIPs = 0.0.0.0/0, ::/0\n"
        f"Endpoint = {settings.wg_host}:{settings.wg_port}\n"
        f"PersistentKeepalive = 25\n"
    )


def wg_show_handshakes() -> dict[str, dict]:
    """Parse `wg show wg0 dump` to learn current handshake state.

    Returns {wg_ip: {"last_handshake": int_epoch, "rx": int, "tx": int}}.
    Best-effort — returns {} if wg isn't available locally.
    """
    container = os.environ.get("WG_CONTAINER", "gdlf-wg")
    try:
        code, raw = _docker_exec(container, ["wg", "show", "wg0", "dump"], timeout=5.0)
        if code != 0:
            return {}
        out = raw.decode()
    except Exception:
        return {}
    result: dict[str, dict] = {}
    for i, line in enumerate(out.strip().splitlines()):
        if i == 0:
            continue  # interface line
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        # public_key, preshared_key, endpoint, allowed_ips, latest_handshake, rx, tx, keepalive
        allowed_ips = parts[3]
        ip = allowed_ips.split("/")[0]
        try:
            result[ip] = {
                "last_handshake": int(parts[4]),
                "rx": int(parts[5]),
                "tx": int(parts[6]),
            }
        except ValueError:
            continue
    return result


def slug(s: str) -> str:
    """Filesystem-safe slug for a kid/device name."""
    out = []
    for c in s.lower():
        if c.isalnum() or c in "-_":
            out.append(c)
        elif c.isspace():
            out.append("-")
    return "".join(out) or secrets.token_hex(4)
