"""Assemble a Windows enrolment bundle (.zip) for a single device.

The zip contains exactly:

  Install.cmd             — self-elevating UAC wrapper (entry point)
  install.ps1             — administrator-context install script
  reconcile.ps1           — body of the 5-minute scheduled task
  <kid>.conf              — the per-kid wg-quick config
  gdlf-mitm-ca.crt        — the mitmproxy CA in DER form (Windows-friendly)
  wireguard.msi           — the official WireGuard for Windows installer
  README.txt              — short instructions for the parent

Historical note: we originally built a Windows Provisioning Package
(`.ppkg`) — same intent, but `.ppkg` is internally a WIM archive with a
complex multi-XML structure (CommonSettings, Multivariant, MasterDatastore,
RunTime, per-setting `.provxml` files keyed by undocumented SettingsGroup
GUIDs). Only Microsoft's icd.exe (Windows Configuration Designer) can
produce a compliant one — `gcab` / `wimlib` get the container right but
the contents are an internal compiler step we can't replicate from Python.
A signed-`.ppkg` with a hand-rolled CAB inside makes Windows prompt for a
"package password" at apply time, then fail with a link to
aka.ms/provisioningfaq. So we deliver as a zip + self-elevating .cmd
instead. The on-device behaviour is identical to what apply.ps1 always did.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import secrets
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .. import wg
from ..schema import Device, Kid
from ..settings import settings
from . import scripts, wireguard_conf

log = logging.getLogger("gdlf.windows_mdm.package")


MITM_CA_PEM = Path("/etc/gdlf/mitmproxy/mitmproxy-ca-cert.pem")
WG_MSI_PATH = lambda: settings.state_dir / "windows" / "wireguard.msi"  # noqa: E731


# Filenames as packed inside the zip. Kept ASCII + lowercase so the
# PowerShell wrappers can reference them verbatim without quoting games.
INSTALL_CMD_FILENAME = "Install.cmd"
UNINSTALL_CMD_FILENAME = "Uninstall.cmd"
APPLY_FILENAME = "install.ps1"
RECONCILE_FILENAME = "reconcile.ps1"
REVOKE_FILENAME = "uninstall.ps1"
CA_FILENAME = "gdlf-mitm-ca.crt"
MSI_FILENAME = "wireguard.msi"
README_FILENAME = "README.txt"


@dataclass(frozen=True)
class BuiltPackage:
    """The result of `build_enroll_zip` / `build_revoke_zip`. The zip bytes
    plus the metadata the caller needs to record on the device."""
    ppkg_bytes: bytes        # legacy field name; now holds the zip bytes
    package_id: str          # GUID in `{...}` form
    package_version: str     # X.Y.Z.W (kept for compatibility w/ existing state)
    conf_sha256: str         # hex; reconcile.ps1 compares against this
    signed: bool             # always False; no signing in the zip path


class PackagingError(RuntimeError):
    """Raised when the bundle can't be built — missing asset, etc."""


# ---------------------------------------------------------------------------
# Public entry points.


