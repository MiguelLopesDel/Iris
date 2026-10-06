"""A completion batch is finished together: few transactions, one sync per directory.

Finishing the uploads of a batch side by side made each one queue for
SQLite's single writer and for the catalog several times, and sync two
directories of its own; on a spinning disk that waiting, not the work,
bounded how many photos per second a backup finished.
"""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from pathlib import Path

import core.sync_file_ops as file_ops
import core.sync_upload_service as service_module
from core.sync_upload_service import SyncUploadService
from core.users_db import IrisUser

DEVICE = "phone-1"


def _user(root: Path) -> IrisUser:
    return IrisUser(
        id=1, username="alice", password_hash="unused", display_name="", is_admin=False,
        db_path=root / "data" / "iris.db", media_root=root / "media", model_name="", session_version=1,
    )


def _photo(index: int) -> bytes:
    return b"\xff\xd8" + bytes([index % 256]) * 3000


def _upload(service, user, photos: list[bytes], prefix: str = "item") -> list[str]:
    reserved = service.reserve_upload_batch(user, DEVICE, 1 << 40, {"uploads": [{
        "client_upload_id": f"{prefix}-{index}", "filename": f"IMG_{index:04d}.jpg", "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "captured_at": "2026-09-09T12:00:00Z",
    } for index, data in enumerate(photos)]})["uploads"]
    ids = []
    for entry, data in zip(reserved, photos, strict=True):
        async def body(payload=data):
            yield payload

        asyncio.run(service.receive_chunk(
            user, DEVICE, entry["upload_id"], 0, len(data), body(), lambda *a, **k: None,
        ))
        ids.append(entry["upload_id"])
    return ids


def _complete(service, user, upload_ids: list[str]) -> list[dict]:
    return asyncio.run(service.complete_upload_batch(
        user, DEVICE, {"uploads": [{"upload_id": value} for value in upload_ids]},
        sync_ai_processing=False, load_model=False, on_finished=lambda: None,
        log_phase=lambda *a, **k: None,
    ))["uploads"]


def _cataloged(user: IrisUser) -> list[Path]:
    with sqlite3.connect(user.db_path) as conn:
        return [Path(row[0]) for row in conn.execute("SELECT caminho FROM memes")]


def test_a_batch_syncs_each_directory_once_after_moving_and_before_recording(
    tmp_path: Path, monkeypatch,
):
    events: list[str] = []
    monkeypatch.setattr(service_module, "fsync_directory", lambda path: events.append("sync"))
    monkeypatch.setattr(file_ops, "fsync_directory", lambda path: events.append("sync"))
    real_move, real_record = service_module.move_upload_into_library, service_module.record_upload_finalized

    def move(*args, **kwargs):
        events.append("move")
        return real_move(*args, **kwargs)

    def record(*args, **kwargs):
        events.append("record")
        return real_record(*args, **kwargs)

    user, service = _user(tmp_path), SyncUploadService()
    ids = _upload(service, user, [_photo(index) for index in range(6)])
    monkeypatch.setattr(service_module, "move_upload_into_library", move)
    monkeypatch.setattr(service_module, "record_upload_finalized", record)
    events.clear()

    assert [entry["state"] for entry in _complete(service, user, ids)] == ["ready"] * 6

    # Six moves, then the library folder and the upload area synced once
    # each (not twice per photo), and only then the moves recorded.
    assert events == ["move"] * 6 + ["sync"] * 2 + ["record"] * 6


def test_each_upload_of_a_batch_keeps_its_own_outcome(tmp_path: Path):
    user, service = _user(tmp_path), SyncUploadService()
    good, corrupt, short = _photo(1), _photo(2), _photo(3)
    ids = _upload(service, user, [good, corrupt])
    temporary = {
        row[0]: Path(row[1])
        for row in sqlite3.connect(user.db_path).execute("SELECT id, temp_path FROM sync_uploads")
    }
    temporary[ids[1]].write_bytes(corrupt[:-1] + b"\x00")  # damaged after its hash was declared
    [incomplete] = service.reserve_upload_batch(user, DEVICE, 1 << 40, {"uploads": [{
        "client_upload_id": "short", "filename": "IMG_0009.jpg", "size": len(short),
        "sha256": hashlib.sha256(short).hexdigest(), "captured_at": "2026-09-09T12:00:00Z",
    }]})["uploads"]

    results = _complete(service, user, [ids[0], ids[1], incomplete["upload_id"], "unknown"])

    assert results[0]["state"] == "ready"
    assert results[1]["error_code"] == 422
    assert results[2]["error_code"] == 409
    assert results[3]["error_code"] == 404
    assert [path.read_bytes() for path in _cataloged(user)] == [good]
    states = dict(sqlite3.connect(user.db_path).execute("SELECT id, state FROM sync_uploads"))
    assert states[ids[1]] == "failed"
    assert states[incomplete["upload_id"]] == "uploading"


