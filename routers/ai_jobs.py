"""HTTP adapter for processing computers (AI workers; see core/ai_jobs.py).

A worker is a device session. An administrator's worker serves every library
whose owner lets their photos be processed (core/ai_policy.py); anyone else's
serves only their own. Work is handed out only while the instance processes AI
on workers. A job id carries the library: ``<user_id>.<media_id>``.

The worker claims a batch, downloads each original, posts a result or a failure
per item, and flushes now and then so the search indices and the people
grouping of the libraries it touched are rebuilt once, not once per photo.
"""
from __future__ import annotations

import contextlib
import itertools
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from core import ai_jobs, ai_policy, instance_settings, model_migration
from core.users_db import get_user_by_id, list_users

router = APIRouter(prefix="/api/ai-jobs", tags=["ai-jobs"])
_rotation = itertools.count()


def _worker(request: Request):
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    device_id = getattr(request.state, "iris_device_id", None)
    if not device_id:
        raise HTTPException(403, "Use uma sessão de dispositivo para processar mídia")
    return user, device_id


def _users_db(request: Request):
    return request.app.state.users_db_path


def _libraries(request: Request, actor) -> list:
    """The accounts this worker may process now, by policy and consent."""
    path = _users_db(request)
    candidates = list_users(path) if actor.is_admin else [actor]
    return [user for user in candidates if ai_policy.for_workers(path, user)]


def _models(user) -> tuple[str, str]:
    """(model the worker must produce, migration target or "")."""
    target = model_migration.migration_target(user.db_path, user.model_name)
    return target or model_migration.library_model(user.db_path, user.model_name), target