def build_enroll_ppkg(
    *,
    kid: Kid,
    device: Device,
    dashboard_base_url: str = "",
    shortlink_code: str = "",
) -> BuiltPackage:
    """Build the enrolment .zip for one device.

    `dashboard_base_url` + `shortlink_code` enable the install.ps1
    phone-home: at the end of installation the script POSTs the
    mark-enrolled endpoint so the parent doesn't have to click Mark
    Applied. Both are best-effort — empty strings skip the phone-home.

    (Name kept as `build_enroll_ppkg` for API compatibility — callers
    already use this. The output is now a .zip, not a .ppkg.)
    """
    _require_assets(require_msi=True)

    wg_conf = wireguard_conf.render(kid, device)
    conf_sha256 = hashlib.sha256(wg_conf.encode("utf-8")).hexdigest()
    tunnel = wireguard_conf.tunnel_name(kid, device)
    conf_filename = f"{tunnel}.conf"

    ca_der = _load_mitm_ca_der()
    ca_sha1 = hashlib.sha1(ca_der).hexdigest()

    package_id = _stable_package_id(kid, device)
    package_version = _new_version(device)

    ctx = scripts.ScriptContext(
        kid_name=kid.name,
        device_name=device.name,
        wg_ip=device.wg_ip,
        tunnel_name=tunnel,
        conf_filename=conf_filename,
        conf_sha256=conf_sha256,
        ca_filename=CA_FILENAME,
        ca_sha1=ca_sha1,
        msi_filename=MSI_FILENAME,
        package_id=package_id,
        dashboard_base_url=dashboard_base_url,
        shortlink_code=shortlink_code,
    )

    install_cmd = scripts.render_install_cmd()
    install_ps1 = scripts.render_apply(ctx)
    reconcile_ps1 = scripts.render_reconcile(ctx)

    files: dict[str, bytes] = {
        INSTALL_CMD_FILENAME: install_cmd.encode("utf-8"),
        APPLY_FILENAME: install_ps1.encode("utf-8"),
        RECONCILE_FILENAME: reconcile_ps1.encode("utf-8"),
        conf_filename: wg_conf.encode("utf-8"),
        CA_FILENAME: ca_der,
        MSI_FILENAME: WG_MSI_PATH().read_bytes(),
        README_FILENAME: _enroll_readme(kid, device).encode("utf-8"),
    }

    zip_bytes = _pack_zip(files)
    return BuiltPackage(
        ppkg_bytes=zip_bytes,
        package_id=package_id,
        package_version=package_version,
        conf_sha256=conf_sha256,
        signed=False,
    )


def build_revoke_ppkg(*, kid: Kid, device: Device) -> BuiltPackage:
    """Build an un-enrolment .zip. Reuses the same `package_id` so state
    tracking is continuous across enrol → revoke.
    """
    _require_assets(require_msi=False)

    package_id = _stable_package_id(kid, device)
    package_version = _new_version(device, bump_for_revoke=True)
    uninstall_cmd = scripts.render_uninstall_cmd()
    uninstall_ps1 = scripts.render_revoke()

    files: dict[str, bytes] = {
        UNINSTALL_CMD_FILENAME: uninstall_cmd.encode("utf-8"),
        REVOKE_FILENAME: uninstall_ps1.encode("utf-8"),
        README_FILENAME: _revoke_readme(kid, device).encode("utf-8"),
    }
    zip_bytes = _pack_zip(files)
    return BuiltPackage(
        ppkg_bytes=zip_bytes,
        package_id=package_id,
        package_version=package_version,
        conf_sha256="",
        signed=False,
    )


# ---------------------------------------------------------------------------
# Helpers.


def _stable_package_id(kid: Kid, device: Device) -> str:
    """Stable GUID per (kid, device). Kept from the .ppkg era so the
    `WindowsMdmState.package_id` field on existing devices stays valid."""
    peer_id = f"{wg.slug(kid.name)}__{wg.slug(device.name)}"
    namespace = uuid.UUID("6f4b9b8f-1f4c-4f3d-9b3a-1f3e1f3e1f3e")
    return "{" + str(uuid.uuid5(namespace, peer_id)) + "}"


def _new_version(device: Device, *, bump_for_revoke: bool = False) -> str:
    """Four-part version kept for state-tracking parity with the old .ppkg
    path. Not load-bearing in the zip world — there's no Windows-side
    "is this newer" check on a plain zip — but the dashboard still reads
    `package_version` from kids.yaml to surface when the last bundle was
    issued."""
    prev = device.windows_mdm.package_version if device.windows_mdm else None
    epoch = int(datetime.utcnow().timestamp())
    third = epoch % 65535
    fourth = 2 if bump_for_revoke else 1
    candidate = f"1.0.{third}.{fourth}"
    if prev:
        try:
            if _version_tuple(candidate) <= _version_tuple(prev):
                candidate = _bump(prev, fourth)
        except ValueError:
            pass
    return candidate


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def _bump(prev: str, fourth: int) -> str:
    a, b, c, _ = _version_tuple(prev) + (0,) * (4 - len(prev.split(".")))
    return f"{a}.{b}.{(c + 1) % 65535}.{fourth}"


