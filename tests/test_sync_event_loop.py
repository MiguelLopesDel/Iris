"""Sync requests keep blocking work off the event loop.

The server ran SQLite reads and commits, and a directory fsync, directly in
async handlers: while one waited, no other request moved, and the process sat
on one core with the disk mostly idle. Here a step that used to run on the
loop is made slow on purpose, and the loop's lag is measured meanwhile.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path

import core.sync_upload_service as service_module
from core.perf_probe import LoopLagProbe
from core.sync_upload_service import SyncUploadService
from core.users_db import IrisUser

DEVICE = "phone-1"
SLOW = 0.3


def _user(root: Path) -> IrisUser:
    return IrisUser(
        id=1, username="alice", password_hash="unused", display_name="", is_admin=False,
        db_path=root / "data" / "iris.db", media_root=root / "media", model_name="", session_version=1,
    )


def _reserve(service: SyncUploadService, user: IrisUser, data: bytes, client_id: str) -> str:
    return service.reserve_upload_batch(user, DEVICE, 1 << 40, {"uploads": [{
        "client_upload_id": client_id, "filename": f"{client_id}.jpg", "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "captured_at": "2026-09-09T12:00:00Z",
    }]})["uploads"][0]["upload_id"]


async def _while_measuring_lag(work) -> float:
    probe = LoopLagProbe(interval=0.01)
    task = asyncio.create_task(probe.run())
    await asyncio.sleep(0.05)
    try:
        await work
    finally:
        task.cancel()
    return probe.snapshot()["loop_lag_ms_max"]


def test_receiving_a_chunk_does_not_block_the_event_loop(tmp_path: Path, monkeypatch):
    original = service_module.fsync_directory

    def slow_fsync(path):
        time.sleep(SLOW)  # a directory sync on a busy disk
        original(path)

    monkeypatch.setattr(service_module, "fsync_directory", slow_fsync)
    user, service, data = _user(tmp_path), SyncUploadService(), b"\xff\xd8" + b"x" * 4096
    upload_id = _reserve(service, user, data, "chunk")

    async def put():
        async def stream():
            yield data

        await service.receive_chunk(user, DEVICE, upload_id, 0, len(data), stream(), lambda *a, **k: None)

    lag_ms = asyncio.run(_while_measuring_lag(put()))
    assert lag_ms < SLOW * 1000 / 3, f"the event loop was blocked for {lag_ms} ms"


def test_completing_uploads_does_not_block_the_event_loop(tmp_path: Path, monkeypatch):
    original = service_module.record_upload_finalized

    def slow_record(*args, **kwargs):
        time.sleep(SLOW)  # a commit waiting for the disk
        return original(*args, **kwargs)

    monkeypatch.setattr(service_module, "record_upload_finalized", slow_record)
    user, service = _user(tmp_path), SyncUploadService()
    ids = []
    for index in range(2):
        data = b"\xff\xd8" + bytes([index]) * 4096
        upload_id = _reserve(service, user, data, f"done-{index}")

        async def stream(payload=data):
            yield payload

        asyncio.run(service.receive_chunk(user, DEVICE, upload_id, 0, len(data), stream(), lambda *a, **k: None))
        ids.append(upload_id)

    lag_ms = asyncio.run(_while_measuring_lag(service.complete_upload_batch(
        user, DEVICE, {"uploads": [{"upload_id": value} for value in ids]},
        sync_ai_processing=False, load_model=False, on_finished=lambda: None, log_phase=lambda *a, **k: None,
    )))
    assert lag_ms < SLOW * 1000 / 3, f"the event loop was blocked for {lag_ms} ms"
