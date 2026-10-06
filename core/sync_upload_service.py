"""Account-scoped application workflows for resumable device uploads."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import AsyncIterable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from core.file_digest import FileDigest
from core.indexer_db import init_db
from core.sync_db import append_change, ensure_tables, now_iso, record_origin
from core.sync_durability import connect_deferred, make_durable
from core.sync_file_ops import fsync_directory, matches_original
from core.sync_processor import process_upload, process_uploads
from core.sync_upload_metadata import UploadMetadataError, parse_upload_metadata
from core.upload_finalization import (
    UnsafeUploadDestinationError,
    move_upload_into_library,
    record_upload_finalized,
)
from core.upload_processing_workers import UploadProcessingWorkers
from core.upload_reservations import UploadReservationStore
from core.users_db import IrisUser

_MAX_CHUNK_BYTES = 32 * 1024 * 1024
_MAX_UPLOAD_INIT_BATCH = 16
_UPLOAD_DISK_BUFFER_BYTES = 1024 * 1024
# A speed test measures a path, not a library: large enough for a stable rate
# over a home connection, small enough that it cannot fill a disk.
SPEED_TEST_MAX_BYTES = 256 * 1024 * 1024
_UPLOAD_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")

# Databases whose schema this process already prepared, by file identity.
# Preparing (init_db plus the sync tables) on every request cost a schema
# walk and a commit per chunk; it is needed once per database file.
_prepared_databases: set[tuple[str, int, int]] = set()
_prepared_lock = threading.Lock()
_prepare_lock = threading.Lock()


def _database_identity(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (str(path), stat.st_dev, stat.st_ino)


def _schema_present(connection: sqlite3.Connection) -> bool:
    # A file recreated at the same path can reuse the inode; a fresh
    # database has no sync tables, so it is prepared again.
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_uploads'"
    ).fetchone() is not None


class SyncUploadError(Exception):
    """An upload workflow failure safe to map to an HTTP response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class _Finalization:
    """One upload of a completion batch, from its claim to its catalog entry."""

    upload_id: str
    filename: str
    size: int
    expected_hash: str
    captured_at: str
    temporary: Path
    destination: Path | None = None
    sequence: int | None = None
    # Settled without a move (a duplicate): the answer, and files to delete
    # once that is committed.
    answer: dict[str, Any] | None = None
    leftovers: list[Path] = field(default_factory=list)