def _load_mitm_ca_der() -> bytes:
    if not MITM_CA_PEM.exists():
        raise PackagingError(
            f"mitmproxy CA missing at {MITM_CA_PEM} — run `./gdlf init`"
        )
    cert = x509.load_pem_x509_certificate(MITM_CA_PEM.read_bytes())
    return cert.public_bytes(serialization.Encoding.DER)


def _require_assets(*, require_msi: bool) -> None:
    if not MITM_CA_PEM.exists():
        raise PackagingError(
            f"mitmproxy CA missing at {MITM_CA_PEM} — run `./gdlf init`"
        )
    if require_msi and not WG_MSI_PATH().exists():
        raise PackagingError(
            f"WireGuard MSI missing at {WG_MSI_PATH()} — run `./gdlf windows init`"
        )


# ---------------------------------------------------------------------------
# Zip packing.


def _pack_zip(files: dict[str, bytes]) -> bytes:
    """Pack the given (filename -> bytes) map into a single zip blob.

    Uses ZIP_DEFLATED so the ~10 MB WireGuard MSI compresses well. Stable
    modification time (epoch) so re-building the same inputs yields a
    bit-identical zip — handy for caching / dedup."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(filename=name)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def _enroll_readme(kid: Kid, device: Device) -> str:
    return (
        "gdlf — Windows enrolment for {kid} / {device} ({ip})\r\n"
        "\r\n"
        "Extract this zip on the kid's PC, then **right-click Install.cmd ->\r\n"
        "Run as administrator** (or double-click it and click Yes at the UAC\r\n"
        "prompt that appears).\r\n"
        "\r\n"
        "Takes ~30 seconds. The script installs the gdlf inspection CA,\r\n"
        "WireGuard, the per-kid tunnel as an auto-start Windows service,\r\n"
        "and a SYSTEM scheduled task that re-asserts state every 5 minutes.\r\n"
        "\r\n"
        "When it's done, come back to the dashboard and click Mark applied.\r\n"
    ).format(kid=kid.name, device=device.name, ip=device.wg_ip)


def _revoke_readme(kid: Kid, device: Device) -> str:
    return (
        "gdlf — Windows un-enrolment for {kid} / {device} ({ip})\r\n"
        "\r\n"
        "Extract this zip on the kid's PC, then **right-click Uninstall.cmd ->\r\n"
        "Run as administrator**.\r\n"
        "\r\n"
        "Removes the gdlf tunnel service, the reconcile task, the gdlf CA,\r\n"
        "and the C:\\ProgramData\\gdlf directory. WireGuard itself is left\r\n"
        "installed.\r\n"
        "\r\n"
        "When it's done, come back to the dashboard and click Mark applied.\r\n"
    ).format(kid=kid.name, device=device.name, ip=device.wg_ip)


# ---------------------------------------------------------------------------
# Storage helpers — used by api_windows_mdm to persist the built .zip
# until the parent downloads it.


def packages_dir() -> Path:
    p = settings.state_dir / "windows" / "packages"
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def stash(blob: bytes) -> str:
    """Persist a zip blob under packages_dir() keyed by a random handle.

    Returns the handle the caller embeds in the single-use download URL.
    Caller is responsible for cleanup via `unstash`. Kept the `.ppkg`
    extension here for backwards compatibility with stashes that may
    already be on disk from earlier builds; new stashes go to .zip."""
    handle = secrets.token_urlsafe(24)
    (packages_dir() / f"{handle}.zip").write_bytes(blob)
    return handle


def unstash(handle: str) -> bytes:
    # Prefer .zip (current); fall back to .ppkg (any pre-rewrite leftovers
    # waiting to be downloaded).
    p_zip = packages_dir() / f"{handle}.zip"
    p_old = packages_dir() / f"{handle}.ppkg"
    p = p_zip if p_zip.exists() else p_old
    if not p.exists():
        raise FileNotFoundError(handle)
    blob = p.read_bytes()
    p.unlink()
    return blob
