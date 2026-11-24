"""Favourites: the photos a person marked with a heart.

Kept in a table of their own rather than as a column of ``memes``: the
catalogue the search engine holds is immutable between rebuilds, and a heart
must not cost a rebuild of the whole library. The table lives in each
library's own database, so backups and accounts carry it along.

The ids of a library's favourites are cached per database: every record a page
returns says whether it is one, and a query per record would cost more than
the page itself.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS favorites (
    meme_id INTEGER PRIMARY KEY REFERENCES memes(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL
)
"""

_lock = threading.Lock()
_cache: dict[str, frozenset[int]] = {}


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)


def set_favorite(conn: sqlite3.Connection, db_path: Path | str, meme_id: int, favorite: bool) -> None:
    ensure_table(conn)
    if favorite:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (meme_id, added_at) VALUES (?, ?)",
            (meme_id, datetime.now(UTC).isoformat()),
        )
    else:
        conn.execute("DELETE FROM favorites WHERE meme_id = ?", (meme_id,))
    conn.commit()
    with _lock:
        _cache.pop(str(db_path), None)


def favorite_ids(conn: sqlite3.Connection, db_path: Path | str) -> frozenset[int]:
    key = str(db_path)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    ensure_table(conn)
    # Only items still in the library: a row whose item left (trash, removal)
    # is not a favourite anyone can see.
    ids = frozenset(
        int(row[0])
        for row in conn.execute(
            "SELECT f.meme_id FROM favorites f JOIN memes m ON m.id = f.meme_id"
        )
    )
    with _lock:
        _cache[key] = ids
    return ids


def forget(db_path: Path | str) -> None:
    """Drop the cached ids, for when the library changed under them."""
    with _lock:
        _cache.pop(str(db_path), None)
