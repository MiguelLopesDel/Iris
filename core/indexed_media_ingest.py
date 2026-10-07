"""Index one accepted upload and commit its catalog state under the active lease."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from core.media_dates import capture_timestamp
from core.upload_catalog_writer import UploadCatalogWriter

if TYPE_CHECKING:
    from core.sqlite_write_registry import SQLiteWriteCoordinatorRegistry


def ingest_upload_with_ai(
    *,
    db_path: Path,
    media_root: Path,
    model_name: str,
    upload_id: str,
    processing_lease_token: str,
    file_path: Path,
    on_finished: Callable[[], None] | None = None,
    write_registry: SQLiteWriteCoordinatorRegistry | None = None,
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

    def catalog_upload(connection: sqlite3.Connection) -> None:
        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            catalog = UploadCatalogWriter(connection)
            upload = connection.execute(
                """SELECT device_id, expected_hash, captured_at, source_id, source_name,
                          source_relative_path, source_volume, source_media_store_id,
                          source_generation, source_media_kind
                   FROM sync_uploads WHERE id = ? AND processing_lease_token = ?""",
                (upload_id, processing_lease_token),
            ).fetchone()
            if upload is None:
                raise RuntimeError("Processing lease was lost before catalog commit")
            media_id = catalog.find_media_id_by_hash(
                upload["expected_hash"], latest_first=True
            )
            # The indexer dated the item by the received file's mtime, which is
            # when it arrived; the gallery dates media by when it was taken.
            captured = capture_timestamp(upload["captured_at"])
            if media_id is not None and captured is not None:
                connection.execute(
                    "UPDATE memes SET file_mtime = ? WHERE id = ?", (captured, media_id)
                )
            catalog.complete_upload(
                upload=upload,
                upload_id=upload_id,
                lease_token=processing_lease_token,
                media_id=media_id,
            )
        finally:
            # The coordinator connection is shared by callbacks for this DB.
            connection.row_factory = previous_row_factory

    if write_registry is not None:
        # Successful futures resolve only after the writer's FULL commit.
        write_registry.submit(db_path, catalog_upload).result()
    else:
        connection = sqlite3.connect(db_path)
        try:
            with connection:
                catalog_upload(connection)
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
        return UploadCatalogWriter(connection).latest_change_cursor(upload_id)
    finally:
        connection.close()
