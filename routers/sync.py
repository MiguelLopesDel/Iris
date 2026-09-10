"""Device synchronization API. All routes require an authenticated account."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from core.sync_db import append_change, changes_after, ensure_tables, now_iso
from core.sync_processor import process_upload
from core.users_db import list_devices, revoke_device

router = APIRouter(prefix="/api/sync", tags=["sync"])
_MAX_CHUNK_BYTES = 32 * 1024 * 1024


def _connection(request: Request) -> sqlite3.Connection:
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    from core.indexer_db import init_db

    init_db(user.db_path).close()
    conn = sqlite3.connect(user.db_path)
    ensure_tables(conn)
    return conn


def _device_payload(device) -> dict:
    return {"id": device.id, "name": device.name, "platform": device.platform, "revoked": bool(device.revoked_at)}


def _library_usage_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


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


@router.post("/uploads")
async def start_upload(request: Request):
    device_id = getattr(request.state, "iris_device_id", None)
    if not device_id:
        raise HTTPException(403, "Use uma sessão de dispositivo para sincronizar mídia")
    payload = await request.json()
    filename = Path(str(payload.get("filename", ""))).name
    expected_size = payload.get("size")
    expected_hash = str(payload.get("sha256", "")).lower()
    if not filename or not isinstance(expected_size, int) or expected_size < 0 or len(expected_hash) != 64:
        raise HTTPException(400, "Metadados de envio inválidos")
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    if expected_size > request.app.state.account_quota_bytes - _library_usage_bytes(user.media_root):
        raise HTTPException(413, "Cota da biblioteca excedida")
    upload_id = uuid.uuid4().hex
    root = user.db_path.parent / "sync_uploads"
    root.mkdir(mode=0o700, exist_ok=True)
    temp_path = root / f"{upload_id}.part"
    with _connection(request) as conn:
        conn.execute(
            "INSERT INTO sync_uploads (id, device_id, filename, expected_size, expected_hash, captured_at, state, temp_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'uploading', ?, ?, ?)",
            (upload_id, device_id, filename, expected_size, expected_hash, str(payload.get("captured_at", ""))[:64], str(temp_path), now_iso(), now_iso()),
        )
    return {"upload_id": upload_id, "offset": 0, "chunk_size": _MAX_CHUNK_BYTES}


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
        received = 0
        with destination.open("ab") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > _MAX_CHUNK_BYTES or offset + received > row[0]:
                    raise HTTPException(413, "Chunk excede o limite")
                output.write(chunk)
        conn.execute("UPDATE sync_uploads SET received_size = ?, updated_at = ? WHERE id = ?", (offset + received, now_iso(), upload_id))
    return {"upload_id": upload_id, "offset": offset + received}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@router.post("/uploads/{upload_id}/complete")
async def complete_upload(request: Request, upload_id: str):
    device_id = getattr(request.state, "iris_device_id", None)
    with _connection(request) as conn:
        row = conn.execute("SELECT filename, expected_size, expected_hash, received_size, temp_path, state, device_id, captured_at FROM sync_uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None or row[6] != device_id:
            raise HTTPException(404, "Envio não encontrado")
        if row[5] != "uploading" or row[1] != row[3]:
            raise HTTPException(409, "Envio incompleto")
        temporary = Path(row[4])
        if await run_in_threadpool(_sha256, temporary) != row[2]:
            conn.execute("UPDATE sync_uploads SET state = 'failed', updated_at = ? WHERE id = ?", (now_iso(), upload_id))
            raise HTTPException(422, "Hash do arquivo não confere")
        user = getattr(request.state, "iris_user", None)
        if user is None:
            raise HTTPException(401, "Autenticação necessária")
        duplicate = conn.execute("SELECT id FROM memes WHERE content_hash = ? LIMIT 1", (row[2],)).fetchone()
        if duplicate is not None:
            temporary.unlink(missing_ok=True)
            conn.execute("UPDATE sync_uploads SET state = 'duplicate', updated_at = ? WHERE id = ?", (now_iso(), upload_id))
            sequence = append_change(conn, "media", str(duplicate[0]), "unchanged", 1, {"media_id": int(duplicate[0]), "state": "duplicate"})
            return {"upload_id": upload_id, "media_id": int(duplicate[0]), "state": "duplicate", "cursor": sequence}
        destination_dir = user.media_root / "uploads" / now_iso()[:7]
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / row[0]
        if destination.exists():
            destination = destination_dir / f"{upload_id[:8]}-{row[0]}"
        shutil.move(str(temporary), destination)
        conn.execute("UPDATE sync_uploads SET state = 'pending_processing', updated_at = ? WHERE id = ?", (now_iso(), upload_id))
        sequence = append_change(conn, "media", upload_id, "created", 1, {
            "upload_id": upload_id, "filename": row[0], "path": str(destination),
            "captured_at": row[7], "state": "pending_processing",
        })
    if request.app.state.load_model:
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
    return {"upload_id": upload_id, "state": "pending_processing", "cursor": sequence, "path": str(destination)}
