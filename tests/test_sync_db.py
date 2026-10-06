from __future__ import annotations

import sqlite3

from core.library_quota import used_bytes
from core.sync_db import ensure_tables


def test_sync_schema_upgrade_preserves_existing_uploads() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE sync_uploads (
        id TEXT PRIMARY KEY, device_id TEXT NOT NULL, filename TEXT NOT NULL,
        expected_size INTEGER NOT NULL, expected_hash TEXT NOT NULL,
        received_size INTEGER NOT NULL DEFAULT 0, captured_at TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL, temp_path TEXT NOT NULL, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)"""
    )
    conn.execute(
        """INSERT INTO sync_uploads
        (id, device_id, filename, expected_size, expected_hash, captured_at, state,
         temp_path, created_at, updated_at)
        VALUES ('u1', 'd1', 'photo.jpg', 1, 'hash', '', 'uploading', '/tmp/u1', '', '')"""
    )

    ensure_tables(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_uploads)")}
    assert "source_id" in columns
    assert "source_generation" in columns
    assert conn.execute("SELECT filename FROM sync_uploads WHERE id = 'u1'").fetchone() == ("photo.jpg",)


def test_library_usage_counter_tracks_catalog_changes() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, file_size INTEGER DEFAULT 0)")
    conn.executemany(
        "INSERT INTO memes (id, file_size) VALUES (?, ?)",
        [(1, 12), (2, 8), (3, 0)],
    )

    ensure_tables(conn)
    assert used_bytes(conn) == 20

    conn.execute("INSERT INTO memes (id, file_size) VALUES (4, 5)")
    assert used_bytes(conn) == 25
    conn.execute("UPDATE memes SET file_size = 2 WHERE id = 1")
    assert used_bytes(conn) == 15
    conn.execute("DELETE FROM memes WHERE id = 2")
    assert used_bytes(conn) == 7


def test_library_usage_is_summed_once_not_on_every_request() -> None:
    # ensure_tables runs on every sync request; summing the whole library
    # each time made every request scan it.
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, file_size INTEGER DEFAULT 0)")
    conn.executemany("INSERT INTO memes (id, file_size) VALUES (?, ?)", [(1, 12), (2, 8)])
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    for _ in range(3):
        ensure_tables(conn)

    assert sum("SUM(" in statement for statement in statements) == 1
    assert used_bytes(conn) == 20


class _OtherConnectionWritesFirst:
    """Lets another connection initialize between this one's check and write."""

    def __init__(self, conn: sqlite3.Connection, check: str, other) -> None:
        self._conn, self._check, self._other = conn, check, other

    def execute(self, sql: str, *args):
        cursor = self._conn.execute(sql, *args)
        if self._check in sql and self._other is not None:
            other, self._other = self._other, None
            other()
        return cursor


def test_two_connections_creating_the_usage_counter_at_once(tmp_path) -> None:
    db = tmp_path / "iris.db"
    first = sqlite3.connect(db, isolation_level=None)
    first.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, file_size INTEGER DEFAULT 0)")
    first.execute("INSERT INTO memes (id, file_size) VALUES (1, 7)")
    second = sqlite3.connect(db, isolation_level=None)

    ensure_tables(_OtherConnectionWritesFirst(
        first, "SELECT 1 FROM library_storage_usage", lambda: ensure_tables(second),
    ))

    assert used_bytes(first) == 7
