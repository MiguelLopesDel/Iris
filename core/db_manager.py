import sqlite3
import threading
from pathlib import Path
from typing import Any


class DatabaseManager:
    """Manages SQLite connections with thread-local persistence and WAL mode."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._known_tables: frozenset[str] = frozenset()
        self._library_roots: dict[int, Path] = {}
        self._table_columns_cache: dict[str, frozenset[str]] = {}
        if self.db_path.exists():
            self._known_tables = self._load_known_tables()
            self._library_roots = self._load_libraries()

    def get_connection(self) -> sqlite3.Connection:
        if getattr(self._local, "conn", None) is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency and performance
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    def _load_known_tables(self) -> frozenset[str]:
        if not self.db_path.exists():
            return frozenset()
        conn = self.get_connection()
        return frozenset(
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        )

    def invalidate_table_cache(self) -> None:
        self._known_tables = self._load_known_tables()
        self._table_columns_cache.clear()

    def has_collections_tables(self) -> bool:
        return "collections" in self._known_tables and "media_collections" in self._known_tables

    def has_concept_tables(self) -> bool:
        return "concepts" in self._known_tables

    def get_library_roots(self) -> dict[int, Path]:
        return self._library_roots

    def _load_libraries(self) -> dict[int, Path]:
        if "media_libraries" not in self._known_tables:
            return {}
        conn = self.get_connection()
        rows = conn.execute("SELECT id, root_path FROM media_libraries").fetchall()
        mapping = {}
        for row in rows:
            try:
                mapping[int(row["id"])] = Path(str(row["root_path"])).resolve()
            except Exception:
                continue
        return mapping

    def table_columns(self, table: str) -> frozenset[str]:
        if table in self._table_columns_cache:
            return self._table_columns_cache[table]
        conn = self.get_connection()
        cols = frozenset(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
        self._table_columns_cache[table] = cols
        return cols

    # -- Collections Operations --
    def list_collections(self) -> list[dict[str, Any]]:
        if not self.db_path.exists() or not self.has_collections_tables():
            return []
        conn = self.get_connection()
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.description, COUNT(mc.meme_id) AS count
            FROM collections c
            LEFT JOIN media_collections mc ON mc.collection_id = c.id
            GROUP BY c.id
            ORDER BY c.name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def create_collection(self, name: str, description: str = "") -> int:
        import datetime as _dt
        conn = self.get_connection()
        cursor = conn.execute(
            "INSERT INTO collections (name, description, created_at) VALUES (?, ?, ?)",
            (name.strip(), description.strip(), _dt.datetime.now().isoformat()),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def rename_collection(self, collection_id: int, new_name: str) -> None:
        conn = self.get_connection()
        conn.execute(
            "UPDATE collections SET name = ? WHERE id = ?",
            (new_name.strip(), collection_id),
        )
        conn.commit()

    def delete_collection(self, collection_id: int) -> None:
        conn = self.get_connection()
        conn.execute("DELETE FROM media_collections WHERE collection_id = ?", (collection_id,))
        conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        conn.commit()

    def add_records_to_collection(self, db_ids: list[int], collection_id: int) -> int:
        import datetime as _dt
        if not db_ids:
            return 0
        now = _dt.datetime.now().isoformat()
        conn = self.get_connection()
        conn.executemany(
            "INSERT OR IGNORE INTO media_collections (meme_id, collection_id, added_at) VALUES (?, ?, ?)",
            [(db_id, collection_id, now) for db_id in db_ids],
        )
        conn.commit()
        return len(db_ids)

    def remove_records_from_collection(self, db_ids: list[int], collection_id: int) -> None:
        if not db_ids:
            return
        conn = self.get_connection()
        conn.executemany(
            "DELETE FROM media_collections WHERE meme_id = ? AND collection_id = ?",
            [(db_id, collection_id) for db_id in db_ids],
        )
        conn.commit()

    def get_record_collections(self, db_id: int) -> list[dict[str, Any]]:
        if not self.db_path.exists() or not self.has_collections_tables():
            return []
        conn = self.get_connection()
        rows = conn.execute(
            """
            SELECT c.id, c.name
            FROM collections c
            JOIN media_collections mc ON mc.collection_id = c.id
            WHERE mc.meme_id = ?
            ORDER BY c.name
            """,
            (db_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_collection_db_ids(self, collection_ids: frozenset[int]) -> frozenset[int]:
        if not collection_ids or not self.has_collections_tables():
            return frozenset()
        placeholders = ",".join("?" * len(collection_ids))
        conn = self.get_connection()
        rows = conn.execute(
            f"SELECT meme_id FROM media_collections WHERE collection_id IN ({placeholders})",
            list(collection_ids),
        ).fetchall()
        return frozenset(row[0] for row in rows)
