from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from core.sqlite_write_coordinator import CoordinatorQueueFull, SQLiteWriteCoordinator


def _create_table(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE values_written (value INTEGER NOT NULL)")


def _insert(value: int):
    def operation(connection: sqlite3.Connection) -> int:
        connection.execute("INSERT INTO values_written(value) VALUES (?)", (value,))
        return value

    return operation


def test_commits_full_before_resolving_future_and_keeps_connection_on_worker(tmp_path: Path) -> None:
    database = tmp_path / "account.db"
    _create_table(database)
    caller_thread = threading.get_ident()
    coordinator = SQLiteWriteCoordinator(database, batch_window_s=0)

    def operation(connection: sqlite3.Connection) -> tuple[int, int, bool]:
        connection.execute("INSERT INTO values_written(value) VALUES (7)")
        synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        return threading.get_ident(), synchronous, connection.in_transaction

    future = coordinator.submit(operation)
    worker_thread, synchronous, in_transaction = future.result(timeout=2)
    assert coordinator.shutdown(timeout=2)

    assert worker_thread != caller_thread
    assert synchronous == 2  # SQLite FULL
    assert in_transaction is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM values_written").fetchall() == [(7,)]


def test_batches_are_bounded_and_individual_savepoint_failure_does_not_drop_siblings(
    tmp_path: Path,
) -> None:
    database = tmp_path / "account.db"
    _create_table(database)
    coordinator = SQLiteWriteCoordinator(
        database,
        max_pending=8,
        max_batch_items=2,
        batch_window_s=0.05,
    )
    trace: list[str] = []

    def trace_batch(connection: sqlite3.Connection) -> None:
        connection.set_trace_callback(trace.append)

    def fail_after_write(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT INTO values_written(value) VALUES (99)")
        raise ValueError("one item is invalid")

    futures = [
        coordinator.submit(trace_batch),
        coordinator.submit(_insert(1)),
        coordinator.submit(fail_after_write),
        coordinator.submit(_insert(2)),
        coordinator.submit(_insert(3)),
    ]
    assert futures[0].result(timeout=2) is None
    assert futures[1].result(timeout=2) == 1
    with pytest.raises(ValueError, match="one item is invalid"):
        futures[2].result(timeout=2)
    assert futures[3].result(timeout=2) == 2
    assert futures[4].result(timeout=2) == 3
    assert coordinator.shutdown(timeout=2)

    assert sum(statement == "COMMIT" for statement in trace) == 3
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT value FROM values_written ORDER BY value"
        ).fetchall()
    assert rows == [(1,), (2,), (3,)]


def test_queue_is_bounded_and_shutdown_timeout_does_not_cancel_accepted_work(
    tmp_path: Path,
) -> None:
    database = tmp_path / "account.db"
    _create_table(database)
    coordinator = SQLiteWriteCoordinator(
        database,
        max_pending=1,
        max_batch_items=1,
        batch_window_s=0,
    )
    entered = threading.Event()
    release = threading.Event()

    def blocked(connection: sqlite3.Connection) -> str:
        entered.set()
        assert release.wait(timeout=2)
        connection.execute("INSERT INTO values_written(value) VALUES (5)")
        return "done"

    active = coordinator.submit(blocked)
    assert entered.wait(timeout=2)
    queued = coordinator.submit(_insert(6))
    with pytest.raises(CoordinatorQueueFull):
        coordinator.submit(_insert(7))

    assert coordinator.shutdown(timeout=0.01) is False
    with pytest.raises(RuntimeError, match="shutting down"):
        coordinator.submit(_insert(8))
    release.set()

    assert active.result(timeout=2) == "done"
    assert queued.result(timeout=2) == 6
    assert coordinator.shutdown(timeout=2) is True
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM values_written ORDER BY value"
        ).fetchall() == [(5,), (6,)]


def test_cancelled_queued_write_is_skipped_and_writer_continues(tmp_path: Path) -> None:
    database = tmp_path / "account.db"
    _create_table(database)
    coordinator = SQLiteWriteCoordinator(database, batch_window_s=0)
    entered = threading.Event()
    release = threading.Event()
    callbacks: list[int] = []

    def blocked(connection: sqlite3.Connection) -> None:
        entered.set()
        assert release.wait(timeout=2)
        connection.execute("INSERT INTO values_written(value) VALUES (1)")

    active = coordinator.submit(blocked)
    assert entered.wait(timeout=2)
    cancelled = coordinator.submit(lambda _connection: callbacks.append(2))
    assert cancelled.cancel()
    final = coordinator.submit(_insert(3))
    release.set()

    assert active.result(timeout=2) is None
    assert final.result(timeout=2) == 3
    assert coordinator.shutdown(timeout=2)
    assert cancelled.cancelled()
    assert callbacks == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM values_written ORDER BY value"
        ).fetchall() == [(1,), (3,)]


def test_callback_failure_is_independent_when_every_operation_in_batch_fails(
    tmp_path: Path,
) -> None:
    database = tmp_path / "account.db"
    _create_table(database)
    coordinator = SQLiteWriteCoordinator(database, batch_window_s=0.05)

    def failing(message: str):
        def operation(connection: sqlite3.Connection) -> None:
            connection.execute("INSERT INTO values_written(value) VALUES (42)")
            raise LookupError(message)

        return operation

    first = coordinator.submit(failing("first"))
    second = coordinator.submit(failing("second"))
    with pytest.raises(LookupError, match="first"):
        first.result(timeout=2)
    with pytest.raises(LookupError, match="second"):
        second.result(timeout=2)
    assert coordinator.shutdown(timeout=2)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM values_written").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_pending": 0}, "max_pending"),
        ({"max_pending": 4097}, "max_pending"),
        ({"max_batch_items": 0}, "max_batch_items"),
        ({"max_batch_items": 257}, "max_batch_items"),
        ({"batch_window_s": -0.1}, "batch_window_s"),
        ({"batch_window_s": float("nan")}, "batch_window_s"),
        ({"batch_window_s": 2}, "batch_window_s"),
        ({"sqlite_timeout_s": 0}, "sqlite_timeout_s"),
        ({"sqlite_timeout_s": float("inf")}, "sqlite_timeout_s"),
    ],
)
def test_rejects_invalid_limits(tmp_path: Path, kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SQLiteWriteCoordinator(tmp_path / "account.db", **kwargs)


def test_shutdown_timeout_must_be_nonnegative(tmp_path: Path) -> None:
    coordinator = SQLiteWriteCoordinator(tmp_path / "account.db")
    with pytest.raises(ValueError, match="timeout"):
        coordinator.shutdown(timeout=-1)
    assert coordinator.shutdown(timeout=2)
