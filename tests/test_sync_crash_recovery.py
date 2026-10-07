"""A sync interrupted at any stage ends with the photo stored exactly once.

Two kinds of interruption are simulated at each stage of the upload protocol:

- the server process dies: everything committed so far is kept, since SQLite's
  WAL survives a process crash even without a disk sync;
- the power fails: the database rolls back to its last disk sync (the
  durability barrier), while files that were synced as written stay ahead of
  it. SQLite guarantees that a power loss in WAL mode only loses the most
  recent transactions, in order; restoring the copy taken at the last barrier
  is the worst case of that.

After the interruption a client retries the way the app does, and the photo
must end cataloged once, its file intact, and nothing marked as failed.
"""
from __future__ import annotations

import asyncio
import gc
import hashlib
import io
import sqlite3
import threading
from pathlib import Path

import pytest
from PIL import Image

import core.sync_ingest as ingest_module
import core.sync_upload_service as service_module
from core.ingest_policy import IngestPolicy
from core.sqlite_write_registry import SQLiteWriteCoordinatorRegistry
from core.sync_processor import process_upload, process_uploads
from core.sync_recovery import recover_pending_uploads
from core.sync_upload_service import SyncUploadError, SyncUploadService
from core.users_db import IrisUser

DEVICE = "phone-1"
QUOTA = 1 << 40


class Crash(BaseException):
    """The server stopped here.

    A BaseException, so error handlers (``except Exception``) do not run, as
    they would not when a process dies. ``finally`` blocks still run, which
    is gentler than a real crash only for the catalog lease: it is released
    here instead of expiring later, so recovery does not have to wait.
    """


