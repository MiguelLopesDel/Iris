"""Bounded, single-threaded SQLite writes for one database file.

Each coordinator owns one worker thread and creates its SQLite connection in
that thread. Callbacks are deliberately narrow: they receive the worker-owned
connection and perform one operation without managing its transaction. A batch
uses one outer transaction and a savepoint per callback, so one invalid
operation does not discard its siblings.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_logger = logging.getLogger("iris.sqlite_write_coordinator")


class SQLiteWriteQueueFull(RuntimeError):
    """Base class for explicit backpressure when bounded write capacity is full."""


class CoordinatorQueueFull(SQLiteWriteQueueFull):
    """Raised synchronously when the bounded pending-operation queue is full."""


@dataclass(frozen=True)
class _Operation[T]:
    callback: Callable[[sqlite3.Connection], T]
    future: Future[T]
    # False: the caller does not need this write on disk when its Future
    # resolves (it can rebuild it after a power loss); see relaxed_barrier_s.
    durable: bool = True


class SQLiteWriteCoordinator:
    """Serialize bounded SQLite write batches for one database.

    Construct one instance per database and inject it into the services that
    need coordinated writes. The pending queue is capped at ``max_pending``;
    the active batch is capped separately at ``max_batch_items``. The worker
    collects queued work for at most ``batch_window_s`` after the first item,
    then commits the batch with ``synchronous=FULL`` before resolving any
    successful Future.

    Submitted callbacks must use only the provided connection, must not close
    it, and must not issue transaction-control statements. Callback results
    and exceptions are delivered to their corresponding Futures. Queue
    saturation raises :class:`CoordinatorQueueFull` without blocking the
    caller. A future cancelled while queued is skipped; after its callback
    starts, cancellation cannot undo the database operation.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        max_pending: int = 256,
        max_batch_items: int = 256,
        batch_window_s: float = 0.01,
        sqlite_timeout_s: float = 30.0,
        relaxed_barrier_s: float = 1.0,
    ) -> None:
        if (
            isinstance(max_pending, bool)
            or not isinstance(max_pending, int)
            or not 1 <= max_pending <= 4096
        ):
            raise ValueError("max_pending must be between 1 and 4096")
        if (
            isinstance(max_batch_items, bool)
            or not isinstance(max_batch_items, int)
            or not 1 <= max_batch_items <= 256
        ):
            raise ValueError("max_batch_items must be between 1 and 256")
        if not isinstance(batch_window_s, (int, float)) or isinstance(batch_window_s, bool):
            raise ValueError("batch_window_s must be finite and non-negative")
        if not 0 <= batch_window_s <= 1 or not float(batch_window_s) < float("inf"):
            raise ValueError("batch_window_s must be finite and between 0 and 1")
        if not isinstance(sqlite_timeout_s, (int, float)) or isinstance(sqlite_timeout_s, bool):
            raise ValueError("sqlite_timeout_s must be finite and positive")
        if not 0 < sqlite_timeout_s <= 300 or not float(sqlite_timeout_s) < float("inf"):
            raise ValueError("sqlite_timeout_s must be finite and between 0 and 300")

        if (
            isinstance(relaxed_barrier_s, bool)
            or not isinstance(relaxed_barrier_s, (int, float))
            or not 0 < relaxed_barrier_s <= 60
        ):
            raise ValueError("relaxed_barrier_s must be between 0 and 60")
        self._relaxed_barrier_s = float(relaxed_barrier_s)
        # When the oldest commit not yet synced to disk was made, if any.
        self._relaxed_since: float | None = None
        self._synchronous = "FULL"

        self.db_path = Path(db_path)
        self._max_batch_items = max_batch_items
        self._batch_window_s = batch_window_s
        self._sqlite_timeout_s = sqlite_timeout_s
        self._queue: queue.Queue[_Operation[object]] = queue.Queue(maxsize=max_pending)
        self._state_lock = threading.Lock()
        self._stopping = False
        self._accepted_operations = 0
        self._thread = threading.Thread(
            target=self._run,
            name=f"iris-sqlite-writer-{self.db_path.name}",
            daemon=True,
        )
        self._thread.start()

    @property
    def is_alive(self) -> bool:
        """Whether the worker can still accept and execute its database queue."""
        return self._thread.is_alive()

    def submit[T](
        self, callback: Callable[[sqlite3.Connection], T], *, durable: bool = True,
    ) -> Future[T]:
        """Queue one operation and return its result Future.

        With ``durable`` (the default) the Future resolves after a FULL commit.
        Without it, after a commit that may not be on disk yet: a batch with
        only such operations commits with ``synchronous=NORMAL``, and a FULL
        barrier follows within ``relaxed_barrier_s`` (or with the next durable
        batch). Use it only for writes the caller can rebuild after a power
        loss.

        Raises RuntimeError after shutdown begins, or CoordinatorQueueFull
        immediately if the pending queue has reached its configured bound.
        """
        future: Future[T] = Future()
        operation: _Operation[T] = _Operation(callback=callback, future=future, durable=durable)
        with self._state_lock:
            if self._stopping:
                raise RuntimeError("SQLite write coordinator is shutting down")
            try:
                self._queue.put_nowait(cast(_Operation[object], operation))
            except queue.Full as exc:
                raise CoordinatorQueueFull("SQLite write queue is full") from exc
            self._accepted_operations += 1
        return future

    def retire_if_idle(self) -> bool:
        """Close admission and stop the worker only when no work is accepted.

        Callers must serialize registry lookup/submission with retirement. The
        accepted-work count covers queued and currently executing operations,
        so an idle worker cannot be retired between dequeue and execution.
        """
        with self._state_lock:
            if self._stopping or self._accepted_operations:
                return False
            self._stopping = True
        self._thread.join()
        return True

    def shutdown(self, *, timeout: float | None = 10.0) -> bool:
        """Stop accepting writes and drain accepted work.

        Returns ``False`` if the worker did not finish within ``timeout``.
        A timed-out call does not cancel accepted operations; a later call may
        wait again. ``None`` waits without a deadline.
        """
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 <= timeout < float("inf")
        ):
            raise ValueError("timeout must be finite and non-negative")
        with self._state_lock:
            self._stopping = True
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def __enter__(self) -> SQLiteWriteCoordinator:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.shutdown(timeout=None)

    def _run(self) -> None:
        # Deliberately created and used only on this thread. Keep SQLite's
        # default check_same_thread protection enabled.
        connection: sqlite3.Connection | None = None
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.db_path,
                timeout=self._sqlite_timeout_s,
                isolation_level=None,
            )
            connection.execute("PRAGMA synchronous=FULL")
            while True:
                first = self._next_operation(connection)
                if first is None:
                    self._barrier_if_relaxed(connection)
                    return
                batch = self._collect_batch(first)
                active_batch = [
                    operation
                    for operation in batch
                    if operation.future.set_running_or_notify_cancel()
                ]
                cancelled_count = len(batch) - len(active_batch)
                if cancelled_count:
                    with self._state_lock:
                        self._accepted_operations -= cancelled_count
                if not active_batch:
                    continue
                durable = any(operation.durable for operation in active_batch)
                self._set_synchronous(connection, "FULL" if durable else "NORMAL")
                try:
                    outcomes = self._execute_batch(connection, active_batch)
                except BaseException as exc:
                    with self._state_lock:
                        self._accepted_operations -= len(active_batch)
                    self._resolve([(operation, None, exc) for operation in active_batch])
                    raise
                with self._state_lock:
                    self._accepted_operations -= len(active_batch)
                if any(error is None for _, _, error in outcomes):
                    if durable:
                        self._relaxed_since = None  # a FULL commit syncs earlier ones too
                    elif self._relaxed_since is None:
                        self._relaxed_since = time.monotonic()
                self._resolve(outcomes)
        except BaseException:
            _logger.exception("sqlite_write_worker_failed database_name=%s", self.db_path.name)
            with self._state_lock:
                self._stopping = True
            self._fail_queued(RuntimeError("SQLite write worker stopped unexpectedly"))
        finally:
            if connection is not None:
                connection.close()

    def _next_operation(self, connection: sqlite3.Connection) -> _Operation[object] | None:
        while True:
            try:
                return self._queue.get(timeout=0.05)
            except queue.Empty:
                if (
                    self._relaxed_since is not None
                    and time.monotonic() - self._relaxed_since >= self._relaxed_barrier_s
                ):
                    self._barrier_if_relaxed(connection)
                with self._state_lock:
                    if self._stopping and self._queue.empty():
                        return None

    def _set_synchronous(self, connection: sqlite3.Connection, mode: str) -> None:
        if self._synchronous != mode:
            connection.execute(f"PRAGMA synchronous={mode}")
            self._synchronous = mode

    def _barrier_if_relaxed(self, connection: sqlite3.Connection) -> None:
        """Sync every relaxed commit so far with one FULL commit that writes.

        A FULL commit syncs the WAL up to its own frame, which includes the
        earlier NORMAL commits; it must write something to sync at all.
        """
        if self._relaxed_since is None:
            return
        self._set_synchronous(connection, "FULL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS durability_marks ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), synced_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO durability_marks (id, synced_at) VALUES (1, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET synced_at = excluded.synced_at"
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        self._relaxed_since = None

    def _collect_batch(self, first: _Operation[object]) -> list[_Operation[object]]:
        batch = [first]
        deadline = time.monotonic() + self._batch_window_s
        while len(batch) < self._max_batch_items:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(self._queue.get(timeout=remaining))
            except queue.Empty:
                break
        return batch

    @staticmethod
    def _execute_batch(
        connection: sqlite3.Connection,
        batch: list[_Operation[object]],
    ) -> list[tuple[_Operation[object], object | None, BaseException | None]]:
        outcomes: list[tuple[_Operation[object], object | None, BaseException | None]] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
        except Exception as exc:
            for operation in batch:
                outcomes.append((operation, None, exc))
            return outcomes

        transaction_error: BaseException | None = None
        for operation in batch:
            if transaction_error is not None:
                outcomes.append((operation, None, transaction_error))
                continue
            try:
                connection.execute("SAVEPOINT iris_write_operation")
                value = operation.callback(connection)
                connection.execute("RELEASE SAVEPOINT iris_write_operation")
            except Exception as exc:
                try:
                    connection.execute("ROLLBACK TO SAVEPOINT iris_write_operation")
                    connection.execute("RELEASE SAVEPOINT iris_write_operation")
                except sqlite3.Error:
                    # A broken transaction prevents safe sibling execution.
                    outcomes.append((operation, None, exc))
                    transaction_error = exc
                    continue
                outcomes.append((operation, None, exc))
            else:
                outcomes.append((operation, value, None))

        has_success = any(error is None for _, _, error in outcomes)
        try:
            if has_success:
                connection.commit()
            else:
                connection.rollback()
        except Exception as commit_error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            outcomes = [
                (operation, None, error or commit_error) for operation, _, error in outcomes
            ]
        return outcomes

    @staticmethod
    def _resolve(
        outcomes: list[tuple[_Operation[object], object | None, BaseException | None]],
    ) -> None:
        for operation, value, error in outcomes:
            if operation.future.done():
                continue
            try:
                if error is None:
                    operation.future.set_result(value)
                else:
                    operation.future.set_exception(error)
            except InvalidStateError:
                # A caller may cancel after the done() check. The database
                # transaction has already committed; cancellation must not
                # take down the account's writer or strand sibling work.
                continue

    def _fail_queued(self, error: BaseException) -> None:
        while True:
            try:
                operation = self._queue.get_nowait()
            except queue.Empty:
                return
            if not operation.future.done():
                operation.future.set_exception(error)
            with self._state_lock:
                self._accepted_operations -= 1
