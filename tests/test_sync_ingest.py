"""A batch of small reserved uploads is received and finished in one request.

Each photo keeps its own outcome; the batch shares one admission, one file
durability barrier and one commit, and nothing is acknowledged before both
its file and its database state are on disk.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import core.sync_ingest as ingest_module
from core.file_durability import FileDurabilityService
from core.ingest_policy import IngestPolicy
from core.sync_ingest import parse_ingest_manifest
from core.sync_recovery import recover_pending_uploads
from core.sync_upload_service import SyncUploadError, SyncUploadService
from core.users_db import IrisUser

DEVICE = "phone-1"
POLICY = IngestPolicy(durability_window_s=0)


def _user(root: Path) -> IrisUser:
    return IrisUser(
        id=1, username="alice", password_hash="unused", display_name="", is_admin=False,
        db_path=root / "data" / "iris.db", media_root=root / "media", model_name="", session_version=1,
    )


def _service() -> SyncUploadService:
    return SyncUploadService(ingest_policy=POLICY)


def _photo(index: int, size: int = 3000) -> bytes:
    return b"\xff\xd8" + bytes([index % 256]) * (size - 2)


def _reserve(service, user, photos: list[bytes], prefix: str = "item") -> list[str]:
    result = service.reserve_upload_batch(user, DEVICE, 1 << 40, {"uploads": [{
        "client_upload_id": f"{prefix}-{index}", "filename": f"IMG_{index:04d}.jpg",
        "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "captured_at": "2026-09-09T12:00:00Z",
    } for index, data in enumerate(photos)]})
    return [entry["upload_id"] for entry in result["uploads"]]


async def _body(parts: list[bytes], piece: int):
    data = b"".join(parts)
    for start in range(0, len(data), piece):
        yield data[start:start + piece]


def _ingest(service, user, entries: list[tuple[str, int]], parts: list[bytes], piece: int = 1000):
    manifest = ",".join(f"{upload_id}:{size}" for upload_id, size in entries)
    return asyncio.run(service.ingest_pipeline.ingest(
        user, DEVICE, manifest, sum(size for _, size in entries), _body(parts, piece),
        sync_ai_processing=False, load_model=False, on_finished=lambda: None,
        log_phase=lambda *a, **k: None,
    ))["uploads"]


def _states(user) -> dict[str, str]:
    with sqlite3.connect(user.db_path) as conn:
        return dict(conn.execute("SELECT id, state FROM sync_uploads"))


def _cataloged(user) -> list[Path]:
    with sqlite3.connect(user.db_path) as conn:
        return [Path(row[0]) for row in conn.execute("SELECT caminho FROM memes ORDER BY id")]


def test_a_batch_is_stored_and_cataloged_in_one_request(tmp_path: Path):
    user, service = _user(tmp_path), _service()
    photos = [_photo(index, 2000 + 700 * index) for index in range(5)]
    ids = _reserve(service, user, photos)

    # Pieces that straddle the photos' boundaries.
    results = _ingest(service, user, list(zip(ids, map(len, photos), strict=True)), photos, piece=777)

    assert [entry["state"] for entry in results] == ["ready"] * 5, results
    assert sorted(path.read_bytes() for path in _cataloged(user)) == sorted(photos)
    # Written straight to the library: no temporary upload files at all.
    staging = user.db_path.parent / "sync_uploads"
    assert not staging.exists() or not any(staging.iterdir())


def test_one_file_barrier_and_one_admission_per_batch(tmp_path: Path, monkeypatch):
    flushes, writes = [], []
    real_flush = FileDurabilityService.flush

    def counting_flush(self, files, directories=()):
        files = list(files)
        flushes.append(len(files))
        return real_flush(self, files, directories)

    monkeypatch.setattr(FileDurabilityService, "flush", counting_flush)
    user, service = _user(tmp_path), _service()
    photos = [_photo(index) for index in range(6)]
    ids = _reserve(service, user, photos)
    real_submit = service._submit_write
    monkeypatch.setattr(
        service, "_submit_write", lambda u, callback: writes.append(callback) or real_submit(u, callback)
    )

    _ingest(service, user, [(upload_id, len(data)) for upload_id, data in zip(ids, photos, strict=True)], photos)

    assert flushes == [6]
    # Admission and commit; cataloging adds its own few batch writes.
    names = [getattr(callback, "__name__", "") for callback in writes]
    assert names[:2] == ["admit", "commit"]


def test_each_photo_keeps_its_own_outcome(tmp_path: Path):
    user, service = _user(tmp_path), _service()
    good, corrupt, other = _photo(1), _photo(2), _photo(3)
    ids = _reserve(service, user, [good, corrupt, other])
    damaged = corrupt[:-1] + b"\x00"  # not what was declared at reservation

    results = _ingest(service, user, [
        (ids[0], len(good)), (ids[1], len(damaged)), ("unknown", 40), (ids[2], len(other) + 1),
    ], [good, damaged, b"u" * 40, other + b"!"])

    assert results[0]["state"] == "ready"
    assert results[1]["error_code"] == 422
    assert results[2]["error_code"] == 404
    assert results[3]["error_code"] == 409  # a different size than reserved
    assert [path.read_bytes() for path in _cataloged(user)] == [good]
    states = _states(user)
    assert states[ids[1]] == "failed" and states[ids[2]] == "uploading"
    stored = [path for path in user.media_root.rglob("*") if path.is_file()]
    assert len(stored) == 1, "the corrupt photo's file is removed"


def test_a_photo_the_library_already_has_is_settled_without_its_bytes(tmp_path: Path):
    user, service = _user(tmp_path), _service()
    photo = _photo(4)
    first = _reserve(service, user, [photo], "first")
    again = _reserve(service, user, [photo, _photo(5)], "again")  # before the first is stored
    _ingest(service, user, [(first[0], len(photo))], [photo])

    results = _ingest(service, user, [(again[0], len(photo)), (again[1], 3000)], [photo, _photo(5)])

    assert [entry["state"] for entry in results] == ["duplicate", "ready"]
    assert len(_cataloged(user)) == 2
    assert len([path for path in user.media_root.rglob("*") if path.is_file()]) == 2


def test_two_copies_of_one_photo_in_a_batch_never_share_a_file(tmp_path: Path):
    user, service = _user(tmp_path), _service()
    photo = _photo(6)
    ids = _reserve(service, user, [photo], "a") + _reserve(service, user, [photo], "b")

    results = _ingest(service, user, [(ids[0], len(photo)), (ids[1], len(photo))], [photo, photo])

    assert sorted(entry["state"] for entry in results) == ["duplicate", "ready"]
    [stored] = _cataloged(user)
    assert [path for path in user.media_root.rglob("*") if path.is_file()] == [stored]
    assert stored.read_bytes() == photo


def test_a_batch_cut_short_acknowledges_nothing_and_can_be_sent_again(tmp_path: Path):
    user, service = _user(tmp_path), _service()
    photos = [_photo(index) for index in range(3)]
    ids = _reserve(service, user, photos)
    entries = [(upload_id, len(data)) for upload_id, data in zip(ids, photos, strict=True)]

    with pytest.raises(SyncUploadError) as cut:
        manifest = ",".join(f"{upload_id}:{size}" for upload_id, size in entries)
        asyncio.run(service.ingest_pipeline.ingest(
            user, DEVICE, manifest, sum(size for _, size in entries), _body(photos[:2], 1000),
            sync_ai_processing=False, load_model=False, on_finished=lambda: None,
            log_phase=lambda *a, **k: None,
        ))
    assert cut.value.status_code == 400
    assert set(_states(user).values()) == {"uploading"}
    assert not [path for path in user.media_root.rglob("*") if path.is_file()]

    assert [entry["state"] for entry in _ingest(service, user, entries, photos)] == ["ready"] * 3


def test_a_retried_batch_answers_from_the_recorded_state(tmp_path: Path):
    user, service = _user(tmp_path), _service()
    photos = [_photo(index) for index in range(2)]
    ids = _reserve(service, user, photos)
    entries = [(upload_id, len(data)) for upload_id, data in zip(ids, photos, strict=True)]
    _ingest(service, user, entries, photos)

    again = _ingest(service, user, entries, photos)  # the answer was lost on the way

    assert [entry["state"] for entry in again] == ["ready", "ready"]
    assert len(_cataloged(user)) == 2


def _left_receiving(tmp_path: Path, *, complete: bool) -> tuple[IrisUser, str, Path, bytes]:
    """An upload a batch left in ``receiving``, its file complete or cut."""
    user, service = _user(tmp_path), _service()
    photo = _photo(9, 5000)
    [upload_id] = _reserve(service, user, [photo])
    destination = user.media_root / "uploads" / "x" / f"{upload_id[:8]}-IMG_0000.jpg"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(photo if complete else photo[:1000])
    with sqlite3.connect(user.db_path) as conn:
        conn.execute(
            "UPDATE sync_uploads SET state = 'receiving', final_path = ? WHERE id = ?",
            (str(destination), upload_id),
        )
    return user, upload_id, destination, photo


def _recover(user: IrisUser, **options) -> None:
    class Inline:
        def submit(self, user, upload_id, file_path, *, use_ai, on_finished):
            from core.sync_processor import process_upload

            process_upload(
                db_path=user.db_path, media_root=user.media_root, model_name="",
                upload_id=upload_id, file_path=file_path, on_finished=on_finished, use_ai=use_ai,
            )

    import threading

    recover_pending_uploads(
        users_db_path=user.db_path.parent / "users.db", sync_ai_processing=False,
        load_model=False, on_finished=lambda *_: None, stop_event=threading.Event(),
        processing_workers=Inline(), users=[user], **options,
    )


def test_recovery_finishes_a_complete_file_left_receiving(tmp_path: Path):
    user, upload_id, destination, photo = _left_receiving(tmp_path, complete=True)

    _recover(user)

    assert _states(user)[upload_id] == "ready"
    assert _cataloged(user) == [destination] and destination.read_bytes() == photo


def test_recovery_resets_a_partial_file_left_receiving(tmp_path: Path):
    user, upload_id, destination, _photo_bytes = _left_receiving(tmp_path, complete=False)

    _recover(user)

    assert _states(user)[upload_id] == "uploading"
    assert not destination.exists()
    status = _service().upload_status(user, DEVICE, upload_id)
    assert (status["state"], status["offset"]) == ("uploading", 0)


@pytest.mark.parametrize(("manifest", "length", "status"), [
    ("", 0, 400),
    ("abc:10,abc:10", 20, 400),
    ("abc:10", 11, 400),
    ("abc:0", 0, 400),
    ("a/b:10", 10, 400),
    (",".join(f"id{n}:1" for n in range(POLICY.max_items + 1)), POLICY.max_items + 1, 400),
    (f"abc:{POLICY.max_bytes + 1}", POLICY.max_bytes + 1, 413),
])
def test_an_invalid_manifest_is_refused_before_the_body_is_read(manifest, length, status):
    with pytest.raises(SyncUploadError) as refused:
        parse_ingest_manifest(manifest, length, max_items=POLICY.max_items, max_bytes=POLICY.max_bytes)
    assert refused.value.status_code == status


def test_the_ingest_route_end_to_end(tmp_path: Path) -> None:
    script = r'''
import hashlib, io
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image
from core.auth import hash_password
from core.users_db import create_user

data = Path("data")
create_user(data / "users.db", data, username="alice", is_admin=True,
            password_hash=hash_password("synthetic password 1"))
import server

photos = []
for shade in (40, 120, 200):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (shade, 90, 150)).save(buffer, format="JPEG")
    photos.append(buffer.getvalue())

with TestClient(server.app) as phone:
    login = phone.post("/api/auth/devices/login", data={
        "username": "alice", "password": "synthetic password 1", "device_name": "phone", "platform": "android",
    })
    phone.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    reserved = phone.post("/api/sync/uploads/batch", json={"uploads": [
        {"client_upload_id": f"c{n}", "filename": f"IMG_{n:04d}.jpg", "size": len(body),
         "sha256": hashlib.sha256(body).hexdigest(), "captured_at": "2026-09-09T12:00:00Z"}
        for n, body in enumerate(photos)
    ]}).json()["uploads"]
    manifest = ",".join(f"{entry['upload_id']}:{len(body)}" for entry, body in zip(reserved, photos))
    sent = phone.post("/api/sync/ingest", content=b"".join(photos), headers={"X-Iris-Ingest": manifest})
    assert sent.status_code == 200, sent.text
    assert [entry["state"] for entry in sent.json()["uploads"]] == ["ready"] * 3, sent.text

    bad = phone.post("/api/sync/ingest", content=b"x", headers={"X-Iris-Ingest": "abc:2"})
    assert bad.status_code == 400, bad.text
    phone.headers.pop("Authorization")
    assert phone.post("/api/sync/ingest", content=b"x",
                      headers={"X-Iris-Ingest": "abc:1"}).status_code in {401, 403}
print("ok")
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
        IRIS_LOAD_MODEL="0",
        IRIS_INGEST_DURABILITY_WINDOW_S="0",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("ok")


def test_pieces_of_several_photos_share_a_thread_hop(tmp_path: Path, monkeypatch):
    hops = []
    real = ingest_module._write_segments
    monkeypatch.setattr(ingest_module, "_write_segments", lambda segments: hops.append(len(segments)) or real(segments))
    user, service = _user(tmp_path), _service()
    photos = [_photo(index) for index in range(8)]
    ids = _reserve(service, user, photos)

    _ingest(service, user, [(upload_id, len(data)) for upload_id, data in zip(ids, photos, strict=True)], photos)

    assert hops == [8]  # 8 small photos, well under one block: one hand-over


def test_a_periodic_recovery_pass_leaves_a_batch_in_flight_alone(tmp_path: Path, monkeypatch):
    # Recovery runs periodically. A pass that ran while a batch was between
    # writing its files and recording them finished those uploads itself,
    # and the batch then answered 409 for photos it had stored; with a file
    # still arriving, it would have removed it mid-write.
    from core.sync_db import now_iso

    user, service = _user(tmp_path), _service()
    photos = [_photo(index) for index in range(3)]
    ids = _reserve(service, user, photos)
    process_started = now_iso()
    real_verify = ingest_module._close_and_verify

    def verify_then_recover(items):
        real_verify(items)  # files complete on disk, state still "receiving"
        _recover(user, interrupted_before=process_started)

    monkeypatch.setattr(ingest_module, "_close_and_verify", verify_then_recover)

    results = _ingest(service, user, [(upload_id, len(data)) for upload_id, data in zip(ids, photos, strict=True)], photos)

    assert [entry["state"] for entry in results] == ["ready"] * 3, results
    assert sorted(path.read_bytes() for path in _cataloged(user)) == sorted(photos)


def test_the_server_tells_clients_how_large_a_batch_may_be(tmp_path: Path):
    # Batches are sized by the server's policy, not a fixed protocol number:
    # a client reserves and sends as many as the server accepts now.
    user = _user(tmp_path)
    service = SyncUploadService(ingest_policy=IngestPolicy(
        max_items=40, max_bytes=40 * 3000, block_bytes=64 * 1024, max_in_flight_bytes=1 << 20,
        durability_window_s=0,
    ))
    limits = service.ingest_limits()
    assert (limits["max_items"], limits["max_bytes"]) == (40, 120_000)

    photos = [_photo(index) for index in range(limits["suggested_items"])]
    ids = _reserve(service, user, photos)  # more than the legacy 16 at once
    results = _ingest(service, user, list(zip(ids, map(len, photos), strict=True)), photos)

    assert [entry["state"] for entry in results] == ["ready"] * 40
    with pytest.raises(SyncUploadError):
        _reserve(service, user, [_photo(index) for index in range(41)], "over")
