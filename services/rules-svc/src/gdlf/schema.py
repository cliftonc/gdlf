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
