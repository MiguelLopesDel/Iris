"""The item catalogue of one shared space, stored apart from every private library.

Each space owns a directory with its own SQLite file and its own copy of the
bytes. Adding a private item clones the original in (see :mod:`core.fs_clone`:
a reflink where the filesystem allows, a byte copy otherwise), so the shared
item keeps existing after its author trashes the private one; nothing here
refers back to a private library's ids or paths.

Bytes are stored by SHA-256, so the same content added twice to a space is
stored once. Removing an item moves it to the space's trash: hidden, still
restorable, bytes untouched. Only :func:`purge_expired`, once the retention
period is over, frees the bytes -- that is the end of the recoverable-trash
rule, not a shortcut around it.

Access control is the caller's job (see :mod:`core.shared_spaces`); the only
permission decided here is who may remove or restore which item, because it
depends on the item's author.
"""

from __future__ import annotations

import mimetypes
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from core.file_digest import FileDigest
from core.fs_clone import clone_file
from core.users_db import now_iso

SCHEMA_VERSION = 3
MAX_PAGE = 200
DEFAULT_QUOTA_BYTES = 10 * 1024**4
DEFAULT_TRASH_DAYS = 30
_SUFFIX = re.compile(r"^\.[a-z0-9]{1,10}$")
_HASH_CHUNK = 1024 * 1024


class SpaceItemNotFound(Exception):
    """No item with this id exists in this view of the space."""


class SpaceItemPermissionDenied(Exception):
    """The actor's role does not allow changing this item."""


class SpaceItemConflict(Exception):
    """The same content is already visible in the space."""


class SpaceQuotaExceeded(Exception):
    """Storing this item would take the space over its quota."""


class SpaceCatalogVersionError(RuntimeError):
    """The space database was written by a newer, unknown schema."""


@dataclass(frozen=True)
class SpaceStorage:
    """Installer-chosen policy, resolved once at startup."""

    strategy: str = "copy"
    quota_bytes: int = DEFAULT_QUOTA_BYTES
    trash_days: int = DEFAULT_TRASH_DAYS


@dataclass(frozen=True)
class SpaceItem:
    id: int
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    added_by: int | None
    added_at: str
    storage_method: str
    removed_at: str | None

    @property
    def media_type(self) -> str:
        return "video" if self.mime_type.startswith("video/") else "image"


@dataclass(frozen=True)
class ItemMetadata:
    """What the item already carried in its author's library, if anything."""

    description: str = ""
    embedding: bytes | None = None
    embedding_model: str | None = None


def space_root(data_dir: Path, space_id: int) -> Path:
    return data_dir / "spaces" / str(int(space_id))


# -- schema -----------------------------------------------------------------


def _connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    # Autocommit: writes open their own BEGIN IMMEDIATE, see _write.
    connection = sqlite3.connect(root / "space.db", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        _migrate(connection)
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def _write(connection: sqlite3.Connection) -> Iterator[None]:
    """One serialised write transaction: check-then-act stays atomic."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def _create_v1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sha256 TEXT NOT NULL,
            storage_name TEXT NOT NULL,
            original_name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            added_by INTEGER,
            added_at TEXT NOT NULL,
            removed_at TEXT,
            removed_by INTEGER
        )
        """
    )
    # One visible item per content; a removed one may be added again.
    connection.execute(
        "CREATE UNIQUE INDEX idx_items_visible_sha256 ON items(sha256) WHERE removed_at IS NULL"
    )


