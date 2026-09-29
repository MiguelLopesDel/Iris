"""Crash-safe shared steps for completing a device media upload."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.sync_db import append_change, now_iso
from core.sync_file_ops import durable_move_upload


class UnsafeUploadDestinationError(ValueError):
    """A persisted destination escapes the authenticated account library."""


def move_upload_into_library(
    temporary: Path,
    destination: Path,
    *,
    media_root: Path,
    upload_id: str,
    expected_size: int,
    expected_hash: str,
) -> None:
    """Durably move a reserved original, refusing destinations outside its account library."""
    try:
        destination.resolve().relative_to(media_root.resolve())
    except ValueError as exc:
        raise UnsafeUploadDestinationError(
            "upload destination is outside the account media root"
        ) from exc
    durable_move_upload(
        temporary,
        destination,
        upload_id=upload_id,
        expected_size=expected_size,
        expected_hash=expected_hash,
    )


def record_upload_finalized(
    conn: sqlite3.Connection,
    *,
    upload_id: str,
    filename: str,
    destination: Path,
    captured_at: str,
) -> int | None:
    """Commit the finalizing→pending transition and its change-feed event exactly once."""
    changed = conn.execute(
        "UPDATE sync_uploads SET state = 'pending_processing', updated_at = ? "
        "WHERE id = ? AND state = 'finalizing'",
        (now_iso(), upload_id),
    )
    if changed.rowcount != 1:
        conn.commit()
        return None

    sequence = append_change(conn, "media", upload_id, "created", 1, {
        "upload_id": upload_id,
        "filename": filename,
        "path": str(destination),
        "captured_at": captured_at,
        "state": "pending_processing",
    })
    conn.commit()
    return sequence