class SyncUploadService:
    """Own upload reservations, durable chunks, and idempotent completion.

    The authenticated caller supplies the account and device identities. Every
    persistence lookup is scoped to that account database and every upload row
    is checked against the device before being read or mutated.
    """

    def __init__(
        self,
        upload_processor: Callable[..., Any] = process_upload,
        processing_workers: UploadProcessingWorkers | None = None,
        batch_processor: Callable[..., Any] | None = None,
    ) -> None:
        self._upload_processor = upload_processor
        # Catalogs a completion batch at once; a custom single-upload
        # processor without a batch counterpart is called once per upload.
        if batch_processor is None and upload_processor is process_upload:
            batch_processor = process_uploads
        self._batch_processor = batch_processor
        self._processing_workers = processing_workers
        self._operation_locks: dict[tuple[int, str, str], tuple[asyncio.Lock, int]] = {}

    @staticmethod
    def open_connection(user: IrisUser) -> sqlite3.Connection:
        identity = _database_identity(user.db_path)
        if identity is not None and identity in _prepared_databases:
            connection = connect_deferred(user.db_path)
            if _schema_present(connection):
                return connection
            connection.close()
        # One thread prepares a database at a time: requests now run in
        # parallel worker threads, and two schema migrations at once collide
        # (ALTER TABLE ... "duplicate column name").
        with _prepare_lock:
            identity = _database_identity(user.db_path)
            if identity is not None and identity in _prepared_databases:
                connection = connect_deferred(user.db_path)
                if _schema_present(connection):
                    return connection
                connection.close()
            init_db(user.db_path).close()
            connection = connect_deferred(user.db_path)
            ensure_tables(connection)
            # ensure_tables may backfill usage counters before a quota transaction.
            connection.commit()
            identity = _database_identity(user.db_path)
            if identity is not None:
                with _prepared_lock:
                    _prepared_databases.add(identity)
            return connection

    def reserve_upload(
        self,
        user: IrisUser,
        device_id: str | None,
        quota_bytes: int,
        payload: dict,
    ) -> dict[str, Any]:
        if not device_id:
            raise SyncUploadError(403, "Use uma sessão de dispositivo para sincronizar mídia")
        try:
            metadata = parse_upload_metadata(payload)
        except UploadMetadataError as exc:
            raise SyncUploadError(400, str(exc)) from exc

        upload_id = uuid.uuid4().hex
        root = user.db_path.parent / "sync_uploads"
        temp_path = root / f"{upload_id}.part"
        with self.open_connection(user) as connection:
            connection.execute("BEGIN IMMEDIATE")
            created_at = now_iso()
            media_id = UploadReservationStore.catalogued_media_id(connection, metadata.sha256)
            # Bytes already in the library cost nothing: they are never sent.
            if media_id is None and metadata.size > UploadReservationStore.remaining_bytes(
                connection, quota_bytes
            ):
                raise SyncUploadError(413, "Cota da biblioteca excedida")
            UploadReservationStore.insert(
                connection,
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
                state="uploading" if media_id is None else "duplicate",
            )
            UploadReservationStore.record_source(
                connection,
                device_id=device_id,
                source=metadata.source,
                updated_at=now_iso(),
            )
            if media_id is not None:
                return _known_duplicate(connection, upload_id, media_id, device_id, metadata.source)
            root.mkdir(mode=0o700, exist_ok=True)
        make_durable(user.db_path)
        return {"upload_id": upload_id, "offset": 0, "chunk_size": _MAX_CHUNK_BYTES}

    def reserve_upload_batch(
        self,
        user: IrisUser,
        device_id: str | None,
        quota_bytes: int,
        payload: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        if not device_id:
            raise SyncUploadError(403, "Use uma sessão de dispositivo para sincronizar mídia")
        items = payload.get("uploads") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items or len(items) > _MAX_UPLOAD_INIT_BATCH:
            raise SyncUploadError(400, f"O lote deve conter de 1 a {_MAX_UPLOAD_INIT_BATCH} itens")
        if any(not isinstance(raw, dict) for raw in items):
            raise SyncUploadError(400, "Todos os itens do lote devem conter metadados")

        client_upload_ids = [raw.get("client_upload_id") for raw in items]
        if any(
            not isinstance(client_upload_id, str)
            or not _UPLOAD_ID.fullmatch(client_upload_id)
            for client_upload_id in client_upload_ids
        ):
            raise SyncUploadError(400, "Cada item deve conter um client_upload_id válido")
        if len(set(client_upload_ids)) != len(client_upload_ids):
            raise SyncUploadError(400, "O lote não pode repetir client_upload_id")

        prepared: list[dict[str, Any]] = []
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
        results: list[dict[str, Any]] = []
        with self.open_connection(user) as connection:
            connection.execute("BEGIN IMMEDIATE")
            remaining = UploadReservationStore.remaining_bytes(connection, quota_bytes)
            for item in prepared:
                if "error_code" in item:
                    results.append(item)
                    continue
                client_upload_id = item["client_upload_id"]
                existing = connection.execute(
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
                        state = "uploading" if existing[5] == "finalizing" else existing[5]
                        results.append({
                            "client_upload_id": client_upload_id,
                            "upload_id": existing[0],
                            "offset": existing[4],
                            "chunk_size": _MAX_CHUNK_BYTES,
                            "state": state,
                        })
                    continue

                media_id = UploadReservationStore.catalogued_media_id(connection, item["sha256"])
                if media_id is None and item["size"] > remaining:
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
                    connection,
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
                    state="uploading" if media_id is None else "duplicate",
                )
                UploadReservationStore.record_source(
                    connection,
                    device_id=device_id,
                    source=source,
                    updated_at=timestamp,
                )
                if media_id is not None:
                    results.append({
                        "client_upload_id": client_upload_id,
                        **_known_duplicate(connection, upload_id, media_id, device_id, source),
                    })
                    continue
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
        # A reservation or known duplicate the device is told about is on disk.
        make_durable(user.db_path)
        return {"uploads": results}

    def upload_status(self, user: IrisUser, device_id: str | None, upload_id: str) -> dict[str, Any]:
        with self.open_connection(user) as connection:
            row = connection.execute(
                "SELECT received_size, expected_size, state FROM sync_uploads "
                "WHERE id = ? AND device_id = ?",
                (upload_id, device_id),
            ).fetchone()
        if row is None:
            raise SyncUploadError(404, "Envio não encontrado")
        return {"upload_id": upload_id, "offset": row[0], "size": row[1], "state": row[2]}

    async def receive_chunk(
        self,
        user: IrisUser,
        device_id: str | None,
        upload_id: str,
        offset: int,
        content_length: int,
        stream: AsyncIterable[bytes],
        log_phase: Callable[..., None],
    ) -> dict[str, Any]:
        if content_length > _MAX_CHUNK_BYTES:
            raise SyncUploadError(413, "Chunk excede o limite")
        phase_started = time.perf_counter()
        async with self._serialize_upload(user.id, device_id or "", upload_id):
            # Database and directory syncs block; they run in the thread pool
            # so the event loop keeps serving other requests meanwhile.
            expected_size, output = await run_in_threadpool(
                self._open_chunk_target, user, device_id, upload_id, offset, content_length
            )
            received = 0
            try:
                try:
                    pending = bytearray()
                    async for chunk in stream:
                        received += len(chunk)
                        if received > _MAX_CHUNK_BYTES or offset + received > expected_size:
                            raise SyncUploadError(413, "Chunk excede o limite")
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
                    await run_in_threadpool(_rollback_upload_buffer, output, offset)
                    raise
            finally:
                await run_in_threadpool(output.close)
            await run_in_threadpool(self._record_chunk, user, upload_id, offset + received)
        log_phase("chunk_durable", phase_started, bytes=received, state="durable")
        return {"upload_id": upload_id, "offset": offset + received}

    def _open_chunk_target(
        self, user: IrisUser, device_id: str | None, upload_id: str, offset: int, content_length: int,
    ) -> tuple[int, Any]:
        """Check the upload and open its file at ``offset``; runs in a worker thread."""
        with closing(self.open_connection(user)) as connection:
            row = connection.execute(
                "SELECT expected_size, received_size, temp_path, state, device_id "
                "FROM sync_uploads WHERE id = ?",
                (upload_id,),
            ).fetchone()
            if row is None or row[4] != device_id:
                raise SyncUploadError(404, "Envio não encontrado")
            if row[3] != "uploading" or offset != row[1]:
                raise SyncUploadError(409, "Offset de envio incompatível")
            if offset + content_length > row[0]:
                raise SyncUploadError(413, "Chunk ultrapassa o tamanho declarado")
            destination = Path(row[2])
            destination.parent.mkdir(parents=True, exist_ok=True)
            file_existed = destination.exists()
            destination.touch(mode=0o600, exist_ok=True)
            if not file_existed:
                fsync_directory(destination.parent)
            output = destination.open("r+b")
            try:
                output.seek(0, 2)
                file_size = output.tell()
                if file_size < offset:
                    connection.execute(
                        "UPDATE sync_uploads SET received_size = ?, updated_at = ? "
                        "WHERE id = ? AND device_id = ? AND received_size = ?",
                        (file_size, now_iso(), upload_id, device_id, offset),
                    )
                    connection.commit()
                    raise SyncUploadError(409, "Offset reconciliado; consulte o estado do envio")
                if file_size > offset:
                    output.truncate(offset)
                output.seek(offset)
            except BaseException:
                output.close()
                raise
            return int(row[0]), output

    def _record_chunk(self, user: IrisUser, upload_id: str, received_size: int) -> None:
        with closing(self.open_connection(user)) as connection:
            connection.execute(
                "UPDATE sync_uploads SET received_size = ?, updated_at = ? WHERE id = ?",
                (received_size, now_iso(), upload_id),
            )
            connection.commit()

    async def receive_speed_test(
        self,
        user: IrisUser,
        device_id: str | None,
        *,
        write_to_disk: bool,
        content_length: int,
        stream: AsyncIterable[bytes],
    ) -> dict[str, Any]:
        """Receive a synthetic payload that is never stored as media.

        Without ``write_to_disk`` the bytes are dropped, measuring network and
        HTTP alone. With it they go through the same buffered write and fsync
        as an upload chunk into a scratch file, so the difference between the
        two runs is the cost of this server's storage.
        """
        if not device_id:
            raise SyncUploadError(403, "Use uma sessão de dispositivo para medir a velocidade")
        if content_length > SPEED_TEST_MAX_BYTES:
            raise SyncUploadError(413, "Teste de velocidade excede o limite")
        started = time.perf_counter()
        received = 0
        scratch: Path | None = None
        output = None
        try:
            if write_to_disk:
                scratch = user.db_path.parent / "sync_uploads" / f"speedtest-{uuid.uuid4().hex}.part"
                scratch.parent.mkdir(parents=True, exist_ok=True)
                output = scratch.open("wb")
            pending = bytearray()
            async for chunk in stream:
                received += len(chunk)
                if received > SPEED_TEST_MAX_BYTES:
                    raise SyncUploadError(413, "Teste de velocidade excede o limite")
                if output is None:
                    continue
                pending.extend(chunk)
                if len(pending) >= _UPLOAD_DISK_BUFFER_BYTES:
                    await run_in_threadpool(_write_upload_buffer, output, bytes(pending))
                    pending.clear()
            if output is not None:
                if pending:
                    await run_in_threadpool(_write_upload_buffer, output, bytes(pending))
                await run_in_threadpool(_flush_upload_buffer, output)
        finally:
            if output is not None:
                await run_in_threadpool(output.close)
            if scratch is not None:
                # A scratch file of synthetic bytes, never media: it is removed
                # outright rather than sent to any trash.
                scratch.unlink(missing_ok=True)
        return {
            "bytes": received,
            "server_seconds": round(time.perf_counter() - started, 4),
            "mode": "disk" if write_to_disk else "discard",
        }

    async def complete_upload(
        self,
        user: IrisUser,
        device_id: str | None,
        upload_id: str,
        *,
        sync_ai_processing: bool,
        load_model: bool,
        on_finished: Callable[[], None],
        log_phase: Callable[..., None],
    ) -> dict[str, Any]:
        if user is None or not device_id:
            raise SyncUploadError(401, "Autenticação necessária")
        try:
            [outcome] = await self._complete_serialized(
                user,
                device_id,
                [upload_id],
                sync_ai_processing=sync_ai_processing,
                load_model=load_model,
                on_finished=on_finished,
                log_phase=log_phase,
            )
        finally:
            # Whatever the device is answered, success or error, is on disk.
            await run_in_threadpool(make_durable, user.db_path)
        if isinstance(outcome, SyncUploadError):
            raise outcome
        return outcome

    async def complete_upload_batch(
        self,
        user: IrisUser,
        device_id: str | None,
        payload: Any,
        *,
        sync_ai_processing: bool,
        load_model: bool,
        on_finished: Callable[[], None],
        log_phase: Callable[..., None],
    ) -> dict[str, list[dict[str, Any]]]:
        if user is None or not device_id:
            raise SyncUploadError(401, "Autenticação necessária")
        items = payload.get("uploads") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items or len(items) > _MAX_UPLOAD_INIT_BATCH:
            raise SyncUploadError(400, f"O lote deve conter de 1 a {_MAX_UPLOAD_INIT_BATCH} itens")
        if any(not isinstance(item, dict) for item in items):
            raise SyncUploadError(400, "Todos os itens devem conter upload_id")
        upload_ids = [item.get("upload_id") for item in items]
        if any(not isinstance(value, str) or not _UPLOAD_ID.fullmatch(value) for value in upload_ids):
            raise SyncUploadError(400, "Cada item deve conter um upload_id válido")
        if len(set(upload_ids)) != len(upload_ids):
            raise SyncUploadError(400, "O lote não pode repetir upload_id")

        outcomes = await self._complete_serialized(
            user,
            device_id,
            upload_ids,
            sync_ai_processing=sync_ai_processing,
            load_model=load_model,
            on_finished=on_finished,
            log_phase=log_phase,
        )
        # One disk sync for the whole batch, before the device is told it is stored.
        await run_in_threadpool(make_durable, user.db_path)
        return {"uploads": [
            {
                "upload_id": upload_id,
                "error_code": outcome.status_code,
                "error_message": outcome.detail[:500],
            } if isinstance(outcome, SyncUploadError) else outcome
            for upload_id, outcome in zip(upload_ids, outcomes, strict=True)
        ]}

    async def _complete_serialized(
        self,
        user: IrisUser,
        device_id: str,
        upload_ids: list[str],
        **options: Any,
    ) -> list[dict[str, Any] | SyncUploadError]:
        async with AsyncExitStack() as locks:
            # One fixed order, so two batches sharing uploads cannot deadlock.
            for upload_id in sorted(upload_ids):
                await locks.enter_async_context(
                    self._serialize_upload(user.id, device_id, upload_id)
                )
            # Database, hashing and filesystem work end to end, in a worker
            # thread so the event loop keeps serving other requests.
            return await run_in_threadpool(
                self._complete_batch_sync, user, device_id, upload_ids, **options
            )

    def _complete_batch_sync(
        self,
        user: IrisUser,
        device_id: str,
        upload_ids: list[str],
        *,
        sync_ai_processing: bool,
        load_model: bool,
        on_finished: Callable[[], None],
        log_phase: Callable[..., None],
    ) -> list[dict[str, Any] | SyncUploadError]:
        """Finish a batch of uploads together, each with its own outcome.

        Finishing them one by one, side by side, made every upload queue for
        SQLite's single writer several times and sync two directories. Here
        the batch takes few transactions, in the order that keeps a crash
        recoverable at any point:

        1. one transaction claims every upload as finalizing, with its
           destination, before any file moves;
        2. the files move, then each directory touched is synced once;
        3. one transaction records them all as moved;
        4. the catalog takes them in turn.
        """
        outcomes: dict[str, dict[str, Any] | SyncUploadError] = {}
        claimed: list[_Finalization] = []
        with closing(self.open_connection(user)) as connection:
            # Hash before the write transaction, so its lock is not held
            # while files are read.
            digests = self._received_digests(connection, device_id, upload_ids, log_phase)
            try:
                for upload_id in upload_ids:
                    try:
                        claim = self._claim_finalization(
                            connection, user, device_id, upload_id, digests.get(upload_id)
                        )
                    except SyncUploadError as exc:
                        outcomes[upload_id] = exc
                        continue
                    if isinstance(claim, dict):
                        outcomes[upload_id] = claim
                        continue
                    claimed.append(claim)
                    if claim.answer is not None:
                        outcomes[upload_id] = claim.answer
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            # Files of uploads settled as duplicates go once that is committed.
            for finalization in claimed:
                for path in finalization.leftovers:
                    path.unlink(missing_ok=True)
            claimed = [finalization for finalization in claimed if finalization.answer is None]

            moved = self._move_into_library(user, claimed, outcomes, log_phase)

            try:
                for finalization in moved:
                    finalization.sequence = record_upload_finalized(
                        connection,
                        upload_id=finalization.upload_id,
                        filename=finalization.filename,
                        destination=finalization.destination,
                        captured_at=finalization.captured_at,
                        commit=False,
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        inline = not (sync_ai_processing and load_model)
        batched = [
            finalization for finalization in moved
            if inline and finalization.sequence is not None and self._batch_processor is not None
        ]
        if len(batched) > 1:
            outcomes.update(self._register_batch(user, batched, on_finished, log_phase))
        for finalization in moved:
            if finalization.upload_id not in outcomes:
                outcomes[finalization.upload_id] = self._register(
                    user, finalization,
                    sync_ai_processing=sync_ai_processing, load_model=load_model,
                    on_finished=on_finished, log_phase=log_phase,
                )
        return [outcomes[upload_id] for upload_id in upload_ids]

    def _register_batch(
        self,
        user: IrisUser,
        batch: list[_Finalization],
        on_finished: Callable[[], None],
        log_phase: Callable[..., None],
    ) -> dict[str, dict[str, Any] | SyncUploadError]:
        """Catalog moved uploads together; an upload without a result is left out."""
        catalog_started = time.perf_counter()
        try:
            results = self._batch_processor(
                db_path=user.db_path,
                media_root=user.media_root,
                model_name=user.model_name,
                uploads=[(item.upload_id, item.destination) for item in batch],
                on_finished=on_finished,
            )
        except Exception:
            # Each upload then goes through the single path, which records
            # its failure.
            return {}
        outcomes: dict[str, dict[str, Any] | SyncUploadError] = {}
        for item in batch:
            result = results.get(item.upload_id)
            if result is None or result.get("state") == "failed_processing":
                outcomes[item.upload_id] = SyncUploadError(
                    500, "Não foi possível registrar a mídia na biblioteca"
                )
            else:
                outcomes[item.upload_id] = result
        log_phase(
            "catalog_registration", catalog_started, bytes=sum(item.size for item in batch),
            item_count=len(batch), state="ok",
        )
        return outcomes

    @staticmethod
    def _received_digests(
        connection: sqlite3.Connection,
        device_id: str,
        upload_ids: list[str],
        log_phase: Callable[..., None],
    ) -> dict[str, str]:
        """The hash of each of the device's fully received uploads."""
        digests: dict[str, str] = {}
        for upload_id in upload_ids:
            row = connection.execute(
                "SELECT expected_size, expected_hash, received_size, temp_path, state "
                "FROM sync_uploads WHERE id = ? AND device_id = ?",
                (upload_id, device_id),
            ).fetchone()
            if row is None or row[4] != "uploading" or row[0] != row[2]:
                continue
            temporary = Path(row[3])
            if not temporary.is_file():
                continue
            hash_started = time.perf_counter()
            digests[upload_id] = FileDigest.sha256(temporary)
            log_phase(
                "verify_hash", hash_started, bytes=row[0],
                state="ok" if digests[upload_id] == row[1] else "mismatch",
            )
        return digests

    def _claim_finalization(
        self,
        connection: sqlite3.Connection,
        user: IrisUser,
        device_id: str,
        upload_id: str,
        digest: str | None,
    ) -> dict[str, Any] | _Finalization:
        """Settle one upload in the batch's transaction, or claim it for its move.

        Returns the answer for an upload that needs no move, or the claimed
        finalization; raises for an upload that cannot finish now. Nothing is
        committed here: the batch commits once.
        """
        row = connection.execute(
            """SELECT filename, expected_size, expected_hash, received_size, temp_path,
                      state, device_id, captured_at, source_id, source_name,
                      source_relative_path, source_volume, source_media_store_id,
                      source_generation, source_media_kind, final_path
               FROM sync_uploads WHERE id = ?""",
            (upload_id,),
        ).fetchone()
        if row is None or row[6] != device_id:
            raise SyncUploadError(404, "Envio não encontrado")
        if row[5] in {"duplicate", "pending_processing", "processing", "ready", "failed_processing"}:
            return {"upload_id": upload_id, "state": row[5]}
        if row[5] not in {"uploading", "finalizing"}:
            raise SyncUploadError(409, "Envio não pode ser concluído neste estado")
        if row[5] == "uploading" and row[1] != row[3]:
            raise SyncUploadError(409, "Envio incompleto")
        finalization = _Finalization(
            upload_id=upload_id, filename=row[0], size=row[1], expected_hash=row[2],
            captured_at=row[7], temporary=Path(row[4]),
        )
        if row[5] == "finalizing":
            if not row[15]:
                raise SyncUploadError(500, "Envio em finalização sem destino recuperável")
            finalization.destination = Path(row[15])
            return finalization

        source = {
            "id": row[8], "name": row[9], "relative_path": row[10],
            "volume": row[11], "media_store_id": row[12],
            "generation": row[13], "media_kind": row[14],
        }
        destination_dir, primary, alternate = _upload_destinations(user, device_id, upload_id, row)
        recovered: Path | None = None
        if digest is None:
            # After a power loss the database can be behind the files:
            # "fully received" while the original was already moved into
            # the library, or already cataloged.
            for candidate in (primary, alternate):
                # Only an unreferenced copy can be this upload's own: two
                # uploads of the same photo share the usual name.
                if not _referenced_elsewhere(
                    connection, candidate, upload_id, user.media_root
                ) and matches_original(candidate, row[1], row[2]):
                    recovered = candidate
                    break
            if recovered is None and connection.execute(
                "SELECT 1 FROM memes WHERE content_hash = ? LIMIT 1", (row[2],)
            ).fetchone() is None:
                # Nothing to recover: ask for the bytes again rather
                # than failing a photo the device still has.
                connection.execute(
                    "UPDATE sync_uploads SET received_size = 0, updated_at = ? WHERE id = ?",
                    (now_iso(), upload_id),
                )
                raise SyncUploadError(409, "Envio incompleto")
        elif digest != row[2]:
            connection.execute(
                "UPDATE sync_uploads SET state = 'failed', updated_at = ? WHERE id = ?",
                (now_iso(), upload_id),
            )
            raise SyncUploadError(422, "Hash do arquivo não confere")

        duplicate = connection.execute(
            "SELECT id FROM memes WHERE content_hash = ? LIMIT 1", (row[2],)
        ).fetchone()
        if duplicate is not None:
            media_id = int(duplicate[0])
            connection.execute(
                "UPDATE sync_uploads SET state = 'duplicate', updated_at = ? WHERE id = ?",
                (now_iso(), upload_id),
            )
            record_origin(connection, media_id, device_id, source)
            sequence = append_change(
                connection, "media", str(media_id), "unchanged", 1,
                {"media_id": media_id, "state": "duplicate"},
            )
            finalization.leftovers.append(finalization.temporary)
            if recovered is not None and not _referenced_elsewhere(
                connection, recovered, upload_id, user.media_root
            ):
                finalization.leftovers.append(recovered)
            finalization.answer = {
                "upload_id": upload_id, "media_id": media_id,
                "state": "duplicate", "cursor": sequence,
            }
            return finalization

        destination_dir.mkdir(parents=True, exist_ok=True)
        if recovered is not None:
            destination = recovered
        elif not _referenced_elsewhere(connection, primary, upload_id, user.media_root) and (
            not primary.exists() or matches_original(primary, row[1], row[2])
        ):
            # Free, or an identical unreferenced copy left by an interrupted
            # attempt. A name another upload or the catalog points to is never
            # shared, even before its file arrives (an earlier upload in this
            # batch): finishing this one could delete it as a duplicate.
            destination = primary
        else:
            destination = alternate
        claim = connection.execute(
            "UPDATE sync_uploads SET state = 'finalizing', final_path = ?, updated_at = ? "
            "WHERE id = ? AND state = 'uploading'",
            (str(destination), now_iso(), upload_id),
        )
        if claim.rowcount != 1:
            raise SyncUploadError(409, "Outro processo já está finalizando este envio")
        finalization.destination = destination
        return finalization

    @staticmethod
    def _move_into_library(
        user: IrisUser,
        claimed: list[_Finalization],
        outcomes: dict[str, dict[str, Any] | SyncUploadError],
        log_phase: Callable[..., None],
    ) -> list[_Finalization]:
        """Move each claimed file, then sync every directory touched once."""
        moved: list[_Finalization] = []
        unsynced: set[Path] = set()
        for finalization in claimed:
            storage_started = time.perf_counter()
            try:
                move_upload_into_library(
                    finalization.temporary,
                    finalization.destination,
                    media_root=user.media_root,
                    upload_id=finalization.upload_id,
                    expected_size=finalization.size,
                    expected_hash=finalization.expected_hash,
                    unsynced_directories=unsynced,
                )
            except UnsafeUploadDestinationError:
                outcomes[finalization.upload_id] = SyncUploadError(
                    500, "Destino de mídia inválido; envio preservado para recuperação"
                )
                continue
            except ValueError:
                outcomes[finalization.upload_id] = SyncUploadError(
                    422, "Hash do arquivo não confere"
                )
                continue
            except FileNotFoundError:
                outcomes[finalization.upload_id] = SyncUploadError(
                    500, "Arquivo temporário/final ausente; envio preservado para recuperação"
                )
                continue
            log_phase("durable_move", storage_started, bytes=finalization.size, state="ok")
            moved.append(finalization)
        # Before any move is recorded: a recorded move must not be undone by
        # a power loss.
        sync_started = time.perf_counter()
        for directory in sorted(unsynced):
            fsync_directory(directory)
        if unsynced:
            log_phase(
                "directory_sync", sync_started, item_count=len(unsynced), state="ok",
            )
        return moved

    def _register(
        self,
        user: IrisUser,
        finalization: _Finalization,
        *,
        sync_ai_processing: bool,
        load_model: bool,
        on_finished: Callable[[], None],
        log_phase: Callable[..., None],
    ) -> dict[str, Any] | SyncUploadError:
        """Hand one moved upload to the catalog; its outcome for the device."""
        upload_id, destination = finalization.upload_id, finalization.destination
        if finalization.sequence is None:
            return {"upload_id": upload_id, "state": "pending_processing"}
        if sync_ai_processing and load_model:
            if self._processing_workers is not None:
                self._processing_workers.submit(
                    user,
                    upload_id,
                    destination,
                    use_ai=True,
                    on_finished=lambda _user_id: on_finished(),
                )
            return {
                "upload_id": upload_id, "state": "pending_processing",
                "cursor": finalization.sequence, "path": str(destination),
            }

        catalog_started = time.perf_counter()
        try:
            result = self._upload_processor(
                db_path=user.db_path,
                media_root=user.media_root,
                model_name=user.model_name,
                upload_id=upload_id,
                file_path=destination,
                on_finished=on_finished,
                use_ai=False,
            )
            if result is None or result.get("state") == "failed_processing":
                return SyncUploadError(500, "Não foi possível registrar a mídia na biblioteca")
            log_phase(
                "catalog_registration", catalog_started, bytes=finalization.size, item_count=1,
                state=str(result.get("state", "ok")),
            )
            return result
        except Exception:
            with closing(self.open_connection(user)) as connection:
                connection.execute(
                    "UPDATE sync_uploads SET state = 'failed_processing', updated_at = ? WHERE id = ?",
                    (now_iso(), upload_id),
                )
                append_change(connection, "media", upload_id, "updated", 3, {
                    "upload_id": upload_id,
                    "state": "failed_processing",
                    "error": "Catalog registration failed; original preserved for recovery.",
                })
                connection.commit()
            log_phase(
                "catalog_registration", catalog_started, bytes=finalization.size, item_count=1,
                state="failed",
            )
            return SyncUploadError(500, "Não foi possível registrar a mídia na biblioteca")

    @asynccontextmanager
    async def _serialize_upload(self, user_id: int, device_id: str, upload_id: str):
        key = (user_id, device_id, upload_id)
        entry = self._operation_locks.get(key)
        if entry is None:
            lock = asyncio.Lock()
            self._operation_locks[key] = (lock, 1)
        else:
            lock, refcount = entry
            self._operation_locks[key] = (lock, refcount + 1)
        try:
            async with lock:
                yield
        finally:
            lock, refcount = self._operation_locks[key]
            if refcount <= 1:
                del self._operation_locks[key]
            else:
                self._operation_locks[key] = (lock, refcount - 1)


def _upload_destinations(
    user: IrisUser, device_id: str, upload_id: str, row: Any,
) -> tuple[Path, Path, Path]:
    """Where an upload is stored: its directory, the usual name, the fallback name.

    Both names are derived from the upload alone, so an interrupted attempt's
    copy can be found again.
    """
    month = row[7][:7] if re.fullmatch(r"\d{4}-\d{2}.*", row[7]) else now_iso()[:7]
    device_token = hashlib.sha256(device_id.encode()).hexdigest()[:12]
    source_token = hashlib.sha256(str(row[8] or "unknown").encode()).hexdigest()[:12]
    directory = user.media_root / "uploads" / device_token / source_token / month
    return directory, directory / f"{row[2][:12]}-{row[0]}", directory / f"{upload_id[:8]}-{row[0]}"


def _referenced_elsewhere(
    connection: sqlite3.Connection, path: Path, upload_id: str, media_root: Path,
) -> bool:
    """Whether the catalog or another upload points to ``path``."""
    names = (str(path), str(path.resolve()))
    if connection.execute(
        "SELECT 1 FROM memes WHERE caminho IN (?, ?) LIMIT 1", names
    ).fetchone():
        return True
    try:
        relative = path.resolve().relative_to(media_root.resolve()).as_posix()
    except ValueError:
        relative = None
    if relative is not None and connection.execute(
        "SELECT 1 FROM memes WHERE storage_path = ? LIMIT 1", (relative,)
    ).fetchone():
        return True
    return connection.execute(
        "SELECT 1 FROM sync_uploads WHERE final_path IN (?, ?) AND id != ? LIMIT 1",
        (*names, upload_id),
    ).fetchone() is not None


def _known_duplicate(
    connection: sqlite3.Connection,
    upload_id: str,
    media_id: int,
    device_id: str,
    source: dict[str, str | int],
) -> dict[str, Any]:
    """Settle a reservation whose content the library already has, before any byte is sent.

    The reservation row is kept (state ``duplicate``) so a retry with the same
    client id gets the same answer, and the device is recorded as an origin
    exactly as when the duplicate is only found after the transfer.
    """
    record_origin(connection, media_id, device_id, source)
    sequence = append_change(
        connection, "media", str(media_id), "unchanged", 1,
        {"media_id": media_id, "state": "duplicate"},
    )
    return {
        "upload_id": upload_id, "media_id": media_id, "offset": 0,
        "chunk_size": _MAX_CHUNK_BYTES, "state": "duplicate", "cursor": sequence,
    }


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
