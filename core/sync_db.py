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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_changes_sequence ON sync_changes(sequence)")


def append_change(conn: sqlite3.Connection, entity_type: str, entity_id: str, operation: str, revision: int, payload: dict) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_changes (entity_type, entity_id, operation, revision, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (entity_type, entity_id, operation, revision, json.dumps(payload, separators=(",", ":")), now_iso()),
    )
    return int(cursor.lastrowid)


def changes_after(conn: sqlite3.Connection, cursor: int, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT sequence, entity_type, entity_id, operation, revision, payload_json, created_at FROM sync_changes WHERE sequence > ? ORDER BY sequence LIMIT ?",
        (cursor, limit),
    ).fetchall()
    return [
        {"cursor": int(row[0]), "entity_type": row[1], "entity_id": row[2], "operation": row[3], "revision": int(row[4]), "payload": json.loads(row[5]), "created_at": row[6]}
        for row in rows
    ]