def _v1_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE items ADD COLUMN storage_method TEXT NOT NULL DEFAULT 'copy'"
    )
    connection.execute("ALTER TABLE items ADD COLUMN purged_at TEXT")
    connection.execute("CREATE INDEX idx_items_sha256 ON items(sha256)")
    connection.execute(
        "CREATE INDEX idx_items_trash ON items(removed_at) "
        "WHERE removed_at IS NOT NULL AND purged_at IS NULL"
    )
    # Logical bytes held by the space, kept incrementally so a quota check
    # does not scan the catalogue.
    connection.execute(
        "CREATE TABLE usage (id INTEGER PRIMARY KEY CHECK (id = 1), bytes INTEGER NOT NULL)"
    )
    connection.execute(
        """
        INSERT INTO usage (id, bytes)
        SELECT 1, COALESCE(SUM(size), 0) FROM (
            SELECT MAX(size_bytes) AS size FROM items GROUP BY sha256
        )
        """
    )


def _v2_to_v3(connection: sqlite3.Connection) -> None:
    # What the photo already had in its author's library, carried along so a
    # space can be searched without reprocessing anything.
    connection.execute("ALTER TABLE items ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    connection.execute("ALTER TABLE items ADD COLUMN embedding BLOB")
    connection.execute("ALTER TABLE items ADD COLUMN embedding_model TEXT")
    connection.execute(
        """
        CREATE TABLE albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE album_items (
            album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES items(id),
            added_by INTEGER,
            added_at TEXT NOT NULL,
            PRIMARY KEY (album_id, item_id)
        )
        """
    )
    connection.execute("CREATE INDEX idx_album_items_item ON album_items(item_id)")


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _v1_to_v2, 2: _v2_to_v3}


def _migrate(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise SpaceCatalogVersionError(
            f"space.db schema {version} is newer than supported {SCHEMA_VERSION}"
        )
    if version == SCHEMA_VERSION:
        return
    with _write(connection):
        # Re-read under the lock: another process may have migrated meanwhile.
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            _create_v1(connection)
            version = 1
        while version < SCHEMA_VERSION:
            _MIGRATIONS[version](connection)
            version += 1
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# -- rows -------------------------------------------------------------------

_COLUMNS = (
    "id, original_name, mime_type, size_bytes, sha256, added_by, added_at, "
    "storage_method, removed_at, storage_name"
)


def _item_from_row(row: sqlite3.Row) -> SpaceItem:
    return SpaceItem(
        id=int(row["id"]),
        original_name=str(row["original_name"]),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),
        sha256=str(row["sha256"]),
        added_by=int(row["added_by"]) if row["added_by"] is not None else None,
        added_at=str(row["added_at"]),
        storage_method=str(row["storage_method"]),
        removed_at=str(row["removed_at"]) if row["removed_at"] is not None else None,
    )


def _row(connection: sqlite3.Connection, item_id: int, *, removed: bool) -> sqlite3.Row:
    state = "removed_at IS NOT NULL AND purged_at IS NULL" if removed else "removed_at IS NULL"
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM items WHERE id = ? AND {state}", (item_id,)
    ).fetchone()
    if row is None:
        raise SpaceItemNotFound
    return row


