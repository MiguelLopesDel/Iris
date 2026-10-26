"""The date a media item is shown and sorted by.

It is when the item was taken. For media uploaded from a device that is the
time the device reported, not when the server received the file: the stored
copy's mtime is the upload time, and using it put every photo of a first
backup on the day of the backup. For a file indexed from a folder it is the
capture time in its metadata (EXIF, video creation time), not the file's
mtime, which for a copied library is the copy day.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from core.media_metadata import VIDEO_EXTS

# Anything earlier is a device clock or EXIF default, not a capture date.
_EARLIEST = datetime(1990, 1, 1, tzinfo=UTC).timestamp()
_MAX_FUTURE_SECONDS = 24 * 60 * 60


def capture_timestamp(
    captured_at: str | None, *, now: float | None = None, naive_is_local: bool = False
) -> float | None:
    """Epoch seconds for an ISO 8601 capture time, or None when absent or implausible.

    A time without an offset is UTC unless ``naive_is_local``: a camera's EXIF
    time is the wall clock where the photo was taken, read here as the server's
    local time (``TZ``, which ``scripts/server.sh`` takes from the host).
    """
    if not captured_at:
        return None
    try:
        parsed = datetime.fromisoformat(captured_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone() if naive_is_local else parsed.replace(tzinfo=UTC)
    stamp = parsed.timestamp()
    limit = (now if now is not None else datetime.now(UTC).timestamp()) + _MAX_FUTURE_SECONDS
    return stamp if _EARLIEST <= stamp <= limit else None


# Records that a library's legacy dates were repaired. New uploads are dated
# right when they are registered, so the repair is needed once per database;
# running it on every open made each sync request scan the whole library.
_APPLIED_TABLE = "applied_repairs"
DEVICE_UPLOAD_DATES_REPAIR = "device_upload_dates_v1"
METADATA_CAPTURE_DATES_REPAIR = "metadata_capture_dates_v1"
# Further apart than any time zone offset: the row is dated by its file (a
# copy or import day), not by a different reading of the same capture time.
_FILE_DATE_GAP_SECONDS = 26 * 60 * 60


def repair_device_upload_dates_once(conn: sqlite3.Connection) -> int:
    """Runs :func:`repair_device_upload_dates` the first time a database is opened."""
    return _run_once(conn, DEVICE_UPLOAD_DATES_REPAIR, repair_device_upload_dates)


def repair_metadata_capture_dates_once(conn: sqlite3.Connection) -> int:
    """Runs :func:`repair_metadata_capture_dates` the first time a database is opened."""
    return _run_once(conn, METADATA_CAPTURE_DATES_REPAIR, repair_metadata_capture_dates)


def _run_once(conn: sqlite3.Connection, name: str, repair) -> int:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_APPLIED_TABLE} (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = conn.execute(f"SELECT 1 FROM {_APPLIED_TABLE} WHERE name = ?", (name,)).fetchone()
    if applied:
        return 0
    changed = repair(conn)
    # Two connections opening a library at once can both run the repair (it
    # is idempotent); the record must not fail for the second one.
    conn.execute(
        f"INSERT OR IGNORE INTO {_APPLIED_TABLE} (name, applied_at) VALUES (?, ?)",
        (name, datetime.now(UTC).isoformat()),
    )
    return changed


def metadata_capture_timestamp(metadata: dict, path: str) -> float | None:
    """When an indexed file was taken, from the metadata extracted from it.

    A photo carries the camera's wall clock (EXIF, no offset); a video's
    ``creation_time`` is UTC, with the offset dropped by the extractor.
    """
    return capture_timestamp(
        str(metadata.get("captured_at") or ""),
        naive_is_local=Path(path).suffix.lower() not in VIDEO_EXTS,
    )


def repair_metadata_capture_dates(conn: sqlite3.Connection) -> int:
    """Dates indexed files that were dated by their file by when they were taken.

    Before, a file imported from a folder kept its file time, which for a
    copied library is the copy day, although its metadata held the capture
    time. Only rows whose file time is more than a day away from the capture
    time change, so a capture time already applied (read with another offset,
    as for device uploads) is left as it is. Returns rows changed.
    """
    rows = conn.execute(
        """
        SELECT id, caminho, file_mtime, metadata_json FROM memes
        WHERE json_valid(metadata_json)
          AND COALESCE(json_extract(metadata_json, '$.captured_at'), '') != ''
        """
    ).fetchall()
    updates = []
    for media_id, path, file_mtime, metadata_json in rows:
        captured = metadata_capture_timestamp(json.loads(metadata_json), path or "")
        if captured is not None and abs((file_mtime or 0.0) - captured) > _FILE_DATE_GAP_SECONDS:
            updates.append((captured, media_id))
    conn.executemany("UPDATE memes SET file_mtime = ? WHERE id = ?", updates)
    return len(updates)


def repair_device_upload_dates(conn: sqlite3.Connection) -> int:
    """Gives device uploads dated by their arrival their capture date instead.

    Matches only rows still carrying the old mistake (their date within five
    minutes of their import time) whose metadata has a usable capture time,
    so it is idempotent and a no-op once repaired. Returns rows changed.
    """
    captured = "strftime('%s', json_extract(metadata_json, '$.captured_at'))"
    cursor = conn.execute(
        f"""
        UPDATE memes SET file_mtime = CAST({captured} AS REAL)
        WHERE library_id IN (SELECT id FROM media_libraries WHERE name = 'device-uploads')
          AND json_valid(metadata_json)
          AND {captured} IS NOT NULL
          AND CAST({captured} AS REAL) >= ?
          AND CAST({captured} AS REAL) <= CAST(strftime('%s', 'now') AS REAL) + ?
          AND imported_at IS NOT NULL
          AND ABS(file_mtime - CAST(strftime('%s', imported_at) AS REAL)) < 300
          AND ABS(file_mtime - CAST({captured} AS REAL)) >= 1
        """,
        (_EARLIEST, _MAX_FUTURE_SECONDS),
    )
    return cursor.rowcount + _repair_indexed_device_uploads(conn)


def _repair_indexed_device_uploads(conn: sqlite3.Connection) -> int:
    """The same repair for uploads indexed with AI enabled.

    Those land in the shared "media" library through the indexer, with no
    capture time in their metadata. Only the upload that created the item may
    date it: one in state "ready" whose stored file is the item's path
    (final_path = caminho). A device that later presented the same bytes
    (state "duplicate", no file of its own) proves nothing: the item may be a
    host import, which keeps its own date even though a phone's origin was
    recorded against it.
    """
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "sync_uploads" not in tables:
        return 0
    upload_columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_uploads)")}
    if "final_path" not in upload_columns:
        return 0
    captured = (
        "(SELECT CAST(strftime('%s', u.captured_at) AS REAL) FROM sync_uploads u "
        "WHERE u.state = 'ready' AND u.final_path = memes.caminho "
        "AND u.expected_hash = memes.content_hash AND strftime('%s', u.captured_at) IS NOT NULL "
        "ORDER BY u.updated_at DESC LIMIT 1)"
    )
    cursor = conn.execute(
        f"""
        UPDATE memes SET file_mtime = {captured}
        WHERE content_hash IS NOT NULL
          AND imported_at IS NOT NULL
          AND ABS(file_mtime - CAST(strftime('%s', imported_at) AS REAL)) < 300
          AND {captured} IS NOT NULL
          AND {captured} >= ?
          AND {captured} <= CAST(strftime('%s', 'now') AS REAL) + ?
          AND ABS(file_mtime - {captured}) >= 1
        """,
        (_EARLIEST, _MAX_FUTURE_SECONDS),
    )
    return cursor.rowcount