def _photo(shade: int = 120) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (40, shade, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _user(root: Path) -> IrisUser:
    return IrisUser(
        id=1, username="alice", password_hash="unused", display_name="", is_admin=False,
        db_path=root / "data" / "iris.db", media_root=root / "media", model_name="",
        session_version=1,
    )


class InlineWorkers:
    """Runs the recovery's catalog jobs at once, like the server's worker pool."""

    def submit(self, user, upload_id, file_path, *, use_ai, on_finished):
        process_upload(
            db_path=user.db_path, media_root=user.media_root, model_name=user.model_name,
            upload_id=upload_id, file_path=file_path, on_finished=on_finished, use_ai=use_ai,
            write_registry=self.write_registry,
        )

    def __init__(self, write_registry) -> None:
        self.write_registry = write_registry


class _CommittedFuture:
    def __init__(self, future, after_commit) -> None:
        self._future = future
        self._after_commit = after_commit

    def result(self, *args, **kwargs):
        value = self._future.result(*args, **kwargs)
        self._after_commit()
        return value


class _CrashAwareRegistry:
    def __init__(self, server) -> None:
        self._server = server
        self._inner = SQLiteWriteCoordinatorRegistry(
            coordinator_options={"batch_window_s": 0},
        )

    def submit(self, db_path, callback):
        self._server._maybe_crash("before_full_commit")
        future = self._inner.submit(db_path, callback)
        return _CommittedFuture(future, self._after_commit)

    def _after_commit(self) -> None:
        self._server._take_snapshot()
        self._server._maybe_crash("after_full_commit")

    def shutdown(self, *, timeout=10.0):
        return self._inner.shutdown(timeout=timeout)


class Server:
    """The sync service plus the disk state a power loss would leave behind."""

    def __init__(self, user: IrisUser, monkeypatch) -> None:
        self.user = user
        self.snapshot: Path | None = None
        self.crash_at: str | None = None
        # Another photo's request syncing the disk right after this chunk.
        self.other_request_syncs_after_chunk = False
        self.write_registry = _CrashAwareRegistry(self)
        self._processor_hook = None
        self.service = None
        real_move = service_module.move_upload_into_library

        def move(*args, **kwargs):
            self._maybe_crash("before_move")
            real_move(*args, **kwargs)
            self._maybe_crash("after_move")

        monkeypatch.setattr(service_module, "move_upload_into_library", move)
        real_flush = service_module._flush_upload_buffer

        def flush(output):
            real_flush(output)
            self._maybe_crash("after_chunk_written")

        monkeypatch.setattr(service_module, "_flush_upload_buffer", flush)
        real_open, real_write = ingest_module._open_targets, ingest_module._write_segments
        real_commit = ingest_module.SyncIngestPipeline._commit

        def open_targets(items):
            self._maybe_crash("ingest_admitted")  # paths claimed, nothing written
            real_open(items)

        def write_segments(segments, *args):
            real_write(segments, *args)
            self._maybe_crash("ingest_mid_write")  # some bytes on disk, unsynced

        def commit(pipeline, *args, **kwargs):
            self._maybe_crash("ingest_durable")  # files synced, state not recorded
            real_commit(pipeline, *args, **kwargs)
            self._maybe_crash("ingest_committed")  # recorded, catalog not written

        monkeypatch.setattr(ingest_module, "_open_targets", open_targets)
        monkeypatch.setattr(ingest_module, "_write_segments", write_segments)
        monkeypatch.setattr(ingest_module.SyncIngestPipeline, "_commit", commit)
        self.restart()

    def _processor(self, **kwargs):
        self._maybe_crash("before_catalog")
        result = process_upload(**kwargs)
        self._maybe_crash("after_catalog")
        return result

    def _batch_processor(self, **kwargs):
        self._maybe_crash("before_catalog")
        result = process_uploads(**kwargs)
        self._maybe_crash("after_catalog")
        return result

    def _maybe_crash(self, stage: str) -> None:
        if self.crash_at == stage:
            self.crash_at = None
            raise Crash(stage)

    def _take_snapshot(self) -> None:
        # Through SQLite's backup API, so the copy is consistent while other
        # connections are open.
        self.snapshot = self.user.db_path.parent / "power-loss-snapshot.db"
        live, copy = sqlite3.connect(self.user.db_path), sqlite3.connect(self.snapshot)
        try:
            live.backup(copy)
        finally:
            live.close()
            copy.close()

    def restart(self, *, power_loss: bool = False) -> None:
        gc.collect()  # release connections the crashed request left open
        if self.service is not None:
            assert self.write_registry.shutdown(timeout=2)
            self.write_registry = _CrashAwareRegistry(self)
        if power_loss and self.snapshot is not None:
            # The database returns to its last disk sync. Restored through
            # SQLite as well: a real power loss leaves no connection open, but
            # here some may still be, and overwriting files under them is not
            # what a power loss does.
            saved, live = sqlite3.connect(self.snapshot), sqlite3.connect(self.user.db_path)
            try:
                saved.backup(live)
            finally:
                saved.close()
                live.close()
        service_module._prepared_databases.clear()
        self.service = SyncUploadService(
            upload_processor=self._processor, batch_processor=self._batch_processor,
            write_registry=self.write_registry,
            ingest_policy=IngestPolicy(durability_window_s=0),
        )
        if self.user.db_path.exists():
            # What the server does when it starts: resume persisted work.
            recover_pending_uploads(
                users_db_path=self.user.db_path.parent / "users.db",
                sync_ai_processing=False, load_model=False, on_finished=lambda *_: None,
                stop_event=threading.Event(), processing_workers=InlineWorkers(self.write_registry),
                write_registry=self.write_registry,
                users=[self.user],
            )

    # The protocol, as the app calls it.
    def reserve(self, digest: str, size: int, client_upload_id: str = "client-1") -> dict:
        return self.service.reserve_upload_batch(self.user, DEVICE, QUOTA, {"uploads": [{
            "client_upload_id": client_upload_id, "filename": "IMG_0001.jpg", "size": size,
            "sha256": digest, "captured_at": "2026-09-09T12:00:00Z",
        }]})["uploads"][0]

    def put(self, upload_id: str, offset: int, data: bytes) -> dict:
        async def stream():
            yield data[offset:]

        def log_phase(phase, *args, **kwargs):
            if phase == "chunk_durable" and self.other_request_syncs_after_chunk:
                # Chunk progress already crossed the writer's FULL commit.
                self._take_snapshot()

        return asyncio.run(self.service.receive_chunk(
            self.user, DEVICE, upload_id, offset, len(data) - offset, stream(), log_phase,
        ))

    def status(self, upload_id: str) -> dict:
        return self.service.upload_status(self.user, DEVICE, upload_id)

    def ingest(self, entries: list[tuple[str, bytes]]) -> list[dict]:
        async def body():
            for _, data in entries:
                yield data

        manifest = ",".join(f"{upload_id}:{len(data)}" for upload_id, data in entries)
        return asyncio.run(self.service.ingest_pipeline.ingest(
            self.user, DEVICE, manifest, sum(len(data) for _, data in entries), body(),
            sync_ai_processing=False, load_model=False,
            on_finished=lambda: None, log_phase=lambda *a, **k: None,
        ))["uploads"]

    def complete(self, upload_id: str) -> dict:
        return self.complete_many([upload_id])[0]

    def complete_many(self, upload_ids: list[str]) -> list[dict]:
        return asyncio.run(self.service.complete_upload_batch(
            self.user, DEVICE, {"uploads": [{"upload_id": value} for value in upload_ids]},
            sync_ai_processing=False, load_model=False,
            on_finished=lambda: None, log_phase=lambda *a, **k: None,
        ))["uploads"]


def _sync_like_the_app(
    server: Server, data: bytes, *, power_loss: bool, client_upload_id: str = "client-1",
) -> str:
    """Retry the protocol until the server confirms the photo; returns the final state."""
    digest = hashlib.sha256(data).hexdigest()
    upload_id: str | None = None
    for _ in range(8):
        crashed = False
        try:
            if upload_id is None:
                reserved = server.reserve(digest, len(data), client_upload_id)
                if reserved.get("state") == "duplicate":
                    return "duplicate"
                upload_id = reserved["upload_id"]
            status = server.status(upload_id)
            if status["state"] in {"ready", "duplicate"}:
                return status["state"]
            if status["state"] == "uploading" and status["offset"] < len(data):
                server.put(upload_id, status["offset"], data)
            result = server.complete(upload_id)
            if result.get("state") in {"ready", "duplicate"}:
                return result["state"]
        except Crash:
            crashed = True
        except SyncUploadError as exc:
            if exc.status_code == 404:
                upload_id = None  # the server lost the reservation; reserve again
        if crashed:
            # Outside the except block, so the crashed request's frames (and
            # the connections they hold) are released before the restart.
            server.restart(power_loss=power_loss)
    raise AssertionError("the photo was never confirmed")


STAGES = [
    "before_full_commit",    # no transaction changes have reached durable storage
    "after_full_commit",     # reservation durable, response lost
    "after_chunk_written",   # chunk synced to its file, database not updated
    "before_move",           # finalizing claimed, file still in the upload area
    "after_move",            # file moved into the library, catalog not written
    "before_catalog",        # finalization recorded, catalog not written
    "after_catalog",         # cataloged, completion response not yet returned
]


@pytest.mark.parametrize("power_loss", [False, True], ids=["process-crash", "power-loss"])
@pytest.mark.parametrize("stage", STAGES)
def test_an_interrupted_sync_stores_the_photo_exactly_once(tmp_path: Path, monkeypatch, stage, power_loss):
    user = _user(tmp_path)
    server = Server(user, monkeypatch)
    data = _photo()
    server.crash_at = stage
    # The first full-commit stages fire during reservation; later hooks target completion.
    state = _sync_like_the_app(server, data, power_loss=power_loss)

    assert state == "ready"
    assert server.crash_at is None, f"the flow never reached {stage}"
    conn = sqlite3.connect(user.db_path)
    rows = conn.execute(
        "SELECT caminho FROM memes WHERE content_hash = ?", (hashlib.sha256(data).hexdigest(),)
    ).fetchall()
    states = [row[0] for row in conn.execute("SELECT state FROM sync_uploads")]
    conn.close()
    assert len(rows) == 1, "the photo must be cataloged exactly once"
    stored = Path(rows[0][0])
    assert stored.is_file() and stored.read_bytes() == data
    assert "failed" not in states and "failed_processing" not in states
    copies = [path for path in user.media_root.rglob("*") if path.is_file() and path.read_bytes() == data]
    assert copies == [stored], "no orphan copy of the photo may be left in the library"
    leftovers = [path for path in (user.db_path.parent / "sync_uploads").rglob("*") if path.is_file()]
    assert leftovers == [], "no upload leftovers may remain"


def test_a_confirmed_photo_survives_a_power_loss_right_after(tmp_path: Path, monkeypatch):
    # Once the device is told "ready", the next power loss must not undo it.
    user = _user(tmp_path)
    server = Server(user, monkeypatch)
    data = _photo()
    assert _sync_like_the_app(server, data, power_loss=True) == "ready"

    server.restart(power_loss=True)

    conn = sqlite3.connect(user.db_path)
    assert conn.execute("SELECT state FROM sync_uploads").fetchone()[0] == "ready"
    assert conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize("stage", ["before_move", "after_move", "before_catalog", "after_catalog"])
def test_a_power_loss_after_another_requests_sync_still_stores_the_photo(tmp_path: Path, monkeypatch, stage):
    # Photos upload in parallel, so another request may sync the disk between
    # this photo's last chunk and its completion. A power loss then rolls the
    # database back to "fully received" while the file may already have been
    # moved out of the upload area.
    user = _user(tmp_path)
    server = Server(user, monkeypatch)
    server.other_request_syncs_after_chunk = True
    data = _photo()
    server.crash_at = stage

    assert _sync_like_the_app(server, data, power_loss=True) == "ready"

    conn = sqlite3.connect(user.db_path)
    rows = conn.execute("SELECT caminho FROM memes").fetchall()
    states = [row[0] for row in conn.execute("SELECT state FROM sync_uploads")]
    conn.close()
    assert len(rows) == 1 and Path(rows[0][0]).read_bytes() == data
    assert states == ["ready"]
    copies = [path for path in user.media_root.rglob("*") if path.is_file() and path.read_bytes() == data]
    assert copies == [Path(rows[0][0])]


def _cataloged(user: IrisUser) -> list[Path]:
    conn = sqlite3.connect(user.db_path)
    rows = [Path(row[0]) for row in conn.execute("SELECT caminho FROM memes")]
    conn.close()
    return rows


def test_recovering_a_lost_upload_never_deletes_another_uploads_original(tmp_path: Path, monkeypatch):
    # Two uploads of the same photo share the usual library name. When the
    # second one's temporary file is gone, the copy found there is the first
    # one's cataloged original, not something to clean up.
    user = _user(tmp_path)
    server = Server(user, monkeypatch)
    data = _photo()
    digest = hashlib.sha256(data).hexdigest()
    first = server.reserve(digest, len(data), "client-1")
    second = server.reserve(digest, len(data), "client-2")
    server.put(first["upload_id"], 0, data)
    server.put(second["upload_id"], 0, data)
    assert server.complete(first["upload_id"])["state"] == "ready"
    original = _cataloged(user)[0]
    temporary = Path(sqlite3.connect(user.db_path).execute(
        "SELECT temp_path FROM sync_uploads WHERE id = ?", (second["upload_id"],)
    ).fetchone()[0])
    temporary.unlink()  # the second upload's temporary file is lost

    assert server.complete(second["upload_id"])["state"] == "duplicate"
    assert _cataloged(user) == [original]
    assert original.read_bytes() == data


def test_two_uploads_of_the_same_photo_never_share_one_file(tmp_path: Path, monkeypatch):
    # The first upload is stored but not cataloged yet when the second one
    # completes. Sharing its file would let the later catalog step delete it
    # as the duplicate's original.
    user = _user(tmp_path)
    server = Server(user, monkeypatch)
    data = _photo()
    digest = hashlib.sha256(data).hexdigest()
    first = server.reserve(digest, len(data), "client-1")
    server.put(first["upload_id"], 0, data)
    server.crash_at = "before_catalog"
    with pytest.raises(Crash):
        server.complete(first["upload_id"])

    second = server.reserve(digest, len(data), "client-2")
    server.put(second["upload_id"], 0, data)
    assert server.complete(second["upload_id"])["state"] == "ready"
    server.restart()  # the first upload is cataloged by recovery

    cataloged = _cataloged(user)
    assert len(cataloged) == 1
    assert cataloged[0].read_bytes() == data


@pytest.mark.parametrize("power_loss", [False, True], ids=["process-crash", "power-loss"])
@pytest.mark.parametrize("stage", ["before_move", "after_move", "before_catalog", "after_catalog"])
def test_a_batch_interrupted_midway_stores_every_photo_exactly_once(
    tmp_path: Path, monkeypatch, stage, power_loss,
):
    # A batch is claimed together, moved together and recorded together: a
    # stop between those steps leaves some photos claimed, some moved and some
    # cataloged at once, and each must still end stored once.
    user = _user(tmp_path)
    server = Server(user, monkeypatch)
    photos = [_photo(shade) for shade in (10, 90, 170)]
    ids = []
    for index, data in enumerate(photos):
        reserved = server.reserve(hashlib.sha256(data).hexdigest(), len(data), f"client-{index}")
        server.put(reserved["upload_id"], 0, data)
        ids.append(reserved["upload_id"])
    server.crash_at = stage
    try:
        server.complete_many(ids)
    except Crash:
        pass
    assert server.crash_at is None, f"the batch never reached {stage}"
    server.restart(power_loss=power_loss)

    for index, data in enumerate(photos):
        state = _sync_like_the_app(
            server, data, power_loss=power_loss, client_upload_id=f"client-{index}"
        )
        assert state == "ready"

    conn = sqlite3.connect(user.db_path)
    cataloged = [Path(row[0]) for row in conn.execute("SELECT caminho FROM memes")]
    states = [row[0] for row in conn.execute("SELECT state FROM sync_uploads")]
    conn.close()
    assert sorted(path.read_bytes() for path in cataloged) == sorted(photos)
    assert "failed" not in states and "failed_processing" not in states
    stored = sorted(path for path in user.media_root.rglob("*") if path.is_file())
    assert stored == sorted(cataloged), "no orphan copy may be left in the library"
    leftovers = [path for path in (user.db_path.parent / "sync_uploads").rglob("*") if path.is_file()]
    assert leftovers == []


def _ingest_like_the_app(server: Server, photos: list[bytes], *, power_loss: bool) -> list[str]:
    """Reserve, then send whatever is not confirmed in one batch, until all are."""
    clients = [f"client-{index}" for index in range(len(photos))]
    ids: dict[str, str] = {}
    for _ in range(8):
        crashed = False
        try:
            for client, data in zip(clients, photos, strict=True):
                if client not in ids:
                    reserved = server.reserve(hashlib.sha256(data).hexdigest(), len(data), client)
                    ids[client] = reserved["upload_id"]
            states = {client: server.status(ids[client]) for client in clients}
            pending = [
                (ids[client], data) for client, data in zip(clients, photos, strict=True)
                if states[client]["state"] not in {"ready", "duplicate"}
            ]
            if not pending:
                return [states[client]["state"] for client in clients]
            server.ingest(pending)
        except Crash:
            crashed = True
        except SyncUploadError as exc:
            if exc.status_code == 404:
                ids.clear()  # the server lost the reservations; reserve again
        if crashed:
            server.restart(power_loss=power_loss)
    raise AssertionError("the batch was never confirmed")


INGEST_STAGES = [
    "ingest_admitted",
    "ingest_mid_write",
    "ingest_durable",
    "ingest_committed",
    "before_catalog",
    "after_catalog",
]


@pytest.mark.parametrize("power_loss", [False, True], ids=["process-crash", "power-loss"])
@pytest.mark.parametrize("stage", INGEST_STAGES)
def test_a_batch_ingest_interrupted_anywhere_stores_every_photo_once(
    tmp_path: Path, monkeypatch, stage, power_loss,
):
    user = _user(tmp_path)
    server = Server(user, monkeypatch)
    photos = [_photo(shade) for shade in (15, 95, 175)]
    server.crash_at = stage

    states = _ingest_like_the_app(server, photos, power_loss=power_loss)

    assert states == ["ready"] * 3, states
    assert server.crash_at is None, f"the batch never reached {stage}"
    conn = sqlite3.connect(user.db_path)
    cataloged = [Path(row[0]) for row in conn.execute("SELECT caminho FROM memes")]
    upload_states = [row[0] for row in conn.execute("SELECT state FROM sync_uploads")]
    conn.close()
    assert sorted(path.read_bytes() for path in cataloged) == sorted(photos)
    assert set(upload_states) == {"ready"}, upload_states
    stored = sorted(path for path in user.media_root.rglob("*") if path.is_file())
    assert stored == sorted(cataloged), "no partial or orphan copy may be left in the library"
