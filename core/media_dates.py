"""The date a media item is shown and sorted by.

For media uploaded from a device that is when it was taken, as the device
reported it, not when the server received the file: the stored copy's mtime
is the upload time, and using it put every photo of a first backup on the
day of the backup.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

# Anything earlier is a device clock or EXIF default, not a capture date.
_EARLIEST = datetime(1990, 1, 1, tzinfo=UTC).timestamp()
_MAX_FUTURE_SECONDS = 24 * 60 * 60


def capture_timestamp(captured_at: str | None, *, now: float | None = None) -> float | None:
    """Epoch seconds for an ISO 8601 capture time, or None when absent or implausible."""
    if not captured_at:
        return None
    try:
        parsed = datetime.fromisoformat(captured_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    stamp = parsed.timestamp()
    limit = (now if now is not None else datetime.now(UTC).timestamp()) + _MAX_FUTURE_SECONDS
    return stamp if _EARLIEST <= stamp <= limit else None


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
    return cursor.rowcount
