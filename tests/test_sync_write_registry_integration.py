from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from core.sqlite_write_coordinator import CoordinatorQueueFull
from core.sqlite_write_registry import SQLiteWriteCoordinatorRegistry
from core.sync_upload_service import SyncUploadError, SyncUploadService
from core.users_db import IrisUser


class RecordingRegistry:
    def __init__(self) -> None:
        self.inner = SQLiteWriteCoordinatorRegistry(
            coordinator_options={"batch_window_s": 0},
        )
        self.submissions: list[Path] = []

    def submit(self, db_path: Path, callback):
        self.submissions.append(db_path.resolve())
        return self.inner.submit(db_path, callback)

    def shutdown(self, *, timeout: float = 5.0) -> bool:
        return self.inner.shutdown(timeout=timeout)


class BusyRegistry:
    def submit(self, _db_path: Path, _callback):
        raise CoordinatorQueueFull("bounded test queue is full")


def _user(root: Path, username: str, user_id: int) -> IrisUser:
    data = root / username / "data"
    return IrisUser(
        id=user_id,
        username=username,
        password_hash="unused",
        display_name=username,
        is_admin=False,
        db_path=data / "iris.db",
        media_root=root / username / "media",
        model_name="",
        session_version=1,
    )


def _item(client_id: str, payload: bytes) -> dict[str, object]:
    return {
        "client_upload_id": client_id,
        "filename": f"{client_id}.jpg",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "captured_at": "2026-10-01T12:00:00Z",
    }


def test_reservation_batches_use_one_correct_account_writer_and_return_after_commit(
    tmp_path: Path,
) -> None:
    registry = RecordingRegistry()
    service = SyncUploadService(write_registry=registry)  # type: ignore[arg-type]
    alice = _user(tmp_path, "alice", 1)
    bob = _user(tmp_path, "bob", 2)
    photo_a, photo_b, photo_c = b"alice-1", b"alice-2", b"bob-1"
    try:
        alice_result = service.reserve_upload_batch(
            alice,
            "alice-phone",
            1 << 20,
            {"uploads": [_item("camera-a", photo_a), _item("camera-b", photo_b)]},
        )
        bob_result = service.reserve_upload_batch(
            bob,
            "bob-phone",
            1 << 20,
            {"uploads": [_item("camera-a", photo_c)]},
        )

        assert len(registry.submissions) == 2
        assert registry.submissions == [alice.db_path.resolve(), bob.db_path.resolve()]
        assert len(alice_result["uploads"]) == 2
        assert len(bob_result["uploads"]) == 1

        with sqlite3.connect(alice.db_path) as connection:
            alice_rows = connection.execute(
                "SELECT device_id, client_upload_id FROM sync_uploads ORDER BY client_upload_id"
            ).fetchall()
        with sqlite3.connect(bob.db_path) as connection:
            bob_rows = connection.execute(
                "SELECT device_id, client_upload_id FROM sync_uploads"
            ).fetchall()

        assert alice_rows == [("alice-phone", "camera-a"), ("alice-phone", "camera-b")]
        assert bob_rows == [("bob-phone", "camera-a")]
    finally:
        assert registry.shutdown()


def test_bounded_writer_backpressure_is_retryable_not_an_unhandled_server_error(
    tmp_path: Path,
) -> None:
    service = SyncUploadService(write_registry=BusyRegistry())  # type: ignore[arg-type]
    user = _user(tmp_path, "alice", 1)
    with pytest.raises(SyncUploadError) as raised:
        service.reserve_upload_batch(
            user,
            "alice-phone",
            1 << 20,
            {"uploads": [_item("camera-a", b"one photo")]},
        )

    assert raised.value.status_code == 503
    with sqlite3.connect(user.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sync_uploads").fetchone()[0] == 0
