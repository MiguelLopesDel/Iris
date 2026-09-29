"""Catalog persistence shared by AI and non-AI device-upload ingestion."""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from core.sync_db import record_origin
from core.upload_processing_state import record_upload_processing_result
from core.upload_source import source_from_upload_row


class UploadCatalogWriter:
    """Read and complete upload-related catalog records on a caller's transaction.

    The caller owns the SQLite connection and transaction. Upload completion
    writes its origin, state transition, and change-feed event atomically.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def find_media_id_by_hash(
        self,
        content_hash: str,
        *,
        latest_first: bool = False,
    ) -> int | None:
        ordering = " ORDER BY id DESC" if latest_first else ""
        row = self._connection.execute(
            f"SELECT id FROM memes WHERE content_hash = ?{ordering} LIMIT 1",
            (content_hash,),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def complete_upload(
        self,
        *,
        upload: Mapping[str, object] | sqlite3.Row,
        upload_id: str,
        lease_token: str,
        media_id: int | None,
        state: str = "ready",
    ) -> int:
        if media_id is not None:
            record_origin(
                self._connection,
                media_id,
                str(upload["device_id"]),
                source_from_upload_row(upload),
            )
        return record_upload_processing_result(
            self._connection,
            upload_id=upload_id,
            lease_token=lease_token,
            media_id=media_id,
            state=state,
        )

    def latest_change_cursor(self, upload_id: str) -> int:
        row = self._connection.execute(
            "SELECT MAX(sequence) FROM sync_changes "
            "WHERE entity_type = 'media' AND entity_id = ?",
            (upload_id,),
        ).fetchone()
        return int(row[0] or 0)
