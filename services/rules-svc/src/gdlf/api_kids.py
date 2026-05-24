"""Kids CRUD + schedule/bonus/block endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import store, wg
from .dto import kid_detail_dto, kid_summary_dto
from .schema import Kid, Schedule, ScheduleWindow

router = APIRouter(prefix="/api/kids", tags=["kids"])


class CreateKidBody(BaseModel):
    name: str
    age: int | None = None
    schedule_weekday: str = "07:00-21:00"
    schedule_weekend: str = "08:00-22:00"


class ScheduleBody(BaseModel):
    weekday: str
    weekend: str


class BonusBody(BaseModel):
    minutes: int = Field(ge=1, le=24 * 60)


class BlockBody(BaseModel):
    blocked: bool


class PassthroughBody(BaseModel):
    hosts: list[str]


class PassthroughAddBody(BaseModel):
    host: str


class BlockedAppsBody(BaseModel):
    blocked_apps: list[str]


@router.get("")
def list_kids() -> dict:
    cfg = store.load()
    handshakes = wg.wg_show_handshakes()
    return {"kids": [kid_summary_dto(k, handshakes) for k in cfg.kids]}


@router.post("", status_code=201)
def create_kid(body: CreateKidBody) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name required")

    def add(cfg):
        if cfg.kid(name):
            raise HTTPException(400, f"kid {name!r} already exists")
        try:
            kid = Kid(
                name=name,
                age=body.age,
                schedule=Schedule(
                    weekday=ScheduleWindow(allowed=body.schedule_weekday.strip()),
                    weekend=ScheduleWindow(allowed=body.schedule_weekend.strip()),
                ),
            )
        except Exception as e:
            raise HTTPException(400, f"invalid kid: {e}")
        cfg.kids.append(kid)

    store.mutate(add)
    cfg = store.load(force=True)
    return {"kid": kid_detail_dto(cfg.kid(name), wg.wg_show_handshakes())}


@router.get("/{name}")
def get_kid(name: str) -> dict:
    cfg = store.load()
    kid = cfg.kid(name)
    if not kid:
        raise HTTPException(404, f"unknown kid {name}")
    return {"kid": kid_detail_dto(kid, wg.wg_show_handshakes())}


@router.put("/{name}/schedule")
def update_schedule(name: str, body: ScheduleBody) -> dict:
    try:
        Schedule(
            weekday=ScheduleWindow(allowed=body.weekday.strip()),
            weekend=ScheduleWindow(allowed=body.weekend.strip()),
        )
    except Exception as e:
        raise HTTPException(400, f"invalid schedule: {e}")

    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.schedule.weekday.allowed = body.weekday.strip()
        kid.schedule.weekend.allowed = body.weekend.strip()

    store.mutate(upd)
    cfg = store.load(force=True)
    kid = cfg.kid(name)
    return {
        "schedule": {
            "weekday": kid.schedule.weekday.allowed,
            "weekend": kid.schedule.weekend.allowed,
        }
    }


@router.post("/{name}/bonus")
def grant_bonus(name: str, body: BonusBody) -> dict:
    def add(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        now = datetime.now()
        base = kid.bonus_until if kid.bonus_until and kid.bonus_until > now else now
        kid.bonus_until = base + timedelta(minutes=body.minutes)

    store.mutate(add)
    cfg = store.load(force=True)
    kid = cfg.kid(name)
    return {"bonus_until": kid.bonus_until.isoformat() if kid.bonus_until else None}


@router.delete("/{name}/bonus")
def clear_bonus(name: str) -> dict:
    def clr(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.bonus_until = None

    store.mutate(clr)
    return {"bonus_until": None}


@router.put("/{name}/block")
def block_kid(name: str, body: BlockBody) -> dict:
    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.manual_block = body.blocked

    store.mutate(upd)
    return {"manual_block": body.blocked}


@router.put("/{name}/passthrough")
def set_passthrough(name: str, body: PassthroughBody) -> dict:
    cleaned = sorted({h.strip().lower() for h in body.hosts if h and h.strip()})

    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.mitm_passthrough_hosts = cleaned

    store.mutate(upd)
    return {"mitm_passthrough_hosts": cleaned}


@router.post("/{name}/passthrough")
def add_passthrough(name: str, body: PassthroughAddBody) -> dict:
    host = body.host.strip().lower()
    if not host:
        raise HTTPException(400, "host required")

    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        if host not in kid.mitm_passthrough_hosts:
            kid.mitm_passthrough_hosts = sorted([*kid.mitm_passthrough_hosts, host])

    store.mutate(upd)
    cfg = store.load(force=True)
    return {"mitm_passthrough_hosts": list(cfg.kid(name).mitm_passthrough_hosts)}


@router.delete("/{name}/passthrough/{host}")
def remove_passthrough(name: str, host: str) -> dict:
    target = host.strip().lower()

    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.mitm_passthrough_hosts = [h for h in kid.mitm_passthrough_hosts if h != target]

    store.mutate(upd)
    cfg = store.load(force=True)
    return {"mitm_passthrough_hosts": list(cfg.kid(name).mitm_passthrough_hosts)}


@router.put("/{name}/blocked-apps")
def set_blocked_apps(name: str, body: BlockedAppsBody) -> dict:
    cleaned = sorted({s.strip() for s in body.blocked_apps if s and s.strip()})

    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.blocked_apps = cleaned

    store.mutate(upd)
    return {"blocked_apps": cleaned}


@router.delete("/{name}", status_code=204)
def delete_kid(name: str):
    def rm(cfg):
        cfg.kids = [k for k in cfg.kids if k.name != name]

    store.mutate(rm)
    wg.write_wg0_conf(store.load(force=True))
    wg.reload_wg()
    return None
