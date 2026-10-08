from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.sqlite_write_registry import (
    SQLiteWriteCoordinatorRegistry,
    SQLiteWriteRegistryFull,
)


def _create_table(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE writes (value INTEGER NOT NULL)")


def test_normalized_aliases_share_one_worker_and_route_to_the_correct_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "accounts" / "alice.db"
    _create_table(database)
    alias = tmp_path / "accounts" / "nested" / ".." / "alice.db"
    registry = SQLiteWriteCoordinatorRegistry(
        max_coordinators=2,
        coordinator_options={"batch_window_s": 0},
    )
    caller_thread = threading.get_ident()

    def insert(value: int):
        def operation(connection: sqlite3.Connection) -> tuple[int, int]:
            connection.execute("INSERT INTO writes(value) VALUES (?)", (value,))
            return threading.get_ident(), value

        return operation

    with ThreadPoolExecutor(max_workers=8) as callers:
        futures = [
            callers.submit(registry.submit, database, insert(value))
            for value in range(20)
        ]
        nested = callers.submit(registry.submit, alias, insert(20)).result(timeout=2)
    worker_results = [future.result(timeout=2).result(timeout=2) for future in futures]
    assert nested.result(timeout=2) == (worker_results[0][0], 20)
    assert all(thread_id != caller_thread for thread_id, _ in worker_results)
    assert len({thread_id for thread_id, _ in worker_results}) == 1
    assert registry.shutdown(timeout=2)

    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT value FROM writes ORDER BY value").fetchall()
    assert rows == [(value,) for value in range(21)]


def test_worker_limit_rejects_other_database_without_redirecting_work(
    tmp_path: Path,
) -> None:
    first_db = tmp_path / "first.db"
    second_db = tmp_path / "second.db"
    _create_table(first_db)
    _create_table(second_db)
    registry = SQLiteWriteCoordinatorRegistry(
        max_coordinators=1,
        coordinator_options={"batch_window_s": 0},
    )

    started = threading.Event()
    release = threading.Event()

    def first_operation(connection: sqlite3.Connection) -> None:
        started.set()
        assert release.wait(timeout=2)
        connection.execute("INSERT INTO writes(value) VALUES (1)")

    first = registry.submit(first_db, first_operation)
    assert started.wait(timeout=2)
    with pytest.raises(SQLiteWriteRegistryFull, match="all .* busy"):
        registry.submit(
            second_db,
            lambda connection: connection.execute(
                "INSERT INTO writes(value) VALUES (2)"
            ),
        )
    release.set()
    first.result(timeout=2)
    assert registry.shutdown(timeout=2)

    with sqlite3.connect(first_db) as connection:
        assert connection.execute("SELECT value FROM writes").fetchall() == [(1,)]
    with sqlite3.connect(second_db) as connection:
        assert connection.execute("SELECT value FROM writes").fetchall() == []


def test_idle_worker_is_evicted_for_a_new_account_database(tmp_path: Path) -> None:
    first_db = tmp_path / "first.db"
    second_db = tmp_path / "second.db"
    _create_table(first_db)
    _create_table(second_db)
    registry = SQLiteWriteCoordinatorRegistry(
        max_coordinators=1,
        coordinator_options={"batch_window_s": 0},
    )

    first = registry.submit(
        first_db,
        lambda connection: connection.execute(
            "INSERT INTO writes(value) VALUES (1)"
        ),
    )
    first.result(timeout=2)
    second = registry.submit(
        second_db,
        lambda connection: connection.execute(
            "INSERT INTO writes(value) VALUES (2)"
        ),
    )
    second.result(timeout=2)
    assert registry.shutdown(timeout=2)

    with sqlite3.connect(first_db) as connection:
        assert connection.execute("SELECT value FROM writes").fetchall() == [(1,)]
    with sqlite3.connect(second_db) as connection:
        assert connection.execute("SELECT value FROM writes").fetchall() == [(2,)]


def test_shutdown_closes_admission_and_drains_accepted_work(tmp_path: Path) -> None:
    database = tmp_path / "account.db"
    _create_table(database)
    registry = SQLiteWriteCoordinatorRegistry(
        coordinator_options={"batch_window_s": 0},
    )
    started = threading.Event()
    release = threading.Event()

    def blocked(connection: sqlite3.Connection) -> str:
        started.set()
        assert release.wait(timeout=2)
        connection.execute("INSERT INTO writes(value) VALUES (5)")
        return "drained"

    future = registry.submit(database, blocked)
    assert started.wait(timeout=2)
    assert registry.shutdown(timeout=0.01) is False
    with pytest.raises(RuntimeError, match="registry is shutting down"):
        registry.submit(database, lambda connection: None)

    release.set()
    assert future.result(timeout=2) == "drained"
    assert registry.shutdown(timeout=2)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM writes").fetchall() == [(5,)]


@pytest.mark.parametrize("limit", [0, -1, 4097, True, 1.5])
def test_registry_rejects_invalid_worker_limits(limit: object) -> None:
    with pytest.raises(ValueError, match="max_coordinators"):
        SQLiteWriteCoordinatorRegistry(max_coordinators=limit)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True])
def test_registry_rejects_invalid_shutdown_timeout(
    tmp_path: Path,
    timeout: object,
) -> None:
    registry = SQLiteWriteCoordinatorRegistry()
    with pytest.raises(ValueError, match="timeout"):
        registry.shutdown(timeout=timeout)  # type: ignore[arg-type]
    assert registry.shutdown(timeout=2)
