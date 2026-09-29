"""Device synchronization API. All routes require an authenticated account."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from core.file_digest import FileDigest
from core.sync_db import append_change, changes_after, ensure_tables, now_iso, record_origin
from core.sync_file_ops import fsync_directory
from core.sync_processor import process_upload
from core.sync_upload_metadata import UploadMetadataError, parse_upload_metadata
from core.upload_finalization import (
    UnsafeUploadDestinationError,
    move_upload_into_library,
    record_upload_finalized,
)
from core.upload_reservations import UploadReservationStore
from core.users_db import list_devices, revoke_device

router = APIRouter(prefix="/api/sync", tags=["sync"])
_MAX_CHUNK_BYTES = 32 * 1024 * 1024
_MAX_UPLOAD_INIT_BATCH = 16
_UPLOAD_DISK_BUFFER_BYTES = 1024 * 1024
logger = logging.getLogger("iris")


def _log_sync_phase(request: Request, phase: str, started: float, **fields: int | str | bool) -> None:
    """Log aggregate sync timings without filenames, paths, or upload IDs."""
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


def _connection(request: Request) -> sqlite3.Connection:
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    from core.indexer_db import init_db

    init_db(user.db_path).close()
    conn = sqlite3.connect(user.db_path)
    ensure_tables(conn)
    # ensure_tables may backfill the usage counter for an existing library.
    # Finish that setup before a caller opens an immediate quota transaction.
    conn.commit()
    return conn


def _device_payload(device) -> dict:
    return {"id": device.id, "name": device.name, "platform": device.platform, "revoked": bool(device.revoked_at)}


@router.get("/devices")
async def devices(request: Request):
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    return {"devices": [_device_payload(device) for device in list_devices(request.app.state.users_db_path, user.id)]}


@router.delete("/devices/{device_id}")
async def revoke(request: Request, device_id: str):
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    if not revoke_device(request.app.state.users_db_path, user.id, device_id):
        raise HTTPException(404, "Dispositivo não encontrado")
    return {"ok": True}


@router.get("/changes")
async def changes(request: Request, cursor: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    with _connection(request) as conn:
        rows = changes_after(conn, cursor, limit)
    return {"changes": rows, "next_cursor": rows[-1]["cursor"] if rows else cursor, "has_more": len(rows) == limit}


@router.get("/sources")
async def sources(request: Request):
    with _connection(request) as conn:
        rows = conn.execute(
            """SELECT s.device_id, s.source_id, s.name, s.relative_path, s.volume,
                      s.media_kind, COUNT(DISTINCT o.media_id)
               FROM device_sources s
               LEFT JOIN media_origins o
                 ON o.device_id = s.device_id AND o.source_id = s.source_id
               GROUP BY s.device_id, s.source_id
               ORDER BY lower(s.name), s.media_kind"""
        ).fetchall()
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
        metadata = parse_upload_metadata(payload)
    except UploadMetadataError as exc:
        raise HTTPException(400, str(exc)) from exc
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    upload_id = uuid.uuid4().hex
    root = user.db_path.parent / "sync_uploads"
    temp_path = root / f"{upload_id}.part"
    with _connection(request) as conn:
        # Serialize the quota check with upload reservations so concurrent
        # devices cannot all observe the same free space and overbook it.
        conn.execute("BEGIN IMMEDIATE")
        remaining = UploadReservationStore.remaining_bytes(
            conn, request.app.state.account_quota_bytes
        )
        if metadata.size > remaining:
            raise HTTPException(413, "Cota da biblioteca excedida")
        root.mkdir(mode=0o700, exist_ok=True)
        created_at = now_iso()
        UploadReservationStore.insert(
            conn,
            upload_id=upload_id,
            device_id=device_id,
            filename=metadata.filename,
            expected_size=metadata.size,
            expected_hash=metadata.sha256,
            captured_at=metadata.captured_at,
            temp_path=temp_path,
            created_at=created_at,
            updated_at=created_at,
            source=metadata.source,
        )
        UploadReservationStore.record_source(
            conn, device_id=device_id, source=metadata.source, updated_at=now_iso()
        )
    return {"upload_id": upload_id, "offset": 0, "chunk_size": _MAX_CHUNK_BYTES}


@router.post("/uploads/batch")
async def start_upload_batch(request: Request):
    """Reserve several resumable uploads in one authenticated round trip.

    Every item is independently validated and reserved. The stable
    client_upload_id makes retries after a lost response idempotent; ownership
    is always derived from the authenticated device, never from the payload.
    """
    device_id = getattr(request.state, "iris_device_id", None)
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    if not device_id:
        raise HTTPException(403, "Use uma sessão de dispositivo para sincronizar mídia")

    payload = await request.json()
    items = payload.get("uploads") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items or len(items) > _MAX_UPLOAD_INIT_BATCH:
        raise HTTPException(400, f"O lote deve conter de 1 a {_MAX_UPLOAD_INIT_BATCH} itens")
    if any(not isinstance(raw, dict) for raw in items):
        raise HTTPException(400, "Todos os itens do lote devem conter metadados")
    client_upload_ids = [raw.get("client_upload_id") for raw in items]
    if any(
        not isinstance(client_upload_id, str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", client_upload_id)
        for client_upload_id in client_upload_ids
    ):
        raise HTTPException(400, "Cada item deve conter um client_upload_id válido")
    if len(set(client_upload_ids)) != len(client_upload_ids):
        raise HTTPException(400, "O lote não pode repetir client_upload_id")

    prepared: list[dict] = []
    for raw in items:
        client_upload_id = str(raw.get("client_upload_id", ""))
        try:
            metadata = parse_upload_metadata(raw)
        except UploadMetadataError as exc:
            prepared.append({
                "client_upload_id": client_upload_id,
                "error_code": 400,
                "error_message": str(exc),
            })
            continue
        prepared.append({
            "client_upload_id": client_upload_id,
            "filename": metadata.filename,
            "size": metadata.size,
            "sha256": metadata.sha256,
            "captured_at": metadata.captured_at,
            "source": metadata.source,
        })

    root = user.db_path.parent / "sync_uploads"
    results: list[dict] = []
    with _connection(request) as conn:
        # Reserve each accepted item while holding one quota transaction. A
        # single full library must not make valid siblings fail as a batch.
        conn.execute("BEGIN IMMEDIATE")
        remaining = UploadReservationStore.remaining_bytes(
            conn, request.app.state.account_quota_bytes
        )
        for item in prepared:
            if "error_code" in item:
                results.append(item)
                continue
            client_upload_id = item["client_upload_id"]
            existing = conn.execute(
                """SELECT id, filename, expected_size, expected_hash, received_size, state,
                          captured_at, source_id, source_name, source_relative_path,
                          source_volume, source_media_store_id, source_generation, source_media_kind
                   FROM sync_uploads WHERE device_id = ? AND client_upload_id = ?""",
                (device_id, client_upload_id),
            ).fetchone()
            if existing is not None:
                expected = (
                    item["filename"], item["size"], item["sha256"], item["captured_at"],
                    item["source"]["id"], item["source"]["name"],
                    item["source"]["relative_path"], item["source"]["volume"],
                    item["source"]["media_store_id"], item["source"]["generation"],
                    item["source"]["media_kind"],
                )
                recorded = tuple(existing[1:4]) + tuple(existing[6:14])
                if recorded != expected:
                    results.append({
                        "client_upload_id": client_upload_id,
                        "error_code": 409,
                        "error_message": "ID de envio já foi usado para outra mídia",
                    })
                else:
                    # A lost completion response may leave a durable finalizing
                    # intent. Return it as a fully-offset upload so the client
                    # proceeds straight to /complete and lets the server resume
                    # the persisted move, rather than treating an internal state
                    # as an unknown terminal failure.
                    state = "uploading" if existing[5] == "finalizing" else existing[5]
                    results.append({
                        "client_upload_id": client_upload_id,
                        "upload_id": existing[0],
                        "offset": existing[4],
                        "chunk_size": _MAX_CHUNK_BYTES,
                        "state": state,
                    })
                continue

            if item["size"] > remaining:
                results.append({
                    "client_upload_id": client_upload_id,
                    "error_code": 413,
                    "error_message": "Cota da biblioteca excedida",
                })
                continue
            upload_id = uuid.uuid4().hex
            temp_path = root / f"{upload_id}.part"
            source = item["source"]
            timestamp = now_iso()
            UploadReservationStore.insert(
                conn,
                upload_id=upload_id,
                device_id=device_id,
                filename=item["filename"],
                expected_size=item["size"],
                expected_hash=item["sha256"],
                captured_at=item["captured_at"],
                temp_path=temp_path,
                created_at=timestamp,
                updated_at=timestamp,
                source=source,
                client_upload_id=client_upload_id,
            )
            UploadReservationStore.record_source(
                conn, device_id=device_id, source=source, updated_at=timestamp
            )
            remaining -= item["size"]
            results.append({
                "client_upload_id": client_upload_id,
                "upload_id": upload_id,
                "offset": 0,
                "chunk_size": _MAX_CHUNK_BYTES,
                "state": "uploading",
            })
        if any("upload_id" in result for result in results):
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {"uploads": results}


@router.get("/uploads/{upload_id}")
async def upload_status(request: Request, upload_id: str):
    device_id = getattr(request.state, "iris_device_id", None)
    with _connection(request) as conn:
        row = conn.execute(
            "SELECT received_size, expected_size, state FROM sync_uploads WHERE id = ? AND device_id = ?",
            (upload_id, device_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Envio não encontrado")
    return {"upload_id": upload_id, "offset": row[0], "size": row[1], "state": row[2]}


@router.put("/uploads/{upload_id}")
async def upload_chunk(request: Request, upload_id: str, offset: int = Query(..., ge=0)):
    phase_started = time.perf_counter()
    device_id = getattr(request.state, "iris_device_id", None)
    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > _MAX_CHUNK_BYTES:
        raise HTTPException(413, "Chunk excede o limite")
    with _connection(request) as conn:
        row = conn.execute("SELECT expected_size, received_size, temp_path, state, device_id FROM sync_uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None or row[4] != device_id:
            raise HTTPException(404, "Envio não encontrado")
        if row[3] != "uploading" or offset != row[1]:
            raise HTTPException(409, "Offset de envio incompatível")
        if offset + content_length > row[0]:
            raise HTTPException(413, "Chunk ultrapassa o tamanho declarado")
        destination = Path(row[2])
        destination.parent.mkdir(parents=True, exist_ok=True)
        file_existed = destination.exists()
        destination.touch(mode=0o600, exist_ok=True)
        if not file_existed:
            fsync_directory(destination.parent)
        received = 0
        output = destination.open("r+b")
        try:
            output.seek(0, 2)
            file_size = output.tell()
            if file_size < offset:
                # A crash may have persisted the database offset but not all
                # file bytes. Reconcile to the durable file length and make
                # the client query status before retrying from that offset.
                conn.execute(
                    "UPDATE sync_uploads SET received_size = ?, updated_at = ? "
                    "WHERE id = ? AND device_id = ? AND received_size = ?",
                    (file_size, now_iso(), upload_id, device_id, offset),
                )
                conn.commit()
                raise HTTPException(409, "Offset reconciliado; consulte o estado do envio")
            if file_size > offset:
                # A previous request may have written bytes and then failed
                # before SQLite acknowledged them. Never append behind that
                # uncommitted tail on a retry.
                output.truncate(offset)
            output.seek(offset)
            try:
                pending = bytearray()
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > _MAX_CHUNK_BYTES or offset + received > row[0]:
                        raise HTTPException(413, "Chunk excede o limite")
                    view = memoryview(chunk)
                    cursor = 0
                    while cursor < len(view):
                        available = _UPLOAD_DISK_BUFFER_BYTES - len(pending)
                        end = min(cursor + available, len(view))
                        pending.extend(view[cursor:end])
                        cursor = end
                        if len(pending) == _UPLOAD_DISK_BUFFER_BYTES:
                            await run_in_threadpool(_write_upload_buffer, output, bytes(pending))
                            pending.clear()
                if pending:
                    await run_in_threadpool(_write_upload_buffer, output, bytes(pending))
                await run_in_threadpool(_flush_upload_buffer, output)
            except BaseException:
                # The database still advertises `offset`. Roll the part file
                # back to the same acknowledged boundary before propagating
                # disconnects, cancellation, or validation errors.
                await run_in_threadpool(_rollback_upload_buffer, output, offset)
                raise
        finally:
            await run_in_threadpool(output.close)
        conn.execute("UPDATE sync_uploads SET received_size = ?, updated_at = ? WHERE id = ?", (offset + received, now_iso(), upload_id))
    _log_sync_phase(request, "chunk_durable", phase_started, bytes=received, state="durable")
    return {"upload_id": upload_id, "offset": offset + received}


def _write_upload_buffer(output, payload: bytes) -> None:
    written = output.write(payload)
    if written != len(payload):
        raise OSError("Short write while storing upload chunk")


def _flush_upload_buffer(output) -> None:
    output.flush()
    os.fsync(output.fileno())


def _rollback_upload_buffer(output, offset: int) -> None:
    output.truncate(offset)
    output.flush()


@router.post("/uploads/{upload_id}/complete")
async def complete_upload(request: Request, upload_id: str):
    # Prevent duplicate completion races within a server process (for example,
    # when a client retries after a response timeout). The refcount keeps this
    # registry bounded to uploads that are active or waiting for the same lock.
    device_id = getattr(request.state, "iris_device_id", None)
    user = getattr(request.state, "iris_user", None)
    if user is None or not device_id:
        raise HTTPException(401, "Autenticação necessária")
    lock_registry = getattr(request.app.state, "sync_completion_locks", None)
    if lock_registry is None:
        lock_registry = {}
        request.app.state.sync_completion_locks = lock_registry
    lock_key = (int(user.id), str(device_id), upload_id)
    entry = lock_registry.get(lock_key)
    if entry is None:
        lock = asyncio.Lock()
        lock_registry[lock_key] = (lock, 1)
    else:
        lock, refcount = entry
        lock_registry[lock_key] = (lock, refcount + 1)
    try:
        async with lock:
            return await _complete_upload_once(request, upload_id, str(device_id))
    finally:
        lock, refcount = lock_registry[lock_key]
        if refcount <= 1:
            del lock_registry[lock_key]
        else:
            lock_registry[lock_key] = (lock, refcount - 1)


async def _complete_upload_once(request: Request, upload_id: str, device_id: str):
    with _connection(request) as conn:
        row = conn.execute(
            """SELECT filename, expected_size, expected_hash, received_size, temp_path,
                      state, device_id, captured_at, source_id, source_name,
                      source_relative_path, source_volume, source_media_store_id,
                      source_generation, source_media_kind, final_path
               FROM sync_uploads WHERE id = ?""", (upload_id,)
        ).fetchone()
        if row is None or row[6] != device_id:
            raise HTTPException(404, "Envio não encontrado")
        if row[5] in {"duplicate", "pending_processing", "processing", "ready", "failed_processing"}:
            # Completion is idempotent: the client may have lost the response
            # after the file was moved or accepted for processing.
            return {"upload_id": upload_id, "state": row[5]}
        if row[5] not in {"uploading", "finalizing"}:
            raise HTTPException(409, "Envio não pode ser concluído neste estado")
        if row[5] == "uploading" and row[1] != row[3]:
            raise HTTPException(409, "Envio incompleto")
        temporary = Path(row[4])
        user = getattr(request.state, "iris_user", None)
        if user is None:
            raise HTTPException(401, "Autenticação necessária")
        source = {
            "id": row[8], "name": row[9], "relative_path": row[10], "volume": row[11],
            "media_store_id": row[12], "generation": row[13], "media_kind": row[14],
        }
        if row[5] == "uploading":
            hash_started = time.perf_counter()
            actual_hash = await run_in_threadpool(FileDigest.sha256, temporary) if temporary.is_file() else None
            _log_sync_phase(request, "verify_hash", hash_started, bytes=row[1], state="ok" if actual_hash == row[2] else "mismatch")
            if actual_hash != row[2]:
                conn.execute("UPDATE sync_uploads SET state = 'failed', updated_at = ? WHERE id = ?", (now_iso(), upload_id))
                raise HTTPException(422, "Hash do arquivo não confere")
            duplicate = conn.execute("SELECT id FROM memes WHERE content_hash = ? LIMIT 1", (row[2],)).fetchone()
            if duplicate is not None:
                conn.execute("UPDATE sync_uploads SET state = 'duplicate', updated_at = ? WHERE id = ?", (now_iso(), upload_id))
                record_origin(conn, int(duplicate[0]), device_id, source)
                sequence = append_change(conn, "media", str(duplicate[0]), "unchanged", 1, {"media_id": int(duplicate[0]), "state": "duplicate"})
                # Persist the idempotent receipt before deleting the redundant
                # temp bytes. If the process dies after commit, retry returns
                # `duplicate` and a later temp-file janitor can reclaim bytes.
                conn.commit()
                temporary.unlink(missing_ok=True)
                return {"upload_id": upload_id, "media_id": int(duplicate[0]), "state": "duplicate", "cursor": sequence}
            month = row[7][:7] if re.fullmatch(r"\d{4}-\d{2}.*", row[7]) else now_iso()[:7]
            device_token = hashlib.sha256(device_id.encode()).hexdigest()[:12]
            source_token = hashlib.sha256(str(source["id"] or "unknown").encode()).hexdigest()[:12]
            destination_dir = user.media_root / "uploads" / device_token / source_token / month
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / f"{row[2][:12]}-{row[0]}"
            if destination.exists():
                destination = destination_dir / f"{upload_id[:8]}-{row[0]}"
            # Persist the intended destination before crossing the DB/filesystem
            # boundary. A retry can finish this exact move after a process crash.
            claim = conn.execute(
                "UPDATE sync_uploads SET state = 'finalizing', final_path = ?, updated_at = ? "
                "WHERE id = ? AND state = 'uploading'",
                (str(destination), now_iso(), upload_id),
            )
            conn.commit()
            if claim.rowcount != 1:
                raise HTTPException(409, "Outro processo já está finalizando este envio")
        else:
            destination = Path(row[15]) if row[15] else None
            if destination is None:
                raise HTTPException(500, "Envio em finalização sem destino recuperável")

        # This is atomic on one filesystem and uses verified destination-side
        # staging when the account media directory is a separate mounted disk.
        storage_started = time.perf_counter()
        try:
            await run_in_threadpool(
                move_upload_into_library,
                temporary,
                destination,
                media_root=user.media_root,
                upload_id=upload_id,
                expected_size=row[1],
                expected_hash=row[2],
            )
        except UnsafeUploadDestinationError as exc:
            logger.error(
                "sync_upload_destination_invalid user_id=%s upload_id=%s",
                user.id,
                upload_id,
            )
            raise HTTPException(500, "Destino de mídia inválido; envio preservado para recuperação") from exc
        except ValueError as exc:
            raise HTTPException(422, "Hash do arquivo não confere") from exc
        except FileNotFoundError as exc:
            raise HTTPException(500, "Arquivo temporário/final ausente; envio preservado para recuperação") from exc
        _log_sync_phase(request, "durable_move", storage_started, bytes=row[1], state="ok")

        sequence = record_upload_finalized(
            conn,
            upload_id=upload_id,
            filename=row[0],
            destination=destination,
            captured_at=row[7],
        )
        if sequence is None:
            return {"upload_id": upload_id, "state": "pending_processing"}
    if request.app.state.sync_ai_processing and request.app.state.load_model:
        threading.Thread(
            target=process_upload,
            kwargs={
                "db_path": user.db_path, "media_root": user.media_root,
                "model_name": user.model_name, "upload_id": upload_id,
                "file_path": destination,
                "on_finished": lambda: request.app.state.backend_registry.invalidate(user.id),
            },
            name=f"iris-sync-{upload_id[:8]}", daemon=True,
        ).start()
        return {
            "upload_id": upload_id,
            "state": "pending_processing",
            "cursor": sequence,
            "path": str(destination),
        }

    catalog_started = time.perf_counter()
    try:
        result = await run_in_threadpool(
            process_upload,
            db_path=user.db_path,
            media_root=user.media_root,
            model_name=user.model_name,
            upload_id=upload_id,
            file_path=destination,
            on_finished=lambda: request.app.state.backend_registry.invalidate(user.id),
            use_ai=False,
        )
        if result is None or result.get("state") == "failed_processing":
            raise HTTPException(500, "Não foi possível registrar a mídia na biblioteca")
        _log_sync_phase(request, "catalog_registration", catalog_started, bytes=row[1], item_count=1, state=str(result.get("state", "ok")))
        return result
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        # The original has already been moved into the account library. Record
        # the terminal error instead of leaving the phone's upload pending
        # forever; the file remains on disk for recovery.
        with _connection(request) as conn:
            conn.execute(
                "UPDATE sync_uploads SET state = 'failed_processing', updated_at = ? WHERE id = ?",
                (now_iso(), upload_id),
            )
            append_change(
                conn,
                "media",
                upload_id,
                "updated",
                3,
                {
                    "upload_id": upload_id,
                    "state": "failed_processing",
                    "error": str(exc)[:500],
                },
            )
        _log_sync_phase(request, "catalog_registration", catalog_started, bytes=row[1], item_count=1, state="failed")
        raise HTTPException(500, "Não foi possível registrar a mídia na biblioteca") from exc


@router.post("/uploads/complete-batch")
async def complete_upload_batch(request: Request):
    """Finalize independent media uploads with one control-plane round trip.

    Each item retains its own completion result; failures do not discard sibling
    successes. The existing per-upload operation remains the source of truth.
    """
    user = getattr(request.state, "iris_user", None)
    device_id = getattr(request.state, "iris_device_id", None)
    if user is None or not device_id:
        raise HTTPException(401, "Autenticação necessária")
    payload = await request.json()
    items = payload.get("uploads") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items or len(items) > _MAX_UPLOAD_INIT_BATCH:
        raise HTTPException(400, f"O lote deve conter de 1 a {_MAX_UPLOAD_INIT_BATCH} itens")
    if any(not isinstance(item, dict) for item in items):
        raise HTTPException(400, "Todos os itens devem conter upload_id")
    upload_ids = [item.get("upload_id") for item in items]
    if any(not isinstance(upload_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", upload_id)
            for upload_id in upload_ids):
        raise HTTPException(400, "Cada item deve conter um upload_id válido")
    if len(set(upload_ids)) != len(upload_ids):
        raise HTTPException(400, "O lote não pode repetir upload_id")

    async def finish_one(upload_id: str) -> dict:
        try:
            return await complete_upload(request, upload_id)
        except HTTPException as exc:
            return {
                "upload_id": upload_id,
                "error_code": exc.status_code,
                "error_message": str(exc.detail)[:500],
            }

    results = await asyncio.gather(*(finish_one(upload_id) for upload_id in upload_ids))
    return {"uploads": results}
