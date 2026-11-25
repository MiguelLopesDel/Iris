"""AI processing as the interface shows it: the administrator's view and each account's choice.

The administrator sees where AI runs, the processing computers and what each
did, how much is waiting per account (counts only) and a model change in
progress; the settings themselves are saved through /api/admin/settings. Every
account decides whether its own photos are processed, and can erase what AI
already derived from them.
"""
from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from core import ai_jobs, instance_settings, model_migration
from core.users_db import list_users, set_user_ai_enabled, user_ai_enabled

router = APIRouter(tags=["ai-settings"])


def _user(request: Request):
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    return user


def _library_counts(user) -> dict:
    target = model_migration.migration_target(user.db_path, user.model_name)
    try:
        conn = sqlite3.connect(f"file:{user.db_path}?mode=ro", uri=True, timeout=10)
    except sqlite3.OperationalError:
        return {"items": 0, "without_ai": 0, "migrated": 0}
    try:
        items = conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0]
        without = conn.execute(
            "SELECT COUNT(*) FROM memes WHERE embedding IS NULL OR length(embedding) = 0"
        ).fetchone()[0]
        migrated = 0
        if target:
            try:
                migrated = conn.execute(
                    "SELECT COUNT(*) FROM embedding_next WHERE model_name = ?", (target,)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                migrated = 0
        return {"items": items, "without_ai": without, "migrated": migrated,
                "model": model_migration.library_model(user.db_path, user.model_name)}
    except sqlite3.OperationalError:
        return {"items": 0, "without_ai": 0, "migrated": 0}
    finally:
        conn.close()


def _workers(request: Request) -> list[dict]:
    """Processing computers (devices signed in by the AI worker) and what they did."""
    path = request.app.state.users_db_path
    activity = request.app.state.__dict__.get("ai_worker_activity", {})
    conn = sqlite3.connect(path, timeout=10)
    try:
        rows = conn.execute(
            """SELECT d.id, d.name, d.last_seen_at, u.username FROM devices d
               JOIN users u ON u.id = d.user_id
               WHERE d.platform = 'ai-worker' AND d.revoked_at IS NULL
               ORDER BY d.last_seen_at DESC"""
        ).fetchall()
    finally:
        conn.close()
    now = time.time()
    workers = []
    for device_id, name, last_seen_at, username in rows:
        entry = activity.get(device_id, {})
        hours = max((now - entry.get("since", now)) / 3600, 1 / 60)
        workers.append({
            "id": device_id, "name": name, "account": username,
            "last_seen_at": last_seen_at,
            "active": bool(entry) and now - entry.get("last_seen", 0) < 15 * 60,
            "done": entry.get("done", 0), "failed": entry.get("failed", 0),
            "per_hour": round(entry.get("done", 0) / hours) if entry else 0,
        })
    return workers


@router.get("/api/admin/ai")
async def admin_ai(request: Request):
    actor = _user(request)
    if not actor.is_admin:
        raise HTTPException(403, "Apenas administradores alteram a instalação")

    def build():
        path = request.app.state.users_db_path
        settings = instance_settings.ai_settings(path)
        accounts = []
        for user in list_users(path):
            counts = _library_counts(user)
            accounts.append({"username": user.username, "display_name": user.display_name,
                             "ai_enabled": user_ai_enabled(path, user.id), **counts})
        return {
            "mode": settings.mode,
            "florence_ocr": settings.florence_ocr,
            "model_next": settings.model_next,
            "models_next": list(instance_settings.AI_NEXT_MODELS),
            "server_can_process": bool(request.app.state.load_model),
            "accounts": accounts,
            "workers": _workers(request),
        }

    return await run_in_threadpool(build)


class MyAiIn(BaseModel):
    enabled: bool


def _my_state(request: Request, user) -> dict:
    path = request.app.state.users_db_path
    settings = instance_settings.ai_settings(path)
    return {"enabled": user_ai_enabled(path, user.id), "mode": settings.mode,
            **_library_counts(user)}


@router.get("/api/me/ai")
async def my_ai(request: Request):
    user = _user(request)
    return await run_in_threadpool(_my_state, request, user)


@router.put("/api/me/ai")
async def set_my_ai(request: Request, body: MyAiIn):
    user = _user(request)
    await run_in_threadpool(set_user_ai_enabled, request.app.state.users_db_path, user.id, body.enabled)
    return await run_in_threadpool(_my_state, request, user)


@router.post("/api/me/ai/forget")
async def forget_my_ai(request: Request):
    """Erase what AI derived from this account's photos (the photos stay)."""
    user = _user(request)

    def run() -> int:
        conn = sqlite3.connect(user.db_path, timeout=30)
        try:
            forgotten = ai_jobs.forget(conn)
        finally:
            conn.close()
        ai_jobs.rebuild_vector_indices(user.db_path)
        return forgotten

    forgotten = await run_in_threadpool(run)
    registry = getattr(request.app.state, "backend_registry", None)
    if registry is not None:
        registry.refresh(user.id)
    return {"ok": True, "forgotten": forgotten, **await run_in_threadpool(_my_state, request, user)}
