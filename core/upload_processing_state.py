"""Persistence rules for committing a completed device-upload processing job."""
from __future__ import annotations

import sqlite3

from core.sync_db import append_change, now_iso

_TERMINAL_CATALOG_STATES = {"ready", "duplicate"}


def record_upload_processing_result(
    connection: sqlite3.Connection,
    *,
    upload_id: str,
    lease_token: str,
    media_id: int | None,
    state: str = "ready",
) -> int:
    """Commit a catalog result only for the worker holding the upload lease.

    The caller owns the surrounding database transaction. Updating the row and
    appending its change-feed event must remain in that same transaction.
    """
    if state not in _TERMINAL_CATALOG_STATES:
        raise ValueError(f"Unsupported catalog processing state: {state}")

    changed = connection.execute(
        "UPDATE sync_uploads SET state = ?, updated_at = ? "
        "WHERE id = ? AND processing_lease_token = ?",
        (state, now_iso(), upload_id, lease_token),
    )
    if changed.rowcount != 1:
        raise RuntimeError("Processing lease was lost before catalog commit")

    return append_change(
        connection,
        "media",
        upload_id,
        "updated",
        3,
        {"upload_id": upload_id, "media_id": media_id, "state": state},
    )
