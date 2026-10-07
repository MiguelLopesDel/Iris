"""HTTP adapters for authenticated device synchronization workflows."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from core.sync_db import changes_after
from core.sync_upload_service import SyncUploadError, SyncUploadService
from core.users_db import list_devices, revoke_device

router = APIRouter(prefix="/api/sync", tags=["sync"])
logger = logging.getLogger("iris")


def _user(request: Request):
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    return user


def _upload_service(request: Request) -> SyncUploadService:
    service = getattr(request.app.state, "sync_upload_service", None)
    if service is None:
        # Keeps router use in small test/development apps independent of the
        # full server factory; production wires the service at app creation.
        service = SyncUploadService()
        request.app.state.sync_upload_service = service
    return service


def _log_sync_phase(request: Request, phase: str, started: float, **fields: int | str | bool) -> None:
    """Log aggregate timings without filenames, paths, or upload IDs."""
    phase_ms = round((time.perf_counter() - started) * 1000, 1)
    request_id = getattr(request.state, "request_id", None)
    logger.info(
        "sync_phase_completed phase=%s request_id=%s phase_ms=%.1f fields=%s",
        phase,
        request_id or "-",
        phase_ms,
        " ".join(f"{key}={value}" for key, value in fields.items()),
        extra={
            "event": "sync_phase_completed",
            "request_id": request_id,
            "user_id": getattr(getattr(request.state, "iris_user", None), "id", None),
            "phase": phase,
            "phase_ms": phase_ms,
            **fields,
        },
    )


def _raise_http(exc: SyncUploadError) -> None:
    raise HTTPException(exc.status_code, exc.detail) from exc


def _device_payload(device, current_id: str | None) -> dict:
    return {
        "id": device.id, "name": device.name, "platform": device.platform,
        "revoked": bool(device.revoked_at), "last_seen_at": device.last_seen_at,
        "current": device.id == current_id,
    }


def _connection(request: Request):
    """Open the authenticated user's sync database for read-only route work."""
    return _upload_service(request).open_connection(_user(request))


@router.get("/devices")
async def devices(request: Request):
    user = _user(request)
    current = getattr(request.state, "iris_device_id", None)
    return {"devices": [
        _device_payload(device, current)
        for device in list_devices(request.app.state.users_db_path, user.id)
        if not device.revoked_at
    ]}


@router.delete("/devices/{device_id}")
async def revoke(request: Request, device_id: str):
    user = _user(request)
    if not revoke_device(request.app.state.users_db_path, user.id, device_id):
        raise HTTPException(404, "Dispositivo não encontrado")
    return {"ok": True}


