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

import core.sync_upload_service as service_module
from core import sync_durability
from core.sync_processor import process_upload
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


def _photo() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (40, 120, 200)).save(buffer, format="JPEG")
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
        )


class Server:
    """The sync service plus the disk state a power loss would leave behind."""

    def __init__(self, user: IrisUser, monkeypatch) -> None:
        self.user = user
        self.snapshot: Path | None = None
        self.crash_at: str | None = None
        # Another photo's request syncing the disk right after this chunk.
        self.other_request_syncs_after_chunk = False
        self._real_make_durable = sync_durability.make_durable
        self._processor_hook = None
        real_make_durable = sync_durability.make_durable

        def make_durable(db_path):
            self._maybe_crash("before_barrier")
            real_make_durable(db_path)
            self._take_snapshot()
            self._maybe_crash("after_barrier")

        monkeypatch.setattr(service_module, "make_durable", make_durable)
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
        self.restart()

    def _processor(self, **kwargs):
        self._maybe_crash("before_catalog")
        result = process_upload(**kwargs)
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
        self.service = SyncUploadService(upload_processor=self._processor)
        if self.user.db_path.exists():
            # What the server does when it starts: resume persisted work.
            recover_pending_uploads(
                users_db_path=self.user.db_path.parent / "users.db",
                sync_ai_processing=False, load_model=False, on_finished=lambda *_: None,
                stop_event=threading.Event(), processing_workers=InlineWorkers(),
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
                self._real_make_durable(self.user.db_path)
                self._take_snapshot()

        return asyncio.run(self.service.receive_chunk(
            self.user, DEVICE, upload_id, offset, len(data) - offset, stream(), log_phase,
        ))

    def status(self, upload_id: str) -> dict:
        return self.service.upload_status(self.user, DEVICE, upload_id)

    def complete(self, upload_id: str) -> dict:
        return asyncio.run(self.service.complete_upload_batch(
            self.user, DEVICE, {"uploads": [{"upload_id": upload_id}]},
            sync_ai_processing=False, load_model=False,
            on_finished=lambda: None, log_phase=lambda *a, **k: None,
        ))["uploads"][0]


def _sync_like_the_app(server: Server, data: bytes, *, power_loss: bool) -> str:
    """Retry the protocol until the server confirms the photo; returns the final state."""
    digest = hashlib.sha256(data).hexdigest()
    upload_id: str | None = None
    for _ in range(8):
        crashed = False
        try:
            if upload_id is None:
                reserved = server.reserve(digest, len(data))
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
    "before_barrier",        # reserve committed, never confirmed to the device
    "after_barrier",         # reserve confirmed on disk, answer lost
    "after_chunk_written",   # chunk synced to its file, database not updated
    "before_move",           # finalizing claimed, file still in the upload area
    "after_move",            # file moved into the library, catalog not written
    "before_catalog",        # finalization recorded, catalog not written
    "after_catalog",         # cataloged, completion not yet synced to disk
]


@pytest.mark.parametrize("power_loss", [False, True], ids=["process-crash", "power-loss"])
@pytest.mark.parametrize("stage", STAGES)
def test_an_interrupted_sync_stores_the_photo_exactly_once(tmp_path: Path, monkeypatch, stage, power_loss):
    user = _user(tmp_path)
    server = Server(user, monkeypatch)
    data = _photo()
    server.crash_at = stage
    # Barrier stages fire at the reserve; completion stages fire later in the flow.
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