def test_two_uploads_of_one_photo_in_a_batch_never_share_a_file(tmp_path: Path):
    # The second upload picks its name before the first one's file has moved
    # there: the name is already claimed, so it must take its own.
    user, service = _user(tmp_path), SyncUploadService()
    photo = _photo(5)
    first = _upload(service, user, [photo], "first")
    second = _upload(service, user, [photo], "second")

    results = _complete(service, user, first + second)

    assert sorted(entry["state"] for entry in results) == ["duplicate", "ready"]
    [stored] = _cataloged(user)
    assert stored.read_bytes() == photo
    copies = [path for path in user.media_root.rglob("*") if path.is_file()]
    assert copies == [stored], "the duplicate's copy is removed, the original kept"


def test_a_photo_the_library_already_has_is_settled_in_the_batch(tmp_path: Path):
    user, service = _user(tmp_path), SyncUploadService()
    photo = _photo(7)
    # Both copies are sent before the first is cataloged, so the second is
    # only found to be a duplicate when it completes.
    first = _upload(service, user, [photo], "first")
    again = _upload(service, user, [photo, _photo(8)], "again")
    assert _complete(service, user, first)[0]["state"] == "ready"

    results = _complete(service, user, again)

    assert [entry["state"] for entry in results] == ["duplicate", "ready"]
    assert len(_cataloged(user)) == 2
    leftovers = [path for path in (user.db_path.parent / "sync_uploads").rglob("*") if path.is_file()]
    assert leftovers == []


def test_a_repeated_batch_answers_from_the_recorded_state(tmp_path: Path):
    user, service = _user(tmp_path), SyncUploadService()
    ids = _upload(service, user, [_photo(index) for index in range(3)])
    first = _complete(service, user, ids)

    again = _complete(service, user, ids)

    assert [entry["state"] for entry in first] == ["ready"] * 3
    assert [entry["state"] for entry in again] == ["ready"] * 3
    assert len(_cataloged(user)) == 3


def test_a_batch_is_cataloged_under_one_lease(tmp_path: Path, monkeypatch):
    from core.upload_processing_store import UploadProcessingStore

    claims = []
    real_claim_many = UploadProcessingStore.claim_many

    def counting_claim_many(self, upload_ids, token):
        claims.append(list(upload_ids))
        return real_claim_many(self, upload_ids, token)

    monkeypatch.setattr(UploadProcessingStore, "claim_many", counting_claim_many)
    user, service = _user(tmp_path), SyncUploadService()
    ids = _upload(service, user, [_photo(index) for index in range(4)])

    assert [entry["state"] for entry in _complete(service, user, ids)] == ["ready"] * 4
    assert claims == [ids]


def test_when_the_batch_catalog_fails_each_upload_is_tried_on_its_own(tmp_path: Path, monkeypatch):
    import core.media_ingest as ingest_module

    user, service = _user(tmp_path), SyncUploadService()
    ids = _upload(service, user, [_photo(index) for index in range(3)])
    real_ingest_one = ingest_module._ingest_one

    def failing_for_one(connection, catalog, media_root, upload_id, *args):
        if upload_id == ids[1]:
            raise OSError("unreadable file")
        return real_ingest_one(connection, catalog, media_root, upload_id, *args)

    monkeypatch.setattr(ingest_module, "_ingest_one", failing_for_one)

    results = _complete(service, user, ids)

    assert results[0]["state"] == "ready" and results[2]["state"] == "ready"
    # As when it completes alone: still pending, its retry scheduled.
    assert results[1]["state"] == "pending_processing", results[1]
    with sqlite3.connect(user.db_path) as conn:
        state, attempts, retry_at = conn.execute(
            "SELECT state, processing_attempts, processing_next_attempt_at "
            "FROM sync_uploads WHERE id = ?", (ids[1],),
        ).fetchone()
    # Its own attempt failed and is scheduled again; the failed batch
    # attempt did not count against it.
    assert (state, attempts) == ("pending_processing", 1) and retry_at
    assert len(_cataloged(user)) == 2