@router.get("/changes")
async def changes(request: Request, cursor: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    def read() -> list:
        with _connection(request) as conn:
            return changes_after(conn, cursor, limit)

    # SQLite blocks; off the event loop, so other requests keep moving.
    rows = await run_in_threadpool(read)
    return {"changes": rows, "next_cursor": rows[-1]["cursor"] if rows else cursor, "has_more": len(rows) == limit}


@router.get("/sources")
async def sources(request: Request):
    def read() -> list:
        with _connection(request) as conn:
            return conn.execute(
                """SELECT s.device_id, s.source_id, s.name, s.relative_path, s.volume,
                          s.media_kind, COUNT(DISTINCT o.media_id)
                   FROM device_sources s
                   LEFT JOIN media_origins o
                     ON o.device_id = s.device_id AND o.source_id = s.source_id
                   GROUP BY s.device_id, s.source_id
                   ORDER BY lower(s.name), s.media_kind"""
            ).fetchall()

    rows = await run_in_threadpool(read)
    return {"sources": [
        {"device_id": row[0], "source_id": row[1], "name": row[2],
         "relative_path": row[3], "volume": row[4], "media_kind": row[5],
         "item_count": int(row[6])}
        for row in rows
    ]}


@router.post("/uploads")
async def start_upload(request: Request):
    device_id = getattr(request.state, "iris_device_id", None)
    if not device_id:
        raise HTTPException(403, "Use uma sessão de dispositivo para sincronizar mídia")
    payload = await request.json()
    try:
        return await run_in_threadpool(
            _upload_service(request).reserve_upload,
            _user(request), device_id, request.app.state.account_quota_bytes, payload,
        )
    except SyncUploadError as exc:
        _raise_http(exc)


@router.post("/uploads/batch")
async def start_upload_batch(request: Request):
    """Reserve independent resumable uploads in one authenticated round trip."""
    user = _user(request)
    device_id = getattr(request.state, "iris_device_id", None)
    if not device_id:
        raise HTTPException(403, "Use uma sessão de dispositivo para sincronizar mídia")
    try:
        payload = await request.json()
        return await run_in_threadpool(
            _upload_service(request).reserve_upload_batch,
            user, device_id, request.app.state.account_quota_bytes, payload,
        )
    except SyncUploadError as exc:
        _raise_http(exc)


@router.get("/uploads/{upload_id}")
async def upload_status(request: Request, upload_id: str):
    try:
        return await run_in_threadpool(
            _upload_service(request).upload_status,
            _user(request), getattr(request.state, "iris_device_id", None), upload_id,
        )
    except SyncUploadError as exc:
        _raise_http(exc)


@router.put("/uploads/{upload_id}")
async def upload_chunk(request: Request, upload_id: str, offset: int = Query(..., ge=0)):
    user = _user(request)
    try:
        return await _upload_service(request).receive_chunk(
            user,
            getattr(request.state, "iris_device_id", None),
            upload_id,
            offset,
            int(request.headers.get("content-length", "0") or 0),
            request.stream(),
            lambda phase, started, **fields: _log_sync_phase(request, phase, started, **fields),
        )
    except SyncUploadError as exc:
        _raise_http(exc)


def _finished_callback(request: Request, user_id: int):
    return lambda: request.app.state.backend_registry.invalidate(user_id)


@router.post("/speedtest")
async def speed_test(request: Request, mode: str = Query("discard", pattern="^(discard|disk)$")):
    """Let a device measure its path to this server with bytes that are never kept."""
    user = _user(request)
    try:
        return await _upload_service(request).receive_speed_test(
            user,
            getattr(request.state, "iris_device_id", None),
            write_to_disk=mode == "disk",
            content_length=int(request.headers.get("content-length", "0") or 0),
            stream=request.stream(),
        )
    except SyncUploadError as exc:
        _raise_http(exc)


@router.post("/uploads/{upload_id}/complete")
async def complete_upload(request: Request, upload_id: str):
    user = _user(request)
    device_id = getattr(request.state, "iris_device_id", None)
    if not device_id:
        raise HTTPException(401, "Autenticação necessária")
    try:
        return await _upload_service(request).complete_upload(
            user,
            device_id,
            upload_id,
            sync_ai_processing=request.app.state.sync_ai_processing,
            load_model=request.app.state.load_model,
            on_finished=_finished_callback(request, user.id),
            log_phase=lambda phase, started, **fields: _log_sync_phase(request, phase, started, **fields),
        )
    except SyncUploadError as exc:
        _raise_http(exc)


@router.post("/uploads/complete-batch")
async def complete_upload_batch(request: Request):
    """Finalize independent uploads without allowing one failure to block siblings."""
    user = _user(request)
    device_id = getattr(request.state, "iris_device_id", None)
    if not device_id:
        raise HTTPException(401, "Autenticação necessária")
    try:
        return await _upload_service(request).complete_upload_batch(
            user,
            device_id,
            await request.json(),
            sync_ai_processing=request.app.state.sync_ai_processing,
            load_model=request.app.state.load_model,
            on_finished=_finished_callback(request, user.id),
            log_phase=lambda phase, started, **fields: _log_sync_phase(request, phase, started, **fields),
        )
    except SyncUploadError as exc:
        _raise_http(exc)
