"""The Iris trash of a private library: removed, restorable, purged after a delay.

Moving an item to the trash takes it out of the catalogue at once -- gallery,
search, albums and people stop showing it -- while keeping everything needed
to bring it back: the original file, moved under the library's ``trash/``
folder, and a snapshot of the catalogue row plus every row that depends on it
(album memberships, faces, taught recognitions, enrichment, sync origins).
The dependent tables are found from the schema's foreign keys, not listed by
hand, so a table added later is covered without touching this module.

Restoring puts the file back where it was and reinserts the same rows with the
same ids, so nothing is reprocessed. Purging, once the retention period is
over, is the only step that deletes an original: that is the end of the
recoverable trash, not a shortcut around it.

The item leaves the catalogue by deletion rather than by a hidden flag because
the search engine aligns its in-memory catalogue, its vector sidecar and its
FAISS indexes by row position; a filtered row would misalign all three. After
a change the engine falls back to exact search until the indexes are rebuilt.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.deleted_registry import register_deleted_hashes

DEFAULT_TRASH_DAYS = 30
MAX_PAGE = 200
_BYTES = "__bytes_b64__"


class TrashItemNotFound(Exception):
    """No trashed item with this id in this library."""


class TrashRestoreConflict(Exception):
    """Something else now occupies the item's original place."""


@dataclass(frozen=True)
class TrashedItem:
    id: int
    name: str
    size_bytes: int | None
    trashed_at: str
    purge_after: str
    has_file: bool


def trash_root(db_path: Path) -> Path:
    return db_path.parent / "trash"


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS library_trash (
            meme_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            content_hash TEXT,
            size_bytes INTEGER,
            original_path TEXT,
            trash_path TEXT,
            trashed_at TEXT NOT NULL,
            snapshot TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_library_trash_time ON library_trash(trashed_at)"
    )
    return connection


# -- snapshots ------------------------------------------------------------------


def _encode(value: object) -> object:
    if isinstance(value, bytes | bytearray | memoryview):
        return {_BYTES: base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value: object) -> object:
    if isinstance(value, dict) and _BYTES in value:
        return base64.b64decode(value[_BYTES])
    return value


def _dependents(connection: sqlite3.Connection, table: str) -> Iterator[tuple[str, str, str]]:
    """(child table, child column, parent column) for foreign keys into ``table``."""
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for child in tables:
        for fk in connection.execute(f'PRAGMA foreign_key_list("{child}")'):
            # (id, seq, table, from, to, on_update, on_delete, match)
            if fk[2] == table and str(fk[6]).upper() == "CASCADE":
                yield child, fk[3], fk[4] or "rowid"


def _snapshot(
    connection: sqlite3.Connection, table: str, column: str, value: object, depth: int = 0
) -> list[dict]:
    """Rows of ``table`` where ``column == value``, then what cascades from them."""
    rows = [dict(row) for row in connection.execute(
        f'SELECT * FROM "{table}" WHERE "{column}" = ?', (value,)
    )]
    captured = [{"table": table, "row": {k: _encode(v) for k, v in row.items()}} for row in rows]
    if depth >= 4:  # the schema is two levels deep; this only bounds a cycle
        return captured
    for child, child_column, parent_column in _dependents(connection, table):
        for row in rows:
            if parent_column in row:
                captured += _snapshot(
                    connection, child, child_column, row[parent_column], depth + 1
                )
    return captured


def _reinsert(connection: sqlite3.Connection, captured: list[dict]) -> int:
    """Put captured rows back, parents first; returns rows that could not return.

    A row whose other parent is gone -- an album deleted meanwhile -- is
    skipped rather than blocking the photo's return.
    """
    skipped = 0
    for entry in captured:
        row = {k: _decode(v) for k, v in entry["row"].items()}
        columns = ", ".join(f'"{name}"' for name in row)
        marks = ", ".join("?" for _ in row)
        try:
            connection.execute(
                f'INSERT INTO "{entry["table"]}" ({columns}) VALUES ({marks})', tuple(row.values())
            )
        except sqlite3.IntegrityError:
            if entry["table"] == "memes":
                raise
            skipped += 1
    return skipped


# -- moving -------------------------------------------------------------------------


def _move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError:
        # Another filesystem (a library outside data/): copy, then remove.
        shutil.move(str(source), str(target))


