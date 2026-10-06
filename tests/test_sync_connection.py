"""Sync requests reuse a prepared library schema instead of rebuilding it each time."""
from __future__ import annotations

from pathlib import Path

import core.sync_upload_service as service_module
from core.sync_upload_service import SyncUploadService
from core.users_db import IrisUser


def _user(root: Path) -> IrisUser:
    return IrisUser(
        id=1, username="alice", password_hash="unused", display_name="", is_admin=False,
        db_path=root / "iris.db", media_root=root / "media", model_name="", session_version=1,
    )


def _count_preparations(monkeypatch) -> list[int]:
    calls: list[int] = []
    original = service_module.init_db
    monkeypatch.setattr(service_module, "init_db", lambda path: calls.append(1) or original(path))
    return calls


def test_the_schema_is_prepared_once_per_database(tmp_path: Path, monkeypatch):
    # Every reserve, chunk and completion opens the library; preparing the
    # schema each time cost a schema walk and a commit per request.
    calls = _count_preparations(monkeypatch)
    user = _user(tmp_path)

    for _ in range(3):
        SyncUploadService.open_connection(user).close()

    assert len(calls) == 1


def test_a_database_recreated_at_the_same_path_is_prepared_again(tmp_path: Path, monkeypatch):
    calls = _count_preparations(monkeypatch)
    user = _user(tmp_path)
    SyncUploadService.open_connection(user).close()

    user.db_path.unlink()
    connection = SyncUploadService.open_connection(user)

    assert len(calls) == 2
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_uploads'"
    ).fetchone()
    connection.close()