def _storage_name(sha256: str, original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    # The suffix is kept because thumbnailing dispatches on it (video vs image).
    return f"{sha256[:2]}/{sha256}{suffix if _SUFFIX.fullmatch(suffix) else ''}"


def _sha256(path: Path) -> tuple[str, int]:
    return FileDigest.sha256_with_size(path, _HASH_CHUNK)


def _usage(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT bytes FROM usage WHERE id = 1").fetchone()[0])


def _stored(connection: sqlite3.Connection, sha256: str) -> bool:
    """Whether these bytes are still on disk for some non-purged item."""
    return _kept_bytes(connection, sha256) is not None


def _kept_bytes(connection: sqlite3.Connection, sha256: str) -> tuple[str, str] | None:
    """(storage_name, storage_method) of bytes already held for ``sha256``."""
    row = connection.execute(
        "SELECT storage_name, storage_method FROM items "
        "WHERE sha256 = ? AND purged_at IS NULL ORDER BY id LIMIT 1",
        (sha256,),
    ).fetchone()
    return (str(row[0]), str(row[1])) if row is not None else None


# -- operations -------------------------------------------------------------


def add_item(
    root: Path,
    source: Path,
    original_name: str,
    added_by: int,
    storage: SpaceStorage | None = None,
    metadata: ItemMetadata | None = None,
) -> tuple[SpaceItem, bool]:
    """Clone one original into the space. Returns the item and whether it is new."""
    storage = storage or SpaceStorage()
    metadata = metadata or ItemMetadata()
    name = Path(original_name).name.strip() or source.name
    connection = _connect(root)
    incoming = root / "incoming"
    incoming.mkdir(exist_ok=True)
    temporary = incoming / f"{uuid.uuid4().hex}.part"
    try:
        # Hash the clone, not the source: the clone is a snapshot, so the hash
        # describes exactly the bytes that end up stored.
        method = clone_file(source, temporary, storage.strategy)
        sha256, size = _sha256(temporary)
        with _write(connection):
            existing = connection.execute(
                f"SELECT {_COLUMNS} FROM items WHERE sha256 = ? AND removed_at IS NULL",
                (sha256,),
            ).fetchone()
            if existing is not None:
                return _item_from_row(existing), False
            kept = _kept_bytes(connection, sha256)
            growth = 0 if kept else size
            if growth and _usage(connection) + growth > storage.quota_bytes:
                raise SpaceQuotaExceeded
            if kept:
                # Same bytes, e.g. still in the trash: share them, whatever
                # the new name's extension, and keep how they were made.
                storage_name, method = kept
            else:
                storage_name = _storage_name(sha256, name)
                target = root / "media" / storage_name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporary, target)
            cursor = connection.execute(
                """
                INSERT INTO items (sha256, storage_name, original_name, mime_type,
                                   size_bytes, added_by, added_at, storage_method,
                                   description, embedding, embedding_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sha256,
                    storage_name,
                    name,
                    mimetypes.guess_type(name)[0] or "application/octet-stream",
                    size,
                    added_by,
                    now_iso(),
                    method,
                    metadata.description,
                    metadata.embedding,
                    metadata.embedding_model,
                ),
            )
            connection.execute("UPDATE usage SET bytes = bytes + ? WHERE id = 1", (growth,))
            return _item_from_row(_row(connection, int(cursor.lastrowid), removed=False)), True
    finally:
        # Only this call's own staging file; never a stored or user file.
        temporary.unlink(missing_ok=True)
        connection.close()


def list_items(root: Path, limit: int, before: int | None = None) -> list[SpaceItem]:
    """Visible items, newest first; ``before`` is the last id of the previous page."""
    limit = max(1, min(int(limit), MAX_PAGE))
    connection = _connect(root)
    try:
        rows = connection.execute(
            f"""
            SELECT {_COLUMNS} FROM items
            WHERE removed_at IS NULL AND (? IS NULL OR id < ?)
            ORDER BY id DESC LIMIT ?
            """,
            (before, before, limit),
        ).fetchall()
    finally:
        connection.close()
    return [_item_from_row(row) for row in rows]


def get_item(root: Path, item_id: int) -> SpaceItem:
    connection = _connect(root)
    try:
        return _item_from_row(_row(connection, item_id, removed=False))
    finally:
        connection.close()


def item_original(root: Path, item_id: int) -> tuple[SpaceItem, Path]:
    connection = _connect(root)
    try:
        row = _row(connection, item_id, removed=False)
    finally:
        connection.close()
    path = root / "media" / str(row["storage_name"])
    if not path.is_file():
        raise SpaceItemNotFound
    return _item_from_row(row), path


def item_thumbnail(
    root: Path, item_id: int, generate: Callable[[str, Path], None]
) -> Path:
    item, original = item_original(root, item_id)
    thumb = root / "thumbnails" / f"{item.sha256}.jpg"
    if not thumb.exists():
        generate(str(original), thumb)
    if not thumb.exists():
        raise SpaceItemNotFound
    return thumb


def usage_bytes(root: Path) -> int:
    connection = _connect(root)
    try:
        return _usage(connection)
    finally:
        connection.close()


# -- trash ------------------------------------------------------------------


def can_remove(role: str, actor_id: int, item: SpaceItem) -> bool:
    if role == "manager":
        return True
    return role == "contributor" and item.added_by == actor_id


def remove_item(root: Path, item_id: int, actor_id: int, role: str) -> None:
    """Move an item to the space's trash; its bytes stay for the retention period."""
    connection = _connect(root)
    try:
        with _write(connection):
            item = _item_from_row(_row(connection, item_id, removed=False))
            if not can_remove(role, actor_id, item):
                raise SpaceItemPermissionDenied
            connection.execute(
                "UPDATE items SET removed_at = ?, removed_by = ? WHERE id = ?",
                (now_iso(), actor_id, item_id),
            )
    finally:
        connection.close()


def list_trash(
    root: Path, actor_id: int, role: str, limit: int, before: int | None = None
) -> list[SpaceItem]:
    """Trashed items the actor could restore, newest id first."""
    if role not in {"manager", "contributor"}:
        return []
    limit = max(1, min(int(limit), MAX_PAGE))
    connection = _connect(root)
    try:
        rows = connection.execute(
            f"""
            SELECT {_COLUMNS} FROM items
            WHERE removed_at IS NOT NULL AND purged_at IS NULL
              AND (? = 'manager' OR added_by = ?)
              AND (? IS NULL OR id < ?)
            ORDER BY id DESC LIMIT ?
            """,
            (role, actor_id, before, before, limit),
        ).fetchall()
    finally:
        connection.close()
    return [_item_from_row(row) for row in rows]


def restore_item(root: Path, item_id: int, actor_id: int, role: str) -> SpaceItem:
    connection = _connect(root)
    try:
        with _write(connection):
            item = _item_from_row(_row(connection, item_id, removed=True))
            if not can_remove(role, actor_id, item):
                raise SpaceItemPermissionDenied
            try:
                connection.execute(
                    "UPDATE items SET removed_at = NULL, removed_by = NULL WHERE id = ?",
                    (item_id,),
                )
            except sqlite3.IntegrityError as exc:
                raise SpaceItemConflict from exc
            return _item_from_row(_row(connection, item_id, removed=False))
    finally:
        connection.close()


def purge_after(item: SpaceItem, trash_days: int) -> str | None:
    if item.removed_at is None:
        return None
    removed = datetime.fromisoformat(item.removed_at)
    return (removed + timedelta(days=trash_days)).isoformat()


def purge_expired(root: Path, trash_days: int, now: datetime | None = None) -> int:
    """Definitively drop items trashed more than ``trash_days`` ago.

    Bytes are freed only when no other non-purged item still uses them, so a
    re-added copy of the same content survives the purge of the old one.
    """
    if not (root / "space.db").exists():
        return 0
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=trash_days)).isoformat()
    connection = _connect(root)
    freed: list[tuple[str, str]] = []
    try:
        # A plain read first: this runs on every gallery view, and a write
        # lock per view would serialise readers for nothing.
        due = connection.execute(
            "SELECT 1 FROM items WHERE removed_at IS NOT NULL AND purged_at IS NULL "
            "AND removed_at < ? LIMIT 1",
            (cutoff,),
        ).fetchone()
        if due is None:
            return 0
        with _write(connection):
            rows = connection.execute(
                """
                SELECT id, sha256, storage_name, size_bytes FROM items
                WHERE removed_at IS NOT NULL AND purged_at IS NULL AND removed_at < ?
                """,
                (cutoff,),
            ).fetchall()
            if not rows:
                return 0
            stamp = now_iso()
            connection.executemany(
                "UPDATE items SET purged_at = ? WHERE id = ?",
                [(stamp, int(row["id"])) for row in rows],
            )
            released = 0
            for sha256, storage_name, size in {
                (str(r["sha256"]), str(r["storage_name"]), int(r["size_bytes"])) for r in rows
            }:
                if not _stored(connection, sha256):
                    freed.append((sha256, storage_name))
                    released += size
            connection.execute(
                "UPDATE usage SET bytes = MAX(0, bytes - ?) WHERE id = 1", (released,)
            )
    finally:
        connection.close()
    # After the commit: a crash here leaves orphan bytes, never a live row
    # pointing at a missing file.
    for sha256, storage_name in freed:
        (root / "media" / storage_name).unlink(missing_ok=True)
        (root / "thumbnails" / f"{sha256}.jpg").unlink(missing_ok=True)
    return len(rows)


