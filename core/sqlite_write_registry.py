"""Own bounded SQLite write coordinators for account database files.

The registry deliberately rejects new databases when its worker limit is
reached. It does not evict a coordinator whose accepted work may still be
queued, and it never redirects an operation to another account's database.
"""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import cast

from core.sqlite_write_coordinator import SQLiteWriteCoordinator, SQLiteWriteQueueFull


class SQLiteWriteRegistryFull(SQLiteWriteQueueFull):
    """Raised when all configured account database workers are allocated."""


class SQLiteWriteCoordinatorRegistry:
    """Lazily create one write coordinator per normalized database path.

    Idle coordinators may be retired in least-recently-used order when the
    worker bound is reached. Busy workers are never evicted, and work is never
    redirected to another account database.
    """

    def __init__(
        self,
        *,
        max_coordinators: int = 64,
        coordinator_options: dict[str, object] | None = None,
    ) -> None:
        if (
            isinstance(max_coordinators, bool)
            or not isinstance(max_coordinators, int)
            or not 1 <= max_coordinators <= 4096
        ):
            raise ValueError("max_coordinators must be between 1 and 4096")
        self._max_coordinators = max_coordinators
        self._coordinator_options = dict(coordinator_options or {})
        self._coordinators: OrderedDict[Path, SQLiteWriteCoordinator] = OrderedDict()
        self._lock = threading.Lock()
        self._stopping = False

    @staticmethod
    def normalize_db_path(db_path: Path) -> Path:
        """Canonicalize path aliases without requiring the DB to exist."""
        return Path(db_path).expanduser().resolve(strict=False)

    def submit[T](
        self,
        db_path: Path,
        callback: Callable[[sqlite3.Connection], T],
        *,
        durable: bool = True,
    ) -> Future[T]:
        """Submit work to the coordinator owned by ``db_path``.

        Lookup, lazy creation, and submission are serialized with shutdown so
        an operation is either accepted by its correct worker or rejected.
        """
        normalized_path = self.normalize_db_path(db_path)
        with self._lock:
            if self._stopping:
                raise RuntimeError("SQLite write registry is shutting down")
            coordinator = self._coordinators.get(normalized_path)
            if coordinator is not None and not coordinator.is_alive:
                # A worker that failed while opening its database or committing
                # must not poison this account's registry slot permanently.
                del self._coordinators[normalized_path]
                coordinator = None
            if coordinator is None:
                if len(self._coordinators) >= self._max_coordinators:
                    evicted = False
                    for idle_path, idle_coordinator in tuple(self._coordinators.items()):
                        if idle_coordinator.retire_if_idle():
                            del self._coordinators[idle_path]
                            evicted = True
                            break
                    if not evicted:
                        raise SQLiteWriteRegistryFull("all SQLite write coordinators are busy")
                coordinator = SQLiteWriteCoordinator(
                    normalized_path,
                    **self._coordinator_options,
                )
                self._coordinators[normalized_path] = coordinator
            else:
                self._coordinators.move_to_end(normalized_path)
            return cast(Future[T], coordinator.submit(callback, durable=durable))

    def shutdown(self, *, timeout: float | None = 10.0) -> bool:
        """Stop admission and drain every worker within one shared deadline.

        Returns ``False`` if any worker remains alive when the deadline
        expires. A later call can wait again; accepted operations are never
        cancelled. The registry remains closed after shutdown starts.
        """
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be finite and non-negative")

        with self._lock:
            self._stopping = True
            coordinators = tuple(self._coordinators.values())

        deadline = None if timeout is None else time.monotonic() + timeout
        all_stopped = True
        for coordinator in coordinators:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not coordinator.shutdown(timeout=remaining):
                all_stopped = False
        return all_stopped

    def __enter__(self) -> SQLiteWriteCoordinatorRegistry:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.shutdown(timeout=None)
