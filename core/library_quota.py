"""Transactional byte accounting for an account's catalogued media."""
from __future__ import annotations

import sqlite3


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create an O(1) usage counter, backfilling existing catalog rows once."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memes'"
    ).fetchone() is None:
        return

    conn.execute(
        """CREATE TABLE IF NOT EXISTS library_storage_usage (
        id INTEGER PRIMARY KEY CHECK (id = 1), bytes INTEGER NOT NULL)"""
    )
    # Backfill only when the counter is missing: INSERT OR IGNORE still runs
    # the SELECT, which summed the whole library on every call.
    if conn.execute("SELECT 1 FROM library_storage_usage WHERE id = 1").fetchone() is None:
        conn.execute(
            """INSERT INTO library_storage_usage (id, bytes)
            SELECT 1, COALESCE(SUM(CASE WHEN file_size > 0 THEN file_size ELSE 0 END), 0)
            FROM memes"""
        )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS iris_library_usage_insert
        AFTER INSERT ON memes
        BEGIN
            UPDATE library_storage_usage
            SET bytes = bytes + MAX(COALESCE(NEW.file_size, 0), 0)
            WHERE id = 1;
        END"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS iris_library_usage_delete
        AFTER DELETE ON memes
        BEGIN
            UPDATE library_storage_usage
            SET bytes = MAX(0, bytes - MAX(COALESCE(OLD.file_size, 0), 0))
            WHERE id = 1;
        END"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS iris_library_usage_update
        AFTER UPDATE OF file_size ON memes
        BEGIN
            UPDATE library_storage_usage
            SET bytes = MAX(
                0,
                bytes - MAX(COALESCE(OLD.file_size, 0), 0)
                      + MAX(COALESCE(NEW.file_size, 0), 0)
            )
            WHERE id = 1;
        END"""
    )


def used_bytes(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT bytes FROM library_storage_usage WHERE id = 1"
    ).fetchone()
    return max(0, int(row[0])) if row is not None else 0


def pending_upload_bytes(conn: sqlite3.Connection) -> int:
    """Bytes reserved by transfers not yet represented in the media catalog."""
    row = conn.execute(
        """SELECT COALESCE(SUM(expected_size), 0)
        FROM sync_uploads
        WHERE state IN ('uploading', 'finalizing', 'pending_processing', 'processing', 'failed_processing')
          AND NOT EXISTS (
              SELECT 1 FROM memes WHERE memes.content_hash = sync_uploads.expected_hash
          )"""
    ).fetchone()
    return max(0, int(row[0])) if row is not None else 0