# -- search -------------------------------------------------------------------


def search_items(
    root: Path,
    terms: list[str],
    query_vector: np.ndarray | None = None,
    model: str | None = None,
    limit: int = 60,
    min_similarity: float = 0.2,
) -> list[SpaceItem]:
    """Visible items matching ``terms`` (already folded) or close to the query.

    Text matches the file name and the description carried from the author's
    library. Similarity compares only vectors of the same model: two accounts
    may use different ones, and their vectors live in unrelated spaces.
    Without a query vector, only text counts.

    Scans the space's rows: a family space is small, and this is a bounded
    per-space cost, not a library-wide one.
    """
    from core.search_types import normalize_text

    connection = _connect(root)
    try:
        rows = connection.execute(
            f"SELECT {_COLUMNS}, description, embedding, embedding_model FROM items"
            " WHERE removed_at IS NULL ORDER BY id DESC"
        ).fetchall()
    finally:
        connection.close()
    query = None
    if query_vector is not None and model:
        query = np.asarray(query_vector, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        query = query / norm if norm else None
    scored: list[tuple[float, int, SpaceItem]] = []
    for row in rows:
        haystack = normalize_text(f"{row['original_name']} {row['description']}")
        text = sum(1 for term in terms if term in haystack) / len(terms) if terms else 0.0
        similarity = 0.0
        blob = row["embedding"]
        if query is not None and blob and row["embedding_model"] == model:
            vector = np.frombuffer(blob, dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if vector.shape == query.shape and norm:
                similarity = float(vector @ query) / norm
        if text > 0 or similarity >= min_similarity:
            scored.append((similarity + text, int(row["id"]), _item_from_row(row)))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [item for _, _, item in scored[: max(1, min(limit, MAX_PAGE))]]


# -- albums -------------------------------------------------------------------


class AlbumNotFound(Exception):
    """No album with this id in this space."""


@dataclass(frozen=True)
class Album:
    id: int
    name: str
    created_by: int | None
    created_at: str
    count: int
    cover_item_id: int | None


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not 1 <= len(cleaned) <= 120:
        raise ValueError("O nome do álbum precisa ter de 1 a 120 caracteres")
    return cleaned


def _album(connection: sqlite3.Connection, album_id: int) -> Album:
    row = connection.execute(
        """
        SELECT a.id, a.name, a.created_by, a.created_at,
               COUNT(i.id) AS count, MAX(i.id) AS cover
        FROM albums a
        LEFT JOIN album_items ai ON ai.album_id = a.id
        LEFT JOIN items i ON i.id = ai.item_id AND i.removed_at IS NULL
        WHERE a.id = ? GROUP BY a.id
        """,
        (album_id,),
    ).fetchone()
    if row is None:
        raise AlbumNotFound
    return Album(
        id=int(row["id"]), name=str(row["name"]),
        created_by=int(row["created_by"]) if row["created_by"] is not None else None,
        created_at=str(row["created_at"]), count=int(row["count"]),
        cover_item_id=int(row["cover"]) if row["cover"] is not None else None,
    )


def can_edit_album(role: str, actor_id: int, album: Album) -> bool:
    return role == "manager" or (role == "contributor" and album.created_by == actor_id)


def create_album(root: Path, name: str, actor_id: int, role: str) -> Album:
    if role not in {"manager", "contributor"}:
        raise SpaceItemPermissionDenied
    cleaned = _clean_name(name)
    connection = _connect(root)
    try:
        with _write(connection):
            cursor = connection.execute(
                "INSERT INTO albums (name, created_by, created_at) VALUES (?, ?, ?)",
                (cleaned, actor_id, now_iso()),
            )
            return _album(connection, int(cursor.lastrowid))
    finally:
        connection.close()


def list_albums(root: Path) -> list[Album]:
    connection = _connect(root)
    try:
        ids = [int(r[0]) for r in connection.execute("SELECT id FROM albums ORDER BY name COLLATE NOCASE, id")]
        return [_album(connection, album_id) for album_id in ids]
    finally:
        connection.close()


def get_album(root: Path, album_id: int) -> Album:
    connection = _connect(root)
    try:
        return _album(connection, album_id)
    finally:
        connection.close()


def rename_album(root: Path, album_id: int, name: str, actor_id: int, role: str) -> Album:
    cleaned = _clean_name(name)
    connection = _connect(root)
    try:
        with _write(connection):
            if not can_edit_album(role, actor_id, _album(connection, album_id)):
                raise SpaceItemPermissionDenied
            connection.execute("UPDATE albums SET name = ? WHERE id = ?", (cleaned, album_id))
            return _album(connection, album_id)
    finally:
        connection.close()


def delete_album(root: Path, album_id: int, actor_id: int, role: str) -> None:
    """Remove the album only; its photos stay in the space."""
    connection = _connect(root)
    try:
        with _write(connection):
            if not can_edit_album(role, actor_id, _album(connection, album_id)):
                raise SpaceItemPermissionDenied
            connection.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    finally:
        connection.close()


def album_items(root: Path, album_id: int, limit: int, before: int | None = None) -> list[SpaceItem]:
    limit = max(1, min(int(limit), MAX_PAGE))
    connection = _connect(root)
    try:
        _album(connection, album_id)
        rows = connection.execute(
            f"""
            SELECT {', '.join('i.' + c.strip() for c in _COLUMNS.split(','))}
            FROM album_items ai JOIN items i ON i.id = ai.item_id
            WHERE ai.album_id = ? AND i.removed_at IS NULL AND (? IS NULL OR i.id < ?)
            ORDER BY i.id DESC LIMIT ?
            """,
            (album_id, before, before, limit),
        ).fetchall()
    finally:
        connection.close()
    return [_item_from_row(row) for row in rows]


def add_to_album(root: Path, album_id: int, item_ids: list[int], actor_id: int, role: str) -> int:
    """Add visible items of this space to the album; returns how many were new."""
    if role not in {"manager", "contributor"}:
        raise SpaceItemPermissionDenied
    connection = _connect(root)
    added = 0
    try:
        with _write(connection):
            _album(connection, album_id)
            for item_id in item_ids:
                _row(connection, item_id, removed=False)  # only this space's own items
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO album_items (album_id, item_id, added_by, added_at)"
                    " VALUES (?, ?, ?, ?)",
                    (album_id, item_id, actor_id, now_iso()),
                )
                added += cursor.rowcount
    finally:
        connection.close()
    return added


def remove_from_album(root: Path, album_id: int, item_id: int, actor_id: int, role: str) -> None:
    """Take an item out of an album; the item stays in the space."""
    connection = _connect(root)
    try:
        with _write(connection):
            album = _album(connection, album_id)
            row = connection.execute(
                "SELECT added_by FROM album_items WHERE album_id = ? AND item_id = ?",
                (album_id, item_id),
            ).fetchone()
            if row is None:
                raise SpaceItemNotFound
            allowed = can_edit_album(role, actor_id, album) or (
                role == "contributor" and row["added_by"] == actor_id
            )
            if not allowed:
                raise SpaceItemPermissionDenied
            connection.execute(
                "DELETE FROM album_items WHERE album_id = ? AND item_id = ?", (album_id, item_id)
            )
    finally:
        connection.close()
