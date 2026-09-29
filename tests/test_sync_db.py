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
