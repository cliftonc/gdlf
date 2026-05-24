"""Cookie auth endpoints for the SPA."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import auth
from .settings import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginBody) -> Response:
    if not auth.check_password(body.password):
        return JSONResponse({"error": "invalid password"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        auth.COOKIE,
        auth.make_token(),
        max_age=auth.MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/logout")
def logout() -> Response:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@router.get("/me")
def me(request: Request) -> dict:
    # Middleware has already enforced auth when admin_password is set.
    return {
        "authenticated": True,
        "auth_required": bool(settings.admin_password),
    }