def move_to_trash(
    db_path: Path, originals: dict[int, Path | None], now: datetime | None = None
) -> list[int]:
    """Trash the given catalogue ids; ``originals`` maps each to its file, if any.

    Per item, the catalogue change and the file move succeed or fail together:
    the rows are deleted inside a transaction that is committed only after the
    file is in the trash folder, and the move is undone if the commit fails.
    """
    stamp = (now or datetime.now(UTC)).isoformat()
    root = trash_root(db_path)
    moved: list[int] = []
    connection = _connect(db_path)
    try:
        for meme_id, original in originals.items():
            found = connection.execute("SELECT * FROM memes WHERE id = ?", (meme_id,)).fetchone()
            if found is None:
                continue
            # Older catalogues lack some columns; absent reads as empty.
            row = {key: found[key] for key in ("arquivo", "content_hash", "file_size")
                   if key in found.keys()}
            row.setdefault("content_hash", None)
            row.setdefault("file_size", None)
            has_file = original is not None and original.is_file()
            target = root / str(meme_id) / (original.name if original else str(row["arquivo"]))
            connection.execute("BEGIN IMMEDIATE")
            try:
                captured = _snapshot(connection, "memes", "id", meme_id)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO library_trash
                        (meme_id, name, content_hash, size_bytes, original_path,
                         trash_path, trashed_at, snapshot)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meme_id, str(row["arquivo"] or target.name), row["content_hash"],
                        original.stat().st_size if has_file else row["file_size"],
                        str(original) if original else None,
                        str(target) if has_file else None,
                        stamp, json.dumps(captured),
                    ),
                )
                if row["content_hash"]:
                    # So an import of the same bytes is not silently accepted
                    # back while the user considers it deleted.
                    register_deleted_hashes(connection, [str(row["content_hash"])])
                connection.execute("DELETE FROM memes WHERE id = ?", (meme_id,))
                if has_file:
                    _move(original, target)
                try:
                    connection.execute("COMMIT")
                except BaseException:
                    if has_file:
                        _move(target, original)
                    raise
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            moved.append(meme_id)
    finally:
        connection.close()
    return moved


def restore(db_path: Path, meme_ids: list[int]) -> tuple[list[tuple[int, str | None]], int]:
    """Bring items back where they were.

    Returns ``(id, content hash)`` of each restored item, and how many
    dependent rows could not return (their other parent was deleted).
    """
    restored: list[tuple[int, str | None]] = []
    skipped = 0
    connection = _connect(db_path)
    try:
        for meme_id in meme_ids:
            entry = connection.execute(
                "SELECT * FROM library_trash WHERE meme_id = ?", (meme_id,)
            ).fetchone()
            if entry is None:
                raise TrashItemNotFound
            original = Path(entry["original_path"]) if entry["original_path"] else None
            held = Path(entry["trash_path"]) if entry["trash_path"] else None
            if held is not None and original is not None and original.exists():
                raise TrashRestoreConflict(str(original))
            connection.execute("BEGIN IMMEDIATE")
            try:
                skipped += _reinsert(connection, json.loads(entry["snapshot"]))
                connection.execute("DELETE FROM library_trash WHERE meme_id = ?", (meme_id,))
                if entry["content_hash"]:
                    connection.execute(
                        "DELETE FROM deleted_media WHERE content_hash = ?", (entry["content_hash"],)
                    )
                if held is not None and original is not None:
                    _move(held, original)
                try:
                    connection.execute("COMMIT")
                except BaseException:
                    if held is not None and original is not None:
                        _move(original, held)
                    raise
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            restored.append((meme_id, entry["content_hash"]))
            if held is not None:
                _remove_empty(held.parent)
    finally:
        connection.close()
    return restored, skipped


def _remove_empty(directory: Path) -> None:
    try:
        directory.rmdir()  # only when empty: the item's own folder
    except OSError:
        pass


def purge_after(trashed_at: str, days: int) -> str:
    return (datetime.fromisoformat(trashed_at) + timedelta(days=days)).isoformat()


def list_trash(
    db_path: Path, days: int, limit: int = 60, before: int | None = None
) -> list[TrashedItem]:
    """Newest first; ``before`` is the last id of the previous page."""
    if not db_path.exists():
        return []
    limit = max(1, min(int(limit), MAX_PAGE))
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT meme_id, name, size_bytes, trashed_at, trash_path FROM library_trash
            WHERE (? IS NULL OR meme_id < ?)
            ORDER BY trashed_at DESC, meme_id DESC LIMIT ?
            """,
            (before, before, limit),
        ).fetchall()
    finally:
        connection.close()
    return [
        TrashedItem(
            id=int(row["meme_id"]),
            name=str(row["name"]),
            size_bytes=row["size_bytes"],
            trashed_at=str(row["trashed_at"]),
            purge_after=purge_after(str(row["trashed_at"]), days),
            has_file=bool(row["trash_path"]) and Path(row["trash_path"]).is_file(),
        )
        for row in rows
    ]


def trashed_file(db_path: Path, meme_id: int) -> Path:
    """The held original, for previews in the trash view."""
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT trash_path FROM library_trash WHERE meme_id = ?", (meme_id,)
        ).fetchone()
    finally:
        connection.close()
    if row is None or not row["trash_path"] or not Path(row["trash_path"]).is_file():
        raise TrashItemNotFound
    return Path(row["trash_path"])


def purge_expired(db_path: Path, days: int, now: datetime | None = None) -> int:
    """Definitively delete items trashed more than ``days`` ago."""
    if not db_path.exists():
        return 0
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=days)).isoformat()
    connection = _connect(db_path)
    try:
        # A plain read first: this runs for every library on a timer.
        due = connection.execute(
            "SELECT meme_id, trash_path FROM library_trash WHERE trashed_at < ?", (cutoff,)
        ).fetchall()
        if not due:
            return 0
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "DELETE FROM library_trash WHERE meme_id = ?", [(row["meme_id"],) for row in due]
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    # After the commit: a crash here leaves an orphan file, never a trash
    # entry pointing at nothing.
    for row in due:
        if row["trash_path"]:
            held = Path(row["trash_path"])
            held.unlink(missing_ok=True)
            _remove_empty(held.parent)
    return len(due)

