"""Invite-only account and session endpoints."""
from __future__ import annotations

import math
import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request

from core.auth import hash_password, verify_password
from core.device_tokens import issue_access_token, new_refresh_token, token_hash
from core.login_throttle import LoginThrottle
from core.password_policy import PasswordRejected, check_new_password
from core.users_db import (
    create_device,
    create_user,
    get_device,
    get_user_by_id,
    get_user_by_username,
    rotate_device_refresh_token,
)
from core.web_sessions import end_web_session, start_web_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _public_user(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


def _device_tokens(request: Request, user, device) -> dict:
    refresh = new_refresh_token()
    rotate_device_refresh_token(request.app.state.users_db_path, device.id, token_hash(refresh))
    return {
        "access_token": issue_access_token(request.app.state.auth_secret, user.id, user.session_version, device.id, device.token_version),
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": 900,
        "device_id": device.id,
    }


def _throttle(request: Request) -> LoginThrottle:
    throttle = getattr(request.app.state, "login_throttle", None)
    if throttle is None:
        throttle = LoginThrottle()
        request.app.state.login_throttle = throttle
    return throttle


def _check_credentials(request: Request, username: str, password: str):
    """The user for these credentials, or an HTTP error; failures slow the next attempt."""
    throttle = _throttle(request)
    user = get_user_by_username(request.app.state.users_db_path, username)
    known = user is not None
    wait = throttle.retry_after(username, known=known)
    if wait > 0:
        seconds = math.ceil(wait)
        raise HTTPException(
            429,
            f"Muitas tentativas de login com esse usuário. Tente de novo em {_wait_text(seconds)}.",
            headers={"Retry-After": str(seconds)},
        )
    if user is None or not verify_password(password, user.password_hash):
        throttle.record_failure(username, known=known)
        raise HTTPException(401, "Usuário ou senha inválidos")
    throttle.record_success(username)
    return user


def _wait_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} segundo{'s' if seconds != 1 else ''}"
    minutes = math.ceil(seconds / 60)
    return f"{minutes} minuto{'s' if minutes != 1 else ''}"


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    users_path = request.app.state.users_db_path
    user = _check_credentials(request, username, password)
    start_web_session(request.session, users_path, user, request.headers.get("user-agent", ""))
    return {"ok": True, "user": _public_user(user)}


@router.post("/devices/login")
async def device_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    device_name: str = Form(...),
    platform: str = Form("android"),
):
    user = _check_credentials(request, username, password)
    refresh = new_refresh_token()
    device = create_device(request.app.state.users_db_path, user.id, device_name, platform, token_hash(refresh))
    return {
        "user": _public_user(user),
        "access_token": issue_access_token(request.app.state.auth_secret, user.id, user.session_version, device.id, device.token_version),
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": 900,
        "device_id": device.id,
    }


@router.post("/devices/refresh")
async def refresh_device_token(request: Request, refresh_token: str = Form(...), device_id: str = Form(...)):
    device = get_device(request.app.state.users_db_path, device_id)
    if device is None or device.revoked_at or device.refresh_token_hash != token_hash(refresh_token):
        raise HTTPException(401, "Sessão do dispositivo inválida")
    user = get_user_by_id(request.app.state.users_db_path, device.user_id)
    if user is None:
        raise HTTPException(401, "Sessão do dispositivo inválida")
    return _device_tokens(request, user, device)


@router.post("/logout")
async def logout(request: Request):
    end_web_session(request.session, request.app.state.users_db_path)
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Sessão inválida")
    return _public_user(user)


@router.post("/users")
async def create_invited_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
    is_admin: bool = Form(False),
):
    actor = getattr(request.state, "iris_user", None)
    if actor is None or not actor.is_admin:
        raise HTTPException(403, "Apenas administradores podem criar contas")
    try:
        check_new_password(password, username)
    except PasswordRejected as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        user = create_user(
            request.app.state.users_db_path,
            request.app.state.data_dir,
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            is_admin=is_admin,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Esse nome de usuário já existe") from exc
    return {"ok": True, "user": _public_user(user)}
