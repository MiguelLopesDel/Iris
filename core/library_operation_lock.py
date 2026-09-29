"""Serialize filesystem operations that mutate one account library."""
from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


@contextmanager
def serialize_library_operations(db_path: Path) -> Iterator[None]:
    """Serialize upload processing, recovery moves, and index rebuilds per library."""
    with _locks[str(db_path.resolve())]:
        yield
