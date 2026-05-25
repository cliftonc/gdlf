"""Pydantic models that describe the kids.yaml schema.

This is the single contract between the dashboard, the YAML file, the
nftables reconciler, the AdGuard sync loop, and the mitmproxy addon.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["ios", "android", "chromeos", "windows", "macos", "linux", "other"]
RuleAction = Literal["block", "allow", "flag"]
DayKind = Literal["weekday", "weekend"]


MdmStatus = Literal["pending", "enrolled", "checked_out"]


class MdmState(BaseModel):
    """Per-device MDM enrolment state. Absent until the device is enrolled
    (or has a pending enrolment token outstanding).

    `identity_cn` is the CommonName we put on the device's identity cert,
    used to look the device back up on every /mdm/checkin and /mdm/server
    request via the X-Mdm-Client-Subject header that Caddy forwards.

    `udid` + `push_token` + `push_magic` are populated by Apple during the
    initial Authenticate / TokenUpdate check-ins.
    """
    model_config = ConfigDict(extra="forbid")
    identity_cn: str
    identity_cert_serial: str  # hex; useful for revocation tracking later
    status: MdmStatus = "pending"
    udid: str | None = None
    push_token: str | None = None       # base64 — used for APNs wakeups
    push_magic: str | None = None
    push_cert_topic: str | None = None  # com.apple.mgmt.External.<uuid> from APNs cert UID
    supervised: bool = False
    enrolled_at: datetime | None = None
    last_checkin_at: datetime | None = None


AndroidMdmStatus = Literal["pending", "active", "disabled", "deleted"]


WindowsMdmStatus = Literal["pending", "enrolled", "revoked"]


class WindowsMdmState(BaseModel):
    """Per-device Windows enrolment state.

    Asymmetric vs Apple/Android: there's no live two-way channel after the
    provisioning package is applied. The .ppkg is a one-shot installer that
    drops the CA + WireGuard + a SYSTEM reconcile task on the kid's PC. The
    parent attests "applied" from the dashboard once they've run it.

      * `package_id`     — GUID baked into customizations.xml as `<ID>`.
                           Stays stable across re-issues so a fresh .ppkg
                           replaces (rather than stacks with) the old one.
      * `package_version`— bumped on every (re-)build; surfaces in
                           customizations.xml's `<Version>` element.
      * `conf_sha256`    — hash of the per-kid wg-quick conf baked into
                           the package. The reconcile.ps1 script compares
                           this against the on-disk conf and rewrites if
                           the file has drifted.
    """
    model_config = ConfigDict(extra="forbid")
    status: WindowsMdmStatus = "pending"
    package_id: str
    package_version: str
    conf_sha256: str
    enrolled_at: datetime | None = None
    last_built_at: datetime | None = None


class AndroidMdmState(BaseModel):
    """Per-device Android Management API (AMAPI) enrolment state.

    Unlike Apple MDM, devices talk to Google directly — we only call AMAPI
    on the side. So this state is mostly a record of names + status mirrored
    from Google's view of the device:

      * `enrollment_token_name` — `enterprises/{N}/enrollmentTokens/{id}`
        from `enterprises.enrollmentTokens.create`, before the device enrols.
      * `policy_name`           — `enterprises/{N}/policies/{kid_device}`,
        rebuilt + patched whenever kids.yaml changes.
      * `device_name`           — `enterprises/{N}/devices/{id}` once Google
        notifies us (we discover via the periodic devices.list/get poll).
      * `status`                — Google's `Device.state`, lowercased.
      * `applied_policy_version`— `Device.appliedPolicyVersion`; used to
        confirm a policy update has propagated.
    """
    model_config = ConfigDict(extra="forbid")
    enrollment_token_name: str | None = None
    policy_name: str
    device_name: str | None = None
    status: AndroidMdmStatus = "pending"
    model: str | None = None
    enrolled_at: datetime | None = None
    last_status_at: datetime | None = None
    applied_policy_version: str | None = None


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    platform: Platform
    wg_ip: str
    wg_public_key: str | None = None
    mitm_ca_installed: bool = False
    # Parent-toggled "off switch". When true, the nftables sidecar puts this
    # device's wg_ip into blocked_clients regardless of schedule.
    manual_block: bool = False
    # Optional MDM enrolment state (Apple supervised devices only, currently).
    mdm: MdmState | None = None
    # Optional Android Management API enrolment state.
    android_mdm: AndroidMdmState | None = None
    # Optional Windows enrolment state (provisioning-package based — see
    # gdlf.windows_mdm). One-shot, no live channel.
    windows_mdm: WindowsMdmState | None = None


class ScheduleWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: str = Field(
        description="HH:MM-HH:MM in the configured TZ; multiple windows comma-separated",
        examples=["07:00-21:00", "07:00-12:00,14:00-21:00"],
    )


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekday: ScheduleWindow = ScheduleWindow(allowed="00:00-23:59")
    weekend: ScheduleWindow = ScheduleWindow(allowed="00:00-23:59")


class URLRule(BaseModel):
    """A single mitmproxy-level rule. `match` is a host+path glob; `query` is
    an optional regex against the query string."""
    model_config = ConfigDict(extra="forbid")
    action: RuleAction
    match: str
    query: str | None = None
    flag: bool = False
    note: str | None = None


class Kid(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    age: int | None = None
    devices: list[Device] = []
    schedule: Schedule = Schedule()
    # AdGuard "blocked services" IDs (e.g. "tiktok", "youtube"). The catalog
    # comes from AdGuard (GET /control/blocked_services/all); we just persist
    # which ones are toggled on for this kid and push them into each AdGuard
    # client's per-client `blocked_services` array.
    blocked_apps: list[str] = []
    url_rules: list[URLRule] = []
    keyword_flags: list[str] = []
    # If set and in the future (local time), schedule-based blocks are
    # suspended until this moment — i.e. "bonus time" beyond normal hours.
    bonus_until: datetime | None = None
    # Parent-toggled "off switch" for the whole kid. When true, every one of
    # their devices is added to nftables' blocked_clients (overrides bonus).
    manual_block: bool = False
    # Hosts (fnmatch globs) that mitmproxy should let through untouched — for
    # pinned-cert apps that refuse our CA. Matched against TLS SNI.
    mitm_passthrough_hosts: list[str] = []
    # Registrable domains (eTLD+1, e.g. "tiktok.com") the parent explicitly
    # opted *out* of auto-passthrough. New TLS failures matching one of these
    # are NOT auto-added to `mitm_passthrough_hosts`; toggling a group off in
    # the Passthrough tab records the registrable here so the next retry from
    # the device doesn't re-enable it.
    mitm_passthrough_disabled: list[str] = []


class KidsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kids: list[Kid] = []

    def kid(self, name: str) -> Kid | None:
        return next((k for k in self.kids if k.name == name), None)

    def device_by_ip(self, ip: str) -> tuple[Kid, Device] | None:
        for k in self.kids:
            for d in k.devices:
                if d.wg_ip == ip:
                    return k, d
        return None

    def all_devices(self) -> list[tuple[Kid, Device]]:
        return [(k, d) for k in self.kids for d in k.devices]

    def device_by_mdm_identity(self, cn: str) -> tuple[Kid, Device] | None:
        for k in self.kids:
            for d in k.devices:
                if d.mdm and d.mdm.identity_cn == cn:
                    return k, d
        return None
