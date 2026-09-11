"""Per-library persistence for device synchronization."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sync_changes (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL, operation TEXT NOT NULL, revision INTEGER NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sync_uploads (
        id TEXT PRIMARY KEY, device_id TEXT NOT NULL, filename TEXT NOT NULL,
        expected_size INTEGER NOT NULL, expected_hash TEXT NOT NULL, received_size INTEGER NOT NULL DEFAULT 0,
        captured_at TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, temp_path TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )
    upload_columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_uploads)")}
    for name, declaration in (
        ("source_id", "TEXT NOT NULL DEFAULT ''"),
        ("source_name", "TEXT NOT NULL DEFAULT ''"),
        ("source_relative_path", "TEXT NOT NULL DEFAULT ''"),
        ("source_volume", "TEXT NOT NULL DEFAULT ''"),
        ("source_media_store_id", "TEXT NOT NULL DEFAULT ''"),
        ("source_generation", "INTEGER NOT NULL DEFAULT 0"),
        ("source_media_kind", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in upload_columns:
            conn.execute(f"ALTER TABLE sync_uploads ADD COLUMN {name} {declaration}")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS device_sources (
        device_id TEXT NOT NULL, source_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
        relative_path TEXT NOT NULL DEFAULT '', volume TEXT NOT NULL DEFAULT '',
        media_kind TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
        PRIMARY KEY (device_id, source_id))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS media_origins (
        media_id INTEGER NOT NULL, device_id TEXT NOT NULL, source_id TEXT NOT NULL,
        media_store_id TEXT NOT NULL DEFAULT '', source_generation INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        PRIMARY KEY (media_id, device_id, source_id, media_store_id),
        FOREIGN KEY (media_id) REFERENCES memes(id) ON DELETE CASCADE)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_changes_sequence ON sync_changes(sequence)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_origins_source ON media_origins(device_id, source_id)")


def append_change(conn: sqlite3.Connection, entity_type: str, entity_id: str, operation: str, revision: int, payload: dict) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_changes (entity_type, entity_id, operation, revision, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (entity_type, entity_id, operation, revision, json.dumps(payload, separators=(",", ":")), now_iso()),
    )
    return int(cursor.lastrowid)


def record_origin(
    conn: sqlite3.Connection,
    media_id: int,
    device_id: str,
    source: dict,
) -> None:
    """Attach a device source to an item without trusting it as a filesystem path."""
    if not source.get("id"):
        return
    conn.execute(
        """INSERT INTO device_sources
        (device_id, source_id, name, relative_path, volume, media_kind, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id, source_id) DO UPDATE SET
        name=excluded.name, relative_path=excluded.relative_path, volume=excluded.volume,
        media_kind=excluded.media_kind, updated_at=excluded.updated_at""",
        (device_id, source["id"], source.get("name", ""), source.get("relative_path", ""),
         source.get("volume", ""), source.get("media_kind", ""), now_iso()),
    )
    conn.execute(
        """INSERT OR REPLACE INTO media_origins
        (media_id, device_id, source_id, media_store_id, source_generation, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (media_id, device_id, source["id"], source.get("media_store_id", ""),
         source.get("generation", 0), now_iso()),
    )


def changes_after(conn: sqlite3.Connection, cursor: int, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT sequence, entity_type, entity_id, operation, revision, payload_json, created_at FROM sync_changes WHERE sequence > ? ORDER BY sequence LIMIT ?",
        (cursor, limit),
    ).fetchall()
    return [
        {"cursor": int(row[0]), "entity_type": row[1], "entity_id": row[2], "operation": row[3], "revision": int(row[4]), "payload": json.loads(row[5]), "created_at": row[6]}
        for row in rows
    ]
