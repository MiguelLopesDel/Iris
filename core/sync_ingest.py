"""Receive and finish many small reserved uploads in one request.

The resumable path costs every photo its own request, temporary file,
offset record, rename, re-read for hashing and several transactions. For
small files those fixed costs, not the bytes, bound how many photos per
second the server takes. Here one request carries a bounded batch of
reserved uploads, each whole, and every step runs once for the batch:

1. admission: one writer transaction checks every reservation and records
   its final library path (state ``receiving``) before any byte is written;
2. the bytes go straight to those paths and are hashed as they arrive;
3. one shared durability barrier syncs the files and their directories;
4. one writer transaction (FULL commit) records them as stored, and the
   catalog takes them as a batch.

Every upload keeps its own outcome. After a crash, an upload left in
``receiving`` is verified from its file by recovery, or reset so the device
sends it again; nothing reaches the catalog before step 4.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sqlite3
import time
from collections.abc import AsyncIterable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool

from core.file_durability import FileDurabilityBusy, FileDurabilityService
from core.sync_db import now_iso
from core.upload_finalization import record_upload_finalized
from core.users_db import IrisUser

if TYPE_CHECKING:
    from core.sync_upload_service import SyncUploadService

_ITEM = re.compile(r"([A-Za-z0-9._-]{1,128}):([0-9]{1,12})")
# The admission and recovery treat these as finished: a retried batch gets
# the recorded answer and its bytes are discarded.
_SETTLED = {"duplicate", "pending_processing", "processing", "ready", "failed_processing"}


@dataclass
class _Item:
    upload_id: str
    size: int
    # Exactly one of these ends up set: an outcome without bytes (error or
    # an already-settled answer), or a destination to write.
    error: Any = None
    answer: dict[str, Any] | None = None
    destination: Path | None = None
    new_directory: bool = False
    filename: str = ""
    expected_hash: str = ""
    captured_at: str = ""
    descriptor: int | None = None
    digest: Any = None
    received: int = 0
    stored: bool = False
    sequence: int | None = None


def parse_ingest_manifest(manifest: str, content_length: int, *, max_items: int, max_bytes: int):
    """The ``upload_id:size`` list a batch declares, in body order."""
    from core.sync_upload_service import SyncUploadError

    entries = [entry.strip() for entry in manifest.split(",") if entry.strip()]
    if not entries or len(entries) > max_items:
        raise SyncUploadError(400, f"O lote deve conter de 1 a {max_items} itens")
    items: list[_Item] = []
    for entry in entries:
        match = _ITEM.fullmatch(entry)
        if match is None or int(match.group(2)) < 1:
            raise SyncUploadError(400, "Item do lote inválido")
        items.append(_Item(match.group(1), int(match.group(2))))
    if len({item.upload_id for item in items}) != len(items):
        raise SyncUploadError(400, "O lote não pode repetir upload_id")
    total = sum(item.size for item in items)
    if total > max_bytes:
        raise SyncUploadError(413, "Lote excede o limite")
    if total != content_length:
        raise SyncUploadError(400, "Tamanho do lote não confere com os itens")
    return items


class SyncIngestPipeline:
    """Orchestrate the batch stages; the rules stay in SyncUploadService."""

    def __init__(self, service: SyncUploadService, durability: FileDurabilityService) -> None:
        self._service = service
        self._durability = durability
        self._policy = service.ingest_policy

    async def ingest(
        self,
        user: IrisUser,
        device_id: str | None,
        manifest: str,
        content_length: int,
        stream: AsyncIterable[bytes],
        *,
        sync_ai_processing: bool,
        load_model: bool,
        on_finished: Callable[[], None],
        log_phase: Callable[..., None],
    ) -> dict[str, list[dict[str, Any]]]:
        from core.sync_upload_service import SyncUploadError

        if not device_id:
            raise SyncUploadError(403, "Use uma sessão de dispositivo para sincronizar mídia")
        items = parse_ingest_manifest(
            manifest, content_length,
            max_items=self._policy.max_items, max_bytes=self._policy.max_bytes,
        )
        started = time.perf_counter()
        count = len(items)

        def phase(name: str, since: float, **fields: Any) -> float:
            log_phase(name, since, item_count=count, **fields)
            return time.perf_counter()

        async with AsyncExitStack() as locks:
            # One fixed order, so two batches sharing uploads cannot deadlock.
            for item in sorted(items, key=lambda entry: entry.upload_id):
                await locks.enter_async_context(
                    self._service._serialize_upload(user.id, device_id, item.upload_id)
                )
            mark = phase("ingest_locks", started, state="ok")
            await run_in_threadpool(self._admit, user, device_id, items)
            mark = phase("ingest_admit", mark, state="durable")
            writing = [item for item in items if item.destination is not None]
            try:
                await run_in_threadpool(_open_targets, writing)
                await self._receive(items, stream)
                await run_in_threadpool(_close_and_verify, writing)
            except BaseException:
                await run_in_threadpool(self._abandon, user, device_id, writing)
                raise
            mark = phase(
                "ingest_receive", mark, bytes=sum(item.size for item in items), state="ok"
            )
            stored = [item for item in writing if item.stored]
            try:
                await asyncio.wrap_future(self._durability.flush(
                    [item.destination for item in stored],
                    _directories_to_sync(stored),
                ))
            except FileDurabilityBusy as exc:
                await run_in_threadpool(self._abandon, user, device_id, writing)
                raise SyncUploadError(503, "Servidor ocupado; tente novamente em instantes") from exc
            except BaseException:
                await run_in_threadpool(self._abandon, user, device_id, writing)
                raise
            mark = phase("ingest_fsync", mark, state="durable")
            log_phase(
                "ingest_durable", started, bytes=sum(item.size for item in stored),
                item_count=len(stored), state="durable",
            )
            await run_in_threadpool(self._commit, user, device_id, writing)
            mark = phase("ingest_commit", mark, state="durable")
            outcomes = await run_in_threadpool(
                self._catalog, user, stored,
                sync_ai_processing, load_model, on_finished, log_phase,
            )
            phase("ingest_catalog", mark, state="ok")
        log_phase("ingest_batch", started, item_count=count, state="ok")
        results = []
        for item in items:
            outcome = outcomes.get(item.upload_id, item.error or item.answer)
            if isinstance(outcome, SyncUploadError):
                results.append({
                    "upload_id": item.upload_id, "error_code": outcome.status_code,
                    "error_message": outcome.detail[:500],
                })
            else:
                results.append(outcome)
        # Clients size their next batch from what the server accepts now.
        return {"uploads": results, "limits": self._service.ingest_limits()}

    def _admit(self, user: IrisUser, device_id: str, items: list[_Item]) -> None:
        """One writer transaction: check each reservation and claim its final path."""
        from core.sync_upload_service import (
            SyncUploadError,
            _known_duplicate,
            _referenced_elsewhere,
            _upload_destinations,
        )

        def admit(connection: sqlite3.Connection) -> None:
            for item in items:
                row = connection.execute(
                    """SELECT filename, expected_size, expected_hash, received_size, temp_path,
                              state, device_id, captured_at, source_id, source_name,
                              source_relative_path, source_volume, source_media_store_id,
                              source_generation, source_media_kind, final_path
                       FROM sync_uploads WHERE id = ?""",
                    (item.upload_id,),
                ).fetchone()
                if row is None or row[6] != device_id:
                    item.error = SyncUploadError(404, "Envio não encontrado")
                    continue
                if row[5] in _SETTLED:
                    item.answer = {"upload_id": item.upload_id, "state": row[5]}
                    continue
                if row[5] not in {"uploading", "receiving"}:
                    item.error = SyncUploadError(409, "Envio não aceita bytes neste estado")
                    continue
                if int(row[1]) != item.size:
                    item.error = SyncUploadError(409, "O lote deve levar o arquivo inteiro")
                    continue
                item.filename, item.expected_hash, item.captured_at = row[0], row[2], row[7]
                media = connection.execute(
                    "SELECT id FROM memes WHERE content_hash = ? LIMIT 1", (row[2],)
                ).fetchone()
                if media is not None:
                    connection.execute(
                        "UPDATE sync_uploads SET state = 'duplicate', updated_at = ? WHERE id = ?",
                        (now_iso(), item.upload_id),
                    )
                    source = {
                        "id": row[8], "name": row[9], "relative_path": row[10],
                        "volume": row[11], "media_store_id": row[12],
                        "generation": row[13], "media_kind": row[14],
                    }
                    item.answer = _known_duplicate(
                        connection, item.upload_id, int(media[0]), device_id, source,
                        chunk_size=self._policy.max_bytes,
                    )
                    continue
                if row[5] == "receiving" and row[15]:
                    destination = Path(row[15])  # this upload's own earlier attempt
                else:
                    directory, primary, alternate = _upload_destinations(
                        user, device_id, item.upload_id, row
                    )
                    if not _referenced_elsewhere(
                        connection, primary, item.upload_id, user.media_root
                    ) and not primary.exists():
                        destination = primary
                    elif not _referenced_elsewhere(
                        connection, alternate, item.upload_id, user.media_root
                    ):
                        destination = alternate  # named after this upload alone
                    else:
                        item.error = SyncUploadError(409, "Destino do envio indisponível")
                        continue
                connection.execute(
                    "UPDATE sync_uploads SET state = 'receiving', final_path = ?, "
                    "received_size = 0, updated_at = ? WHERE id = ?",
                    (str(destination), now_iso(), item.upload_id),
                )
                item.destination = destination

        self._service._submit_write(user, admit)

    async def _receive(self, items: list[_Item], stream: AsyncIterable[bytes]) -> None:
        """Split the body across the items, writing and hashing in large pieces.

        Pieces of several small items are handed to the thread pool together,
        so a batch of photos costs a few thread hops, not several per photo.
        """
        from core.sync_upload_service import SyncUploadError

        block = self._policy.block_bytes
        index, left = 0, items[0].size
        pending: list[tuple[_Item, bytearray]] = []
        pending_bytes = 0

        async def hand_over() -> None:
            nonlocal pending, pending_bytes
            if pending:
                segments = [(item, bytes(data)) for item, data in pending]
                pending, pending_bytes = [], 0
                await run_in_threadpool(_write_segments, segments)

        async for chunk in stream:
            view = memoryview(chunk)
            while view:
                while left == 0:
                    index += 1
                    if index >= len(items):
                        raise SyncUploadError(413, "Lote excede o tamanho declarado")
                    left = items[index].size
                item = items[index]
                take = min(left, len(view), block - pending_bytes)
                if item.destination is not None:
                    if not pending or pending[-1][0] is not item:
                        pending.append((item, bytearray()))
                    pending[-1][1].extend(view[:take])
                    pending_bytes += take
                item.received += take
                view, left = view[take:], left - take
                if pending_bytes >= block:
                    await hand_over()
        if index < len(items) - 1 or left:
            raise SyncUploadError(400, "Lote incompleto")
        await hand_over()

    def _abandon(self, user: IrisUser, device_id: str, writing: list[_Item]) -> None:
        """Remove what a batch that did not finish wrote, and let it be sent again."""
        for item in writing:
            if item.descriptor is not None:
                os.close(item.descriptor)
                item.descriptor = None
            if item.destination is not None and not item.stored:
                item.destination.unlink(missing_ok=True)
        unfinished = [item.upload_id for item in writing if not item.stored]

        def reset(connection: sqlite3.Connection) -> None:
            connection.executemany(
                "UPDATE sync_uploads SET state = 'uploading', final_path = '', "
                "received_size = 0, updated_at = ? WHERE id = ? AND state = 'receiving'",
                [(now_iso(), upload_id) for upload_id in unfinished],
            )

        if unfinished:
            try:
                self._service._submit_write(user, reset)
            except Exception:
                pass  # recovery resets a leftover ``receiving`` upload at start

    def _commit(self, user: IrisUser, device_id: str, writing: list[_Item]) -> None:
        """One writer transaction: stored files become finalized, bad ones failed."""
        from core.sync_upload_service import SyncUploadError

        def commit(connection: sqlite3.Connection) -> None:
            for item in writing:
                if not item.stored:
                    connection.execute(
                        "UPDATE sync_uploads SET state = 'failed', final_path = '', "
                        "updated_at = ? WHERE id = ? AND state = 'receiving'",
                        (now_iso(), item.upload_id),
                    )
                    continue
                moved = connection.execute(
                    "UPDATE sync_uploads SET state = 'finalizing', received_size = ?, "
                    "updated_at = ? WHERE id = ? AND state = 'receiving' AND final_path = ?",
                    (item.size, now_iso(), item.upload_id, str(item.destination)),
                )
                if moved.rowcount != 1:
                    item.stored = False
                    item.error = SyncUploadError(409, "O estado do envio mudou")
                    continue
                item.sequence = record_upload_finalized(
                    connection,
                    upload_id=item.upload_id,
                    filename=item.filename,
                    destination=item.destination,
                    captured_at=item.captured_at,
                    commit=False,
                )

        self._service._submit_write(user, commit)

    def _catalog(
        self,
        user: IrisUser,
        stored: list[_Item],
        sync_ai_processing: bool,
        load_model: bool,
        on_finished: Callable[[], None],
        log_phase: Callable[..., None],
    ) -> dict[str, Any]:
        from core.sync_upload_service import _Finalization

        finalizations = [
            _Finalization(
                upload_id=item.upload_id, filename=item.filename, size=item.size,
                expected_hash=item.expected_hash, captured_at=item.captured_at,
                temporary=item.destination, destination=item.destination,
                sequence=item.sequence,
            )
            for item in stored if item.stored
        ]
        return self._service.register_finalized(
            user, finalizations,
            sync_ai_processing=sync_ai_processing, load_model=load_model,
            on_finished=on_finished, log_phase=log_phase,
        )


def _open_targets(items: list[_Item]) -> None:
    for item in items:
        directory = item.destination.parent
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
            item.new_directory = True
        item.descriptor = os.open(
            item.destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        item.digest = hashlib.sha256()


def _write_segments(segments: list[tuple[_Item, bytes]]) -> None:
    # os.write and sha256.update release the GIL for large buffers, so pieces
    # from concurrent requests are written and hashed on several cores.
    for item, data in segments:
        view = memoryview(data)
        while view:
            written = os.write(item.descriptor, view)
            view = view[written:]
        item.digest.update(data)


def _close_and_verify(items: list[_Item]) -> None:
    from core.sync_upload_service import SyncUploadError

    for item in items:
        os.close(item.descriptor)
        item.descriptor = None
        if item.received == item.size and item.digest.hexdigest() == item.expected_hash:
            item.stored = True
        else:
            item.error = SyncUploadError(422, "Hash do arquivo não confere")
            item.destination.unlink(missing_ok=True)


def _directories_to_sync(items: list[_Item]) -> set[Path]:
    directories: set[Path] = set()
    for item in items:
        directories.add(item.destination.parent)
        if item.new_directory:
            # The new folder's own entry lives in its parent.
            directories.add(item.destination.parent.parent)
    return directories

