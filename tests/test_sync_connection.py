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


def test_parallel_first_opens_prepare_the_schema_once(tmp_path: Path, monkeypatch):
    # Requests run in worker threads; two first opens of a new library at once
    # ran two schema migrations and one failed with "duplicate column name".
    import threading

    calls = _count_preparations(monkeypatch)
    user = _user(tmp_path)
    start = threading.Barrier(8)
    errors: list[BaseException] = []

    def open_once() -> None:
        start.wait()
        try:
            SyncUploadService.open_connection(user).close()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=open_once) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(calls) == 1


def _count_connects(monkeypatch) -> list[int]:
    calls: list[int] = []
    original = service_module.connect_deferred
    monkeypatch.setattr(
        service_module, "connect_deferred",
        lambda *args, **kwargs: calls.append(1) or original(*args, **kwargs),
    )
    return calls


def test_a_thread_reuses_its_connection(tmp_path: Path, monkeypatch):
    # A new connection's first statement parses the whole schema; every chunk
    # request opened two, which cost about a fifth of the server's CPU.
    user = _user(tmp_path)
    SyncUploadService.open_connection(user).close()
    connects = _count_connects(monkeypatch)

    for _ in range(5):
        with SyncUploadService.open_connection(user) as connection:
            connection.execute("SELECT COUNT(*) FROM sync_uploads").fetchone()

    assert connects == []


def test_a_reused_connection_sees_what_others_committed(tmp_path: Path):
    import sqlite3

    user = _user(tmp_path)
    with SyncUploadService.open_connection(user) as connection:
        assert connection.execute("SELECT COUNT(*) FROM device_sources").fetchone()[0] == 0

    with sqlite3.connect(user.db_path) as other:
        other.execute(
            "INSERT INTO device_sources (device_id, source_id, updated_at) VALUES ('d', 's', 'now')"
        )

    with SyncUploadService.open_connection(user) as connection:
        assert connection.execute("SELECT COUNT(*) FROM device_sources").fetchone()[0] == 1


def test_a_transaction_left_open_is_rolled_back_when_given_back(tmp_path: Path):
    from contextlib import closing

    user = _user(tmp_path)
    with closing(SyncUploadService.open_connection(user)) as connection:
        connection.execute(
            "INSERT INTO device_sources (device_id, source_id, updated_at) VALUES ('d', 's', 'now')"
        )  # never committed: the request failed midway

    with SyncUploadService.open_connection(user) as connection:
        assert not connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM device_sources").fetchone()[0] == 0


def test_a_nested_use_gets_a_connection_of_its_own(tmp_path: Path):
    from contextlib import closing

    user = _user(tmp_path)
    with closing(SyncUploadService.open_connection(user)) as outer:
        outer.execute("BEGIN IMMEDIATE")
        with closing(SyncUploadService.open_connection(user)) as inner:
            # The outer write transaction is not shared with the inner use.
            assert not inner.in_transaction
        outer.rollback()


def test_threads_do_not_share_a_connection(tmp_path: Path):
    import threading

    user = _user(tmp_path)
    seen: list[int] = []
    together = threading.Barrier(3)

    def use() -> None:
        with SyncUploadService.open_connection(user) as connection:
            seen.append(id(connection._connection))
            together.wait(timeout=5)  # all three hold theirs at once

    threads = [threading.Thread(target=use) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(seen)) == 3


def test_a_database_replaced_at_the_same_path_gets_a_new_connection(tmp_path: Path):
    user = _user(tmp_path)
    with SyncUploadService.open_connection(user) as connection:
        connection.execute(
            "INSERT INTO device_sources (device_id, source_id, updated_at) VALUES ('d', 's', 'now')"
        )
    # Kept open across the replacement, so the new file gets a different inode.
    keep = user.db_path.with_name("old.db")
    user.db_path.rename(keep)
    for suffix in ("-wal", "-shm"):
        user.db_path.with_name(user.db_path.name + suffix).unlink(missing_ok=True)

    with SyncUploadService.open_connection(user) as connection:
        assert connection.execute("SELECT COUNT(*) FROM device_sources").fetchone()[0] == 0
