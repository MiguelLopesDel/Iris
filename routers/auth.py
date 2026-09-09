"""Invite-only account and session endpoints."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request

from core.auth import hash_password, verify_password
from core.users_db import create_user, get_user_by_username

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _public_user(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    users_path = request.app.state.users_db_path
    user = get_user_by_username(users_path, username)
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Usuário ou senha inválidos")
    request.session.clear()
    request.session.update({"user_id": user.id, "session_version": user.session_version})
    return {"ok": True, "user": _public_user(user)}


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
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
