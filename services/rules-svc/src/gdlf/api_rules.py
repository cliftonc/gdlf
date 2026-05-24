"""URL-rule CRUD + suggestion + read-only library."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import rules as rules_mod
from . import store
from .dto import rule_dto
from .schema import RuleAction, URLRule

router = APIRouter(tags=["rules"])


class CreateRuleBody(BaseModel):
    action: RuleAction
    match: str
    query: str | None = None
    flag: bool = False
    note: str | None = None


class MoveBody(BaseModel):
    dir: str  # "up" | "down"


@router.post("/api/kids/{name}/rules", status_code=201)
def add_rule(name: str, body: CreateRuleBody) -> dict:
    _validate_query_regex(body.query)

    def add(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        kid.url_rules.append(
            URLRule(
                action=body.action,
                match=body.match.strip(),
                query=(body.query.strip() if body.query else None) or None,
                flag=body.flag,
                note=(body.note.strip() if body.note else None) or None,
            )
        )

    store.mutate(add)
    cfg = store.load(force=True)
    kid = cfg.kid(name)
    new_idx = len(kid.url_rules) - 1
    return {"rule": rule_dto(kid.url_rules[new_idx]), "index": new_idx}


def _validate_query_regex(query: str | None) -> None:
    if query and query.strip():
        try:
            re.compile(query.strip())
        except re.error as e:
            raise HTTPException(
                400,
                f"invalid query regex: {e}. Use Python regex syntax "
                "(e.g. 'evil' to match the word, not '*evil*').",
            )


@router.put("/api/kids/{name}/rules/{idx}")
def update_rule(name: str, idx: int, body: CreateRuleBody) -> dict:
    _validate_query_regex(body.query)

    def upd(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        if not (0 <= idx < len(kid.url_rules)):
            raise HTTPException(404, "rule out of range")
        kid.url_rules[idx] = URLRule(
            action=body.action,
            match=body.match.strip(),
            query=(body.query.strip() if body.query else None) or None,
            flag=body.flag,
            note=(body.note.strip() if body.note else None) or None,
        )

    store.mutate(upd)
    cfg = store.load(force=True)
    kid = cfg.kid(name)
    return {"rule": rule_dto(kid.url_rules[idx]), "index": idx}


@router.delete("/api/kids/{name}/rules/{idx}", status_code=204)
def delete_rule(name: str, idx: int):
    def rm(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        if 0 <= idx < len(kid.url_rules):
            del kid.url_rules[idx]

    store.mutate(rm)
    return None


@router.patch("/api/kids/{name}/rules/{idx}/move")
def move_rule(name: str, idx: int, body: MoveBody) -> dict:
    if body.dir not in {"up", "down"}:
        raise HTTPException(400, "dir must be up or down")

    def mv(cfg):
        kid = cfg.kid(name)
        if not kid:
            raise HTTPException(404, "unknown kid")
        n = len(kid.url_rules)
        if not (0 <= idx < n):
            return
        new = idx - 1 if body.dir == "up" else idx + 1
        if 0 <= new < n:
            kid.url_rules[idx], kid.url_rules[new] = kid.url_rules[new], kid.url_rules[idx]

    store.mutate(mv)
    cfg = store.load(force=True)
    kid = cfg.kid(name)
    return {"rules": [rule_dto(r) for r in kid.url_rules]}


@router.get("/api/rules/suggest")
def suggest(host: str = "", path: str = "") -> dict:
    return {"suggested": rules_mod.suggest_match(host, path)}


@router.get("/api/rules/library")
def library() -> dict:
    cfg = store.load()
    return {
        "blocklists": {
            name: {"description": bl.description, "sources": list(bl.sources)}
            for name, bl in cfg.blocklists.items()
        },
        "apps": {
            name: {"hosts": list(app.hosts), "ip_ranges": list(app.ip_ranges)}
            for name, app in cfg.apps.items()
        },
    }