@contextlib.contextmanager
def _library(user):
    conn = sqlite3.connect(user.db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _run(user, operation):
    try:
        with _library(user) as conn:
            return operation(conn)
    except ai_jobs.AiJobError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


def _job(request: Request, actor, job_id: str):
    """The library and item a job id names, if this worker may touch it."""
    try:
        user_id, media_id = (int(part) for part in job_id.split(".", 1))
    except ValueError as exc:
        raise HTTPException(404, "Trabalho desconhecido") from exc
    if user_id != actor.id and not actor.is_admin:
        raise HTTPException(404, "Trabalho desconhecido")
    user = get_user_by_id(_users_db(request), user_id)
    if user is None or not ai_policy.for_workers(_users_db(request), user):
        raise HTTPException(404, "Trabalho desconhecido")
    return user, media_id


def _activity(request: Request, device_id: str, **changes) -> None:
    """In-memory record of what each processing computer did, for the admin page."""
    stats = request.app.state.__dict__.setdefault("ai_worker_activity", {})
    entry = stats.setdefault(device_id, {"done": 0, "failed": 0, "since": time.time(),
                                         "last_seen": 0.0, "touched": set()})
    entry["last_seen"] = time.time()
    for key, value in changes.items():
        if key == "touched":
            entry["touched"].add(value)
        else:
            entry[key] += value


def _options(request: Request) -> dict:
    settings = instance_settings.ai_settings(_users_db(request))
    return {"florence_ocr": settings.florence_ocr}


def _pending(libraries, only: str = "") -> int:
    total = 0
    for user in libraries:
        _model, target = _models(user)
        total += _run(user, lambda conn, t=target: ai_jobs.pending_count(conn, t, only))
    return total


@router.get("/status")
async def status(request: Request):
    actor, device_id = _worker(request)
    _activity(request, device_id)

    def build():
        libraries = _libraries(request, actor)
        body = {
            "mode": instance_settings.ai_settings(_users_db(request)).mode,
            "libraries": len(libraries),
            "pending": _pending(libraries),
            "without_ai": _pending(libraries, "new"),
            "options": _options(request),
        }
        done = total = 0
        targets = set()
        for user in libraries:
            _model, target = _models(user)
            if target:
                targets.add(target)
                d, t = _run(user, lambda conn, t=target: model_migration.progress(conn, t))
                done, total = done + d, total + t
        if targets:
            body["migration"] = {"target": sorted(targets)[0], "done": done, "total": total}
        if len(libraries) == 1:
            body["model_name"] = _models(libraries[0])[0]
        return body

    return await run_in_threadpool(build)


@router.post("/claim")
async def claim(request: Request, limit: int = 8, only: str = ""):
    actor, device_id = _worker(request)
    _activity(request, device_id)

    def build():
        libraries = _libraries(request, actor)
        jobs: list[dict] = []
        if libraries:
            # Start each claim at a different library, so none waits behind another.
            start = next(_rotation) % len(libraries)
            for user in libraries[start:] + libraries[:start]:
                if len(jobs) >= limit:
                    break
                model, target = _models(user)
                room = limit - len(jobs)
                claimed = _run(user, lambda conn, t=target, n=room: ai_jobs.claim(
                    conn, device_id, n, target=t, only=only))
                jobs += [
                    {"id": f"{user.id}.{job.media_id}", "filename": job.filename, "size": job.size,
                     "sha256": job.sha256, "model_name": model}
                    for job in claimed
                ]
        body = {"jobs": jobs, "pending": _pending(libraries, only), "options": _options(request)}
        models = {job["model_name"] for job in jobs}
        if len(models) == 1:
            body["model_name"] = models.pop()
        return body

    return await run_in_threadpool(build)


@router.get("/{job_id}/file")
async def download(request: Request, job_id: str):
    actor, device_id = _worker(request)
    user, media_id = _job(request, actor, job_id)
    await run_in_threadpool(_run, user, lambda conn: ai_jobs.require_lease(conn, media_id, device_id))
    original = await run_in_threadpool(request.app.state.original_for_user, user, media_id)
    if original is None:
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(original.path, filename=original.name)


@router.post("/{job_id}/result")
async def result(request: Request, job_id: str):
    actor, device_id = _worker(request)
    user, media_id = _job(request, actor, job_id)
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Resultado inválido") from exc
    model, target = _models(user)
    await run_in_threadpool(
        _run, user,
        lambda conn: ai_jobs.store_result(
            conn, media_id, device_id, model, payload, migrating=bool(target),
            current_model=model_migration.library_model(user.db_path, user.model_name),
        ),
    )
    _activity(request, device_id, done=1, touched=user.id)
    return {"ok": True}


@router.post("/{job_id}/fail")
async def fail(request: Request, job_id: str):
    actor, device_id = _worker(request)
    user, media_id = _job(request, actor, job_id)
    try:
        error = str((await request.json()).get("error", ""))
    except (ValueError, AttributeError):
        error = ""
    await run_in_threadpool(
        _run, user, lambda conn: ai_jobs.record_failure(conn, media_id, device_id, error)
    )
    _activity(request, device_id, failed=1)
    return {"ok": True}


@router.post("/flush")
async def flush(request: Request):
    """Group the new faces, rebuild the indices and reload search, per library touched."""
    _actor, device_id = _worker(request)
    entry = request.app.state.__dict__.get("ai_worker_activity", {}).get(device_id)
    touched = set(entry["touched"]) if entry else set()
    path = _users_db(request)
    libraries = [user for user in (get_user_by_id(path, uid) for uid in touched) if user is not None]

    def rebuild(user) -> bool:
        from core import faces

        _model, target = _models(user)

        def operation(conn):
            if faces.has_face_tables(conn):
                faces.cluster_faces(conn)
            switched = bool(target) and model_migration.complete_if_ready(conn, target)
            ai_jobs.rebuild_vector_indices(user.db_path)
            return switched

        return _run(user, operation)

    switched = False
    registry = getattr(request.app.state, "backend_registry", None)
    for user in libraries:
        switched = await run_in_threadpool(rebuild, user) or switched
        if registry is not None:
            registry.refresh(user.id)
    if entry:
        entry["touched"] -= touched
    return {"ok": True, "switched": switched, "libraries": len(libraries)}
