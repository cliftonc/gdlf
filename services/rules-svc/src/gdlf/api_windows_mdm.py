"""HTTP surface for Windows enrolment via downloadable .zip bundle.

  POST /api/devices/{ip}/windows-mdm/enroll-package  (admin)
       Build a fresh enrolment .zip, stash it, mint a one-time download
       token, return the download URL.

  GET  /dl/windows-mdm/package.zip?t=<token>  (public)
       Single-use download. Burns the token + deletes the stashed blob
       on the first successful fetch. Public so the parent can copy the
       URL to the kid's PC and hit it from a browser there.

  POST /api/devices/{ip}/windows-mdm/mark-enrolled  (admin)
       Parent-attested confirmation that the bundle applied successfully.
       Flips WindowsMdmState.status from `pending` to `enrolled`. (There
       is no live check-in channel for Windows — see CLAUDE.md.)

  DELETE /api/devices/{ip}/windows-mdm  (admin)
       Generate a matching uninstall .zip + return its download URL. Flips
       status to `revoked` once the parent confirms via mark-enrolled
       (yes, same endpoint — semantically just "I applied the latest").

(Historical: this used to ship a `.ppkg`. We dropped it because .ppkg is
internally a WIM archive with Microsoft-internal compiled XML structure —
see `windows_mdm/package.py` docstring.)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import select

from . import api_shortlinks, db, store
from .schema import WindowsMdmState
from .windows_mdm import package

log = logging.getLogger("gdlf.windows_mdm")

router = APIRouter(tags=["windows-mdm"])

# A built .ppkg can sit on disk waiting for the parent to download it.
# After this window we drop the blob + invalidate the token.
DOWNLOAD_TTL = timedelta(hours=24)


def _dashboard_base_url(request: Request) -> str:
    """Best URL for the kid's PC to reach the dashboard's mark-enrolled
    endpoint from.

    Prefers the `Origin` header — the URL the parent's browser used to
    download the .zip — because it's the URL that's already proven
    reachable. Falls back to `request.base_url` minus the trailing slash
    if no Origin is set (e.g. from curl). Returns "" if neither works
    out (install.ps1 then skips the phone-home gracefully).
    """
    origin = request.headers.get("origin") or ""
    if origin:
        return origin.rstrip("/")
    base = str(request.base_url)
    return base.rstrip("/") if base else ""


def _device_or_404(ip: str):
    cfg = store.load(force=True)
    found = cfg.device_by_ip(ip)
    if not found:
        raise HTTPException(404, "unknown device")
    return cfg, found[0], found[1]


# --- Admin: build the enrolment package ------------------------------------


class PackageResponse(BaseModel):
    download_url: str          # /dl/windows-mdm/package.zip?t=...
    package_id: str            # GUID baked into the package
    package_version: str
    signed: bool               # True iff Authenticode signature attached
    expires_at: datetime


def _build_enroll_package(ip: str, request: Request) -> PackageResponse:
    _, kid, device = _device_or_404(ip)

    # Bake the dashboard URL + device shortlink into install.ps1 so it
    # can phone home at the end of installation and auto-mark the device
    # as enrolled. The Origin header is the URL the SPA is being served
    # from — exactly the URL the kid's PC needs to reach to mark.
    dashboard_base = _dashboard_base_url(request)
    shortlink = api_shortlinks.shortlink_for_ip(ip)
    if not shortlink:
        # Mint one — every device should have a shortlink, but a freshly-
        # created Windows device may have raced the auto-mint loop.
        try:
            shortlink = api_shortlinks.mint_shortlink(ip)
        except Exception:
            shortlink = ""

    try:
        built = package.build_enroll_ppkg(
            kid=kid,
            device=device,
            dashboard_base_url=dashboard_base,
            shortlink_code=shortlink or "",
        )
    except package.PackagingError as e:
        raise HTTPException(500, str(e))
    except FileNotFoundError as e:
        raise HTTPException(500, f"required asset missing: {e}")
    except Exception as e:
        log.exception("windows-mdm enroll-package build failed")
        raise HTTPException(500, f"build failed: {e}")

    handle = package.stash(built.ppkg_bytes)
    expires_at = datetime.utcnow() + DOWNLOAD_TTL

    with db.session() as s:
        s.add(db.WindowsEnrollToken(
            token=handle,
            wg_ip=ip,
            package_id=built.package_id,
            created_at=datetime.utcnow(),
            expires_at=expires_at,
            revoke=False,
        ))
        s.commit()

    def mark_built(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    prev = d.windows_mdm
                    d.windows_mdm = WindowsMdmState(
                        status=prev.status if prev and prev.status == "enrolled" else "pending",
                        package_id=built.package_id,
                        package_version=built.package_version,
                        conf_sha256=built.conf_sha256,
                        enrolled_at=prev.enrolled_at if prev else None,
                        last_built_at=datetime.utcnow(),
                    )
    store.mutate(mark_built)

    return PackageResponse(
        download_url=f"/dl/windows-mdm/package.zip?t={handle}",
        package_id=built.package_id,
        package_version=built.package_version,
        signed=built.signed,
        expires_at=expires_at,
    )


@router.post("/api/devices/{ip}/windows-mdm/enroll-package")
def build_enroll_package(ip: str, request: Request) -> PackageResponse:
    return _build_enroll_package(ip, request)


@router.post("/api/dl/{code}/windows-mdm/enroll-package")
def build_enroll_package_by_code(code: str, request: Request) -> PackageResponse:
    _, device = api_shortlinks.device_for_code(code)
    return _build_enroll_package(device.wg_ip, request)


# --- Public: download the built package ------------------------------------


@router.get("/devices/{ip}/windows-mdm/package.zip")
@router.get("/devices/{ip}/windows-mdm/package.ppkg")  # legacy URL; same handler
def download_package(ip: str, t: str) -> Response:
    """Single-use download. The token is part of the URL the parent
    copies onto the kid's PC, so no cookie auth — but the token is opaque
    and one-shot."""
    now = datetime.utcnow()
    with db.session() as s:
        row = s.exec(
            select(db.WindowsEnrollToken).where(db.WindowsEnrollToken.token == t)
        ).first()
        if not row:
            raise HTTPException(404, "unknown download token")
        if row.wg_ip != ip:
            raise HTTPException(404, "token does not match device")
        if row.used_at is not None:
            raise HTTPException(410, "download token already used")
        if row.expires_at < now:
            raise HTTPException(410, "download token expired")
        # Snapshot the fields we need before commit closes the session and
        # the ORM instance detaches (see rules-svc/CLAUDE.md "detached-instance").
        is_revoke = bool(row.revoke)
        row.used_at = now
        s.add(row)
        s.commit()

    try:
        blob = package.unstash(t)
    except FileNotFoundError:
        raise HTTPException(410, "package no longer available")

    filename = f"gdlf-{'uninstall' if is_revoke else 'install'}-{ip}.zip"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/dl/windows-mdm/package.zip")
def download_package_by_token(t: str) -> Response:
    """Single-use download without leaking the device IP in the URL."""
    now = datetime.utcnow()
    with db.session() as s:
        row = s.exec(
            select(db.WindowsEnrollToken).where(db.WindowsEnrollToken.token == t)
        ).first()
        if not row:
            raise HTTPException(404, "unknown download token")
        if row.used_at is not None:
            raise HTTPException(410, "download token already used")
        if row.expires_at < now:
            raise HTTPException(410, "download token expired")
        is_revoke = bool(row.revoke)
        row.used_at = now
        s.add(row)
        s.commit()

    try:
        blob = package.unstash(t)
    except FileNotFoundError:
        raise HTTPException(410, "package no longer available")

    filename = f"gdlf-{'uninstall' if is_revoke else 'install'}.zip"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Admin: parent attests the package applied -----------------------------


@router.post("/api/devices/{ip}/windows-mdm/mark-enrolled")
def mark_enrolled(ip: str) -> dict:
    """Confirm the most recently-built package applied successfully.

    Windows has no live check-in equivalent of Apple's /mdm/checkin or
    Android's `devices.list` poll, so the dashboard surfaces a
    "Mark applied" button for the parent to press once they've run the
    .ppkg on the kid's PC.

    If the previous state was a `revoked` build, this flips to `revoked`;
    otherwise it flips to `enrolled`. The `WindowsEnrollToken.revoke` flag
    on the most recent token tells us which.
    """
    _, _, device = _device_or_404(ip)
    if not device.windows_mdm:
        raise HTTPException(409, "no Windows enrolment package built for this device")

    with db.session() as s:
        row = s.exec(
            select(db.WindowsEnrollToken)
            .where(db.WindowsEnrollToken.wg_ip == ip)
            .order_by(db.WindowsEnrollToken.created_at.desc())
        ).first()
        # Snapshot before session exits, see the detached-instance gotcha.
        is_revoke = bool(row and row.revoke)

    def confirm(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip != ip or not d.windows_mdm:
                    continue
                if is_revoke:
                    d.windows_mdm = None
                else:
                    d.windows_mdm = d.windows_mdm.model_copy(update={
                        "status": "enrolled",
                        "enrolled_at": d.windows_mdm.enrolled_at or datetime.utcnow(),
                    })
    store.mutate(confirm)
    return {"ok": True, "status": "revoked" if is_revoke else "enrolled"}


@router.post("/api/dl/{code}/windows-mdm/mark-enrolled")
def mark_enrolled_by_code(code: str) -> dict:
    _, device = api_shortlinks.device_for_code(code)
    return mark_enrolled(device.wg_ip)


# --- Admin: build a revocation package -------------------------------------


@router.delete("/api/devices/{ip}/windows-mdm")
def build_revoke_package(ip: str) -> PackageResponse:
    """Generate a matching un-enrolment .ppkg. The parent applies this on
    the kid's PC; it reverses everything apply.ps1 did.

    State stays at `enrolled` until the parent calls mark-enrolled to
    confirm the revoke ran — at which point the WindowsMdmState is
    cleared from the device entirely.
    """
    _, kid, device = _device_or_404(ip)
    if not device.windows_mdm:
        raise HTTPException(409, "device not Windows-enrolled")

    try:
        built = package.build_revoke_ppkg(kid=kid, device=device)
    except package.PackagingError as e:
        raise HTTPException(500, str(e))
    except Exception as e:
        log.exception("windows-mdm revoke-package build failed")
        raise HTTPException(500, f"build failed: {e}")

    handle = package.stash(built.ppkg_bytes)
    expires_at = datetime.utcnow() + DOWNLOAD_TTL

    with db.session() as s:
        s.add(db.WindowsEnrollToken(
            token=handle,
            wg_ip=ip,
            package_id=built.package_id,
            created_at=datetime.utcnow(),
            expires_at=expires_at,
            revoke=True,
        ))
        s.commit()

    def mark_pending_revoke(cfg):
        for k in cfg.kids:
            for d in k.devices:
                if d.wg_ip != ip or not d.windows_mdm:
                    continue
                d.windows_mdm = d.windows_mdm.model_copy(update={
                    "status": "revoked",
                    "package_version": built.package_version,
                    "last_built_at": datetime.utcnow(),
                })
    store.mutate(mark_pending_revoke)

    return PackageResponse(
        download_url=f"/dl/windows-mdm/package.zip?t={handle}",
        package_id=built.package_id,
        package_version=built.package_version,
        signed=built.signed,
        expires_at=expires_at,
    )
