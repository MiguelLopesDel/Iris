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
import uuid
from collections.abc import AsyncIterable, Callable
from contextlib import AsyncExitStack, closing
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
    written: int = 0
    stored: bool = False
    sequence: int | None = None
    # Set when the batch's commit also cataloged the upload.
    outcome: dict[str, Any] | None = None


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
                waited, wrote = await self._receive(items, stream)
                await run_in_threadpool(_close_and_verify, writing)
            except BaseException:
                await run_in_threadpool(self._abandon, user, device_id, writing)
                raise
            mark = phase(
                "ingest_receive", mark, bytes=sum(item.size for item in items), state="ok"
            )
            # Within receiving: waiting for the client's bytes, and handing them
            # to the disk and the hash.
            now = time.perf_counter()
            phase("ingest_receive_wait", now - waited, state="ok")
            phase("ingest_receive_write", now - wrote, state="ok")
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
            inline = not (sync_ai_processing and load_model)
            await run_in_threadpool(self._commit, user, device_id, writing, inline, on_finished)
            mark = phase("ingest_commit", mark, state="durable")
            outcomes = {item.upload_id: item.outcome for item in stored if item.outcome}
            outcomes.update(await run_in_threadpool(
                self._catalog, user, [item for item in stored if not item.outcome],
                sync_ai_processing, load_model, on_finished, log_phase,
            ))
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
        """Check each reservation and claim its final path, writing only if needed.

        Uploads reserved for batch ingest were admitted by their reservation's
        own FULL commit: a read confirms it and nothing is written. Anything
        else (a reset, a duplicate found since, an older client) takes the
        admission transaction below.
        """
        if self._already_admitted(user, device_id, items):
            return
        self._admit_in_writer(user, device_id, items)

    def _already_admitted(self, user: IrisUser, device_id: str, items: list[_Item]) -> bool:
        ids = [item.upload_id for item in items]
        with closing(self._service.open_connection(user)) as connection:
            rows = {
                row[0]: row[1:]
                for row in _select_in(
                    connection,
                    "SELECT id, device_id, state, expected_size, final_path, filename, "
                    "expected_hash, captured_at FROM sync_uploads WHERE id IN ({marks})",
                    ids,
                )
            }
            hashes = sorted({row[5] for row in rows.values()})
            known = {row[0] for row in _select_in(
                connection, "SELECT content_hash FROM memes WHERE content_hash IN ({marks})", hashes,
            )}
        for item in items:
            row = rows.get(item.upload_id)
            if (
                row is None or row[0] != device_id or row[1] != "receiving" or not row[3]
                or int(row[2]) != item.size or row[5] in known
            ):
                return False
        for item in items:
            row = rows[item.upload_id]
            item.destination = Path(row[3])
            item.filename, item.expected_hash, item.captured_at = row[4], row[5], row[6]
        return True

    def _admit_in_writer(self, user: IrisUser, device_id: str, items: list[_Item]) -> None:
        """One writer transaction: check each reservation and claim its final path.

        Lookups run once for the whole batch (reservations, known content,
        and whether any record already points to a destination), so the
        admission's cost barely grows with the batch.
        """
        from core.sync_upload_service import (
            SyncUploadError,
            _known_duplicate,
            _referenced_elsewhere,
            _upload_destinations,
        )

        ids = [item.upload_id for item in items]

        def admit(connection: sqlite3.Connection) -> None:
            rows = {
                row[0]: row[1:]
                for row in _select_in(
                    connection,
                    """SELECT id, filename, expected_size, expected_hash, received_size, temp_path,
                              state, device_id, captured_at, source_id, source_name,
                              source_relative_path, source_volume, source_media_store_id,
                              source_generation, source_media_kind, final_path
                       FROM sync_uploads WHERE id IN ({marks})""",
                    ids,
                )
            }
            candidates: list[tuple[_Item, Any]] = []
            for item in items:
                row = rows.get(item.upload_id)
                if row is None or row[6] != device_id:
                    item.error = SyncUploadError(404, "Envio não encontrado")
                elif row[5] in _SETTLED:
                    item.answer = {"upload_id": item.upload_id, "state": row[5]}
                elif row[5] not in {"uploading", "receiving"}:
                    item.error = SyncUploadError(409, "Envio não aceita bytes neste estado")
                elif int(row[1]) != item.size:
                    item.error = SyncUploadError(409, "O lote deve levar o arquivo inteiro")
                else:
                    item.filename, item.expected_hash, item.captured_at = row[0], row[2], row[7]
                    candidates.append((item, row))
            known = dict(_select_in(
                connection,
                "SELECT content_hash, id FROM memes WHERE content_hash IN ({marks})",
                sorted({row[2] for _, row in candidates}),
            ))
            fresh: list[tuple[_Item, Any, Path, Path]] = []
            for item, row in candidates:
                media_id = known.get(row[2])
                if media_id is not None:
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
                        connection, item.upload_id, int(media_id), device_id, source,
                        chunk_size=self._policy.max_bytes,
                    )
                elif row[5] == "receiving" and row[15]:
                    item.destination = Path(row[15])  # this upload's own earlier attempt
                else:
                    _directory, primary, alternate = _upload_destinations(
                        user, device_id, item.upload_id, row
                    )
                    fresh.append((item, row, primary, alternate))
            taken = _paths_in_use(
                connection, [(item.upload_id, primary) for item, _, primary, _ in fresh],
                user.media_root,
            )
            for item, _row, primary, alternate in fresh:
                if str(primary) not in taken and not primary.exists():
                    item.destination = primary
                elif not _referenced_elsewhere(
                    connection, alternate, item.upload_id, user.media_root
                ):
                    item.destination = alternate  # named after this upload alone
                else:
                    item.error = SyncUploadError(409, "Destino do envio indisponível")
                    continue
                # Two copies of one photo in a batch share the usual name: the
                # second one takes its own.
                taken.add(str(item.destination))
            timestamp = now_iso()
            connection.executemany(
                "UPDATE sync_uploads SET state = 'receiving', final_path = ?, "
                "received_size = 0, updated_at = ? WHERE id = ?",
                [
                    (str(item.destination), timestamp, item.upload_id)
                    for item in items if item.destination is not None
                ],
            )

        self._service._submit_write(user, admit)

    async def _receive(
        self, items: list[_Item], stream: AsyncIterable[bytes],
    ) -> tuple[float, float]:
        """Split the body across the items, writing and hashing in large pieces.

        Pieces of several small items are handed to the thread pool together,
        so a batch of photos costs a few thread hops, not several per photo.
        Returns the seconds spent waiting for the body and handing it over.
        """
        from core.sync_upload_service import SyncUploadError

        block = self._policy.block_bytes
        index, left = 0, items[0].size
        pending: list[tuple[_Item, bytearray]] = []
        pending_bytes = 0
        waited = wrote = 0.0

        async def hand_over() -> None:
            nonlocal pending, pending_bytes, wrote
            if pending:
                segments = [(item, bytes(data)) for item, data in pending]
                pending, pending_bytes = [], 0
                started = time.perf_counter()
                await run_in_threadpool(_write_segments, segments)
                wrote += time.perf_counter() - started

        chunks = stream.__aiter__()
        while True:
            started = time.perf_counter()
            try:
                chunk = await chunks.__anext__()
            except StopAsyncIteration:
                waited += time.perf_counter() - started
                break
            waited += time.perf_counter() - started
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
        return waited, wrote

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

    def _commit(
        self,
        user: IrisUser,
        device_id: str,
        writing: list[_Item],
        inline_catalog: bool = False,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        """One writer transaction: stored files become finalized, bad ones failed.

        With ``inline_catalog`` the same transaction also catalogs them: the
        batch already owns these uploads, so the separate claim, catalog and
        release transactions (three FULL commits) are not needed. Each upload
        is cataloged inside its own savepoint; one that fails stays
        ``pending_processing`` and goes through the usual processing path.
        """
        from core.media_ingest import _ingest_one, _prepare_uploads
        from core.sync_upload_service import SyncUploadError
        from core.upload_catalog_writer import UploadCatalogWriter

        media_root = user.media_root.resolve()
        prepared = {}
        if inline_catalog:
            for item in writing:
                if item.stored:
                    try:
                        prepared[item.upload_id] = _prepare_uploads(
                            media_root, [(item.upload_id, item.destination)]
                        )[0]
                    except OSError:
                        pass  # cataloged later by the usual path, which records why
        token = f"ingest-{uuid.uuid4().hex}"
        duplicates: list[Path] = []

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
                if item.sequence is None or item.upload_id not in prepared:
                    continue
                # A nested savepoint, released or rolled back here, inside the
                # writer's own: it never ends the writer's transaction.
                connection.execute("SAVEPOINT iris_ingest_catalog")
                previous_factory = connection.row_factory
                try:
                    connection.execute(
                        "UPDATE sync_uploads SET state = 'processing', processing_lease_token = ?, "
                        "processing_lease_until = '', processing_attempts = processing_attempts + 1, "
                        "processing_next_attempt_at = '', updated_at = ? "
                        "WHERE id = ? AND state = 'pending_processing'",
                        (token, now_iso(), item.upload_id),
                    )
                    connection.row_factory = sqlite3.Row
                    result = _ingest_one(
                        connection, UploadCatalogWriter(connection), media_root, token,
                        prepared[item.upload_id],
                    )
                    connection.execute(
                        "UPDATE sync_uploads SET processing_lease_token = NULL, "
                        "processing_lease_until = '' WHERE id = ? AND processing_lease_token = ?",
                        (item.upload_id, token),
                    )
                    connection.execute("RELEASE SAVEPOINT iris_ingest_catalog")
                except Exception:
                    connection.execute("ROLLBACK TO SAVEPOINT iris_ingest_catalog")
                    connection.execute("RELEASE SAVEPOINT iris_ingest_catalog")
                    continue
                finally:
                    connection.row_factory = previous_factory
                item.outcome = result
                if result["state"] == "duplicate":
                    duplicates.append(item.destination)

        # Not a FULL commit: the uploads' "receiving" rows (final path, size,
        # hash) and their files are already on disk, so after a power loss
        # recovery rebuilds this commit from them. The writer syncs relaxed
        # commits within a second, or with the next durable one.
        self._service._submit_write(user, commit, durable=False)
        # After the commit: the catalog points to the kept copy only.
        for path in duplicates:
            path.unlink(missing_ok=True)
        if on_finished is not None and any(item.outcome for item in writing):
            on_finished()

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
    # Directories now; each file is opened by its first bytes and closed by
    # its last, so a batch keeps one or two files open, not all of them (with
    # several large batches in flight, all of them ran out of descriptors).
    for item in items:
        directory = item.destination.parent
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
            item.new_directory = True
        item.digest = hashlib.sha256()


def _write_segments(segments: list[tuple[_Item, bytes]]) -> None:
    # os.write and sha256.update release the GIL for large buffers, so pieces
    # from concurrent requests are written and hashed on several cores.
    for item, data in segments:
        if item.descriptor is None:
            item.descriptor = os.open(
                item.destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
        view = memoryview(data)
        while view:
            written = os.write(item.descriptor, view)
            view = view[written:]
        item.digest.update(data)
        item.written += len(data)
        if item.written == item.size:
            os.close(item.descriptor)
            item.descriptor = None


def _close_and_verify(items: list[_Item]) -> None:
    from core.sync_upload_service import SyncUploadError

    for item in items:
        if item.descriptor is not None:
            os.close(item.descriptor)
            item.descriptor = None
        if item.written == item.size and item.digest.hexdigest() == item.expected_hash:
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


def _select_in(connection: sqlite3.Connection, sql: str, values: list[Any]) -> list[Any]:
    """Run ``sql`` with ``{marks}`` bound to ``values``, in chunks SQLite accepts."""
    rows: list[Any] = []
    for start in range(0, len(values), 500):
        chunk = values[start:start + 500]
        rows.extend(connection.execute(sql.format(marks=", ".join("?" * len(chunk))), chunk))
    return rows


def _paths_in_use(
    connection: sqlite3.Connection, claims: list[tuple[str, Path]], media_root: Path,
) -> set[str]:
    """Which of the claimed library paths a catalog row or another upload points to.

    Answers for the whole batch at once: the same three questions as
    ``_referenced_elsewhere`` (absolute path, path relative to the library,
    another upload's final path), each a single indexed lookup.
    """
    if not claims:
        return set()
    resolved_root = media_root.resolve()
    resolved_dirs: dict[Path, Path] = {}
    forms: dict[str, str] = {}  # every spelling of a path -> the path as claimed
    relative: dict[str, str] = {}
    owner: dict[str, str] = {}
    for upload_id, path in claims:
        directory = resolved_dirs.setdefault(path.parent, path.parent.resolve())
        resolved = directory / path.name
        forms[str(path)] = forms[str(resolved)] = str(path)
        owner[str(path)] = upload_id
        try:
            relative[resolved.relative_to(resolved_root).as_posix()] = str(path)
        except ValueError:
            pass
    taken = {forms[row[0]] for row in _select_in(
        connection, "SELECT caminho FROM memes WHERE caminho IN ({marks})", list(forms)
    )}
    taken |= {relative[row[0]] for row in _select_in(
        connection, "SELECT storage_path FROM memes WHERE storage_path IN ({marks})", list(relative)
    )}
    for path, upload_id in _select_in(
        connection, "SELECT final_path, id FROM sync_uploads WHERE final_path IN ({marks})", list(forms)
    ):
        if upload_id != owner[forms[path]]:
            taken.add(forms[path])
    return taken


def plan_destinations(
    connection: sqlite3.Connection,
    user: IrisUser,
    device_id: str,
    planned: list[tuple[str, dict[str, Any]]],
) -> None:
    """Admit new reservations for batch ingest, inside the reservation's transaction.

    Each gets its final library path and state ``receiving``, made durable by
    the reservation's own FULL commit, so the ingest request writes nothing
    before its bytes. One that cannot get a path stays ``uploading`` and is
    admitted by the ingest request instead.
    """
    from core.sync_upload_service import _referenced_elsewhere, _upload_destinations

    choices = []
    for upload_id, item in planned:
        row = (
            item["filename"], item["size"], item["sha256"], 0, "", "uploading", device_id,
            item["captured_at"], item["source"]["id"],
        )
        _directory, primary, alternate = _upload_destinations(user, device_id, upload_id, row)
        choices.append((upload_id, primary, alternate))
    taken = _paths_in_use(
        connection, [(upload_id, primary) for upload_id, primary, _ in choices], user.media_root,
    )
    updates = []
    timestamp = now_iso()
    for upload_id, primary, alternate in choices:
        if str(primary) not in taken and not primary.exists():
            destination = primary
        elif not _referenced_elsewhere(connection, alternate, upload_id, user.media_root):
            destination = alternate
        else:
            continue
        taken.add(str(destination))
        updates.append((str(destination), timestamp, upload_id))
    connection.executemany(
        "UPDATE sync_uploads SET state = 'receiving', final_path = ?, updated_at = ? "
        "WHERE id = ? AND state = 'uploading'",
        updates,
    )
