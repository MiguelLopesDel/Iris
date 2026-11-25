"""HTTP adapter for remote AI workers (see core/ai_jobs.py).

A worker is a device session of one account: it sees only that library, as
the phone does. It claims a batch, downloads each original, posts a result or
a failure per item, and flushes at the end of the batch so the search indices
and the people grouping are rebuilt once, not once per photo.
"""
from __future__ import annotations

import contextlib
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from core import ai_jobs, model_migration

router = APIRouter(prefix="/api/ai-jobs", tags=["ai-jobs"])


def _worker(request: Request):
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    device_id = getattr(request.state, "iris_device_id", None)
    if not device_id:
        raise HTTPException(403, "Use uma sessão de dispositivo para processar mídia")
    return user, device_id


@contextlib.contextmanager
def _library(user):
    conn = sqlite3.connect(user.db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _models(user) -> tuple[str, str]:
    """(model the worker must produce, migration target or "")."""
    target = model_migration.migration_target(user.db_path, user.model_name)
    return target or model_migration.library_model(user.db_path, user.model_name), target


def _run(user, operation):
    try:
        with _library(user) as conn:
            return operation(conn)
    except ai_jobs.AiJobError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


@router.get("/status")
async def status(request: Request):
    user, _device_id = _worker(request)
    model, target = _models(user)

    def operation(conn):
        body = {
            "model_name": model,
            "pending": ai_jobs.pending_count(conn, target),
            "without_ai": ai_jobs.pending_count(conn, target, "new"),
        }
        if target:
            done, total = model_migration.progress(conn, target)
            body["migration"] = {"target": target, "done": done, "total": total}
        return body

    return await run_in_threadpool(_run, user, operation)


@router.post("/claim")
async def claim(request: Request, limit: int = 8, only: str = ""):
    user, device_id = _worker(request)
    model, target = _models(user)

    def operation(conn):
        jobs = ai_jobs.claim(conn, device_id, limit, target=target, only=only)
        return {
            "model_name": model,
            "pending": ai_jobs.pending_count(conn, target, only),
            "jobs": [
                {"id": job.media_id, "filename": job.filename, "size": job.size, "sha256": job.sha256}
                for job in jobs
            ],
        }

    return await run_in_threadpool(_run, user, operation)


@router.get("/{media_id}/file")
async def download(request: Request, media_id: int):
    user, device_id = _worker(request)
    await run_in_threadpool(_run, user, lambda conn: ai_jobs.require_lease(conn, media_id, device_id))
    # The same lookup shared spaces use: only this account's catalogued files.
    original = request.app.state.private_original_for(media_id)
    if original is None:
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(original.path, filename=original.name)


@router.post("/{media_id}/result")
async def result(request: Request, media_id: int):
    user, device_id = _worker(request)
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
    return {"ok": True}


@router.post("/{media_id}/fail")
async def fail(request: Request, media_id: int):
    user, device_id = _worker(request)
    try:
        error = str((await request.json()).get("error", ""))
    except (ValueError, AttributeError):
        error = ""
    await run_in_threadpool(
        _run, user, lambda conn: ai_jobs.record_failure(conn, media_id, device_id, error)
    )
    return {"ok": True}


@router.post("/flush")
async def flush(request: Request):
    """After a batch: group the new faces, rebuild the indices, reload search."""
    user, _device_id = _worker(request)
    _model, target = _models(user)

    def operation(conn):
        from core import faces

        if faces.has_face_tables(conn):
            faces.cluster_faces(conn)
        switched = bool(target) and model_migration.complete_if_ready(conn, target)
        ai_jobs.rebuild_vector_indices(user.db_path)
        return {"ok": True, "switched": switched,
                "pending": ai_jobs.pending_count(conn, "" if switched else target)}

    response = await run_in_threadpool(_run, user, operation)
    registry = getattr(request.app.state, "backend_registry", None)
    if registry is not None:
        registry.refresh(user.id)
    return response
