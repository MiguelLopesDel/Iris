"""Index one accepted upload and commit its catalog state under the active lease."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from core.sync_db import record_origin
from core.upload_processing_state import record_upload_processing_result
from core.upload_source import source_from_upload_row


def ingest_upload_with_ai(
    *,
    db_path: Path,
    media_root: Path,
    model_name: str,
    upload_id: str,
    processing_lease_token: str,
    file_path: Path,
    on_finished: Callable[[], None] | None = None,
) -> dict[str, int | str]:
    """Run the existing indexer pipeline, then atomically record upload readiness.

    The processing coordinator owns the lease and retry lifecycle. This module
    owns the AI/indexer adapter and its catalog commit; indexer imports stay
    lazy so receiving uploads does not require loading model code.
    """
    from core.indexer import (
        IndexerConfig,
        create_faiss_indices,
        process_images,
        resolve_device,
    )

    config = IndexerConfig(
        media_dir=file_path.parent,
        db_path=db_path,
        model_name=model_name,
        batch_size=1,
        device=resolve_device("auto"),
        recursive=False,
        limit=None,
        rebuild_faiss_only=False,
        caption_model="none",
        whisper_model="none",
        sample_manifest=None,
        library_name="media",
        library_root=media_root,
        copy_to_library=False,
        extract_faces=True,
    )
    process_images(config, explicit_files=[file_path], dedup_enabled=False)
    create_faiss_indices(db_path, model_name)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            upload = connection.execute(
                """SELECT device_id, expected_hash, source_id, source_name,
                          source_relative_path, source_volume, source_media_store_id,
                          source_generation, source_media_kind
                   FROM sync_uploads WHERE id = ? AND processing_lease_token = ?""",
                (upload_id, processing_lease_token),
            ).fetchone()
            if upload is None:
                raise RuntimeError("Processing lease was lost before catalog commit")
            media = connection.execute(
                "SELECT id FROM memes WHERE content_hash = ? ORDER BY id DESC LIMIT 1",
                (upload["expected_hash"],),
            ).fetchone()
            media_id = int(media[0]) if media is not None else None
            if media_id is not None:
                record_origin(
                    connection,
                    media_id,
                    upload["device_id"],
                    source_from_upload_row(upload),
                )
            record_upload_processing_result(
                connection,
                upload_id=upload_id,
                lease_token=processing_lease_token,
                media_id=media_id,
            )
    finally:
        connection.close()

    if on_finished is not None:
        on_finished()
    return {
        "upload_id": upload_id,
        "state": "ready",
        "cursor": _latest_change_cursor(db_path, upload_id),
    }


def _latest_change_cursor(db_path: Path, upload_id: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT MAX(sequence) FROM sync_changes "
            "WHERE entity_type = 'media' AND entity_id = ?",
            (upload_id,),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        connection.close()
