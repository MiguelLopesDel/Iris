"""Group the disk syncs of device sync into one per request.

Every sync request used to commit several times with SQLite's default
``synchronous=FULL``, and each such commit waits for the WAL to reach the
disk: one forced write per state change, ~6 per uploaded item. On a spinning
disk that capped uploads at about ten items a second.

In WAL mode, commits made with ``synchronous=NORMAL`` are appended to the WAL
without waiting for the disk, and a later commit with ``synchronous=FULL``
syncs the whole WAL file, which makes every earlier transaction durable, from
any connection (D. Richard Hipp, SQLite forum, "Switching pragma synchronous
to upgrade durability"). A power loss can only roll the database back to an
earlier consistent point, never corrupt it.

So sync work commits with :func:`connect_deferred` connections, and a request
calls :func:`make_durable` once before it tells the device that anything is
stored. Files are still synced as they are written; after a power loss the
database may be behind the files, and the completion path recovers from that.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def connect_deferred(db_path: Path, **kwargs) -> sqlite3.Connection:
    """A connection whose commits do not wait for the disk; pair with make_durable."""
    connection = sqlite3.connect(db_path, **kwargs)
    # Only WAL keeps NORMAL consistent across a power loss; any other journal
    # mode keeps SQLite's default.
    if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
        connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def make_durable(db_path: Path) -> None:
    """Sync every transaction committed so far to disk, from any connection.

    The commit must write: a transaction that changes nothing may not sync.
    """
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS durability_marks ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), synced_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO durability_marks (id, synced_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET synced_at = excluded.synced_at",
            (datetime.now(UTC).isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()
