"""Register uploaded media in a private library without running AI models."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from core.media_dates import capture_timestamp
from core.sync_db import now_iso
from core.sync_durability import connect_deferred
from core.upload_catalog_writer import UploadCatalogWriter


def ingest_upload_without_ai(
    *,
    db_path: Path,
    media_root: Path,
    upload_id: str,
    processing_lease_token: str,
    file_path: Path,
    on_finished: Callable[[], None] | None = None,
) -> dict[str, int | str]:
    """Make an accepted file a normal gallery item without computing vectors.

    Upload integrity and deduplication are checked before this point. This step
    only adds the stored original and source metadata to the account catalog;
    thumbnails remain on-demand, and both embedding columns stay NULL.
    """
    return ingest_uploads_without_ai(
        db_path=db_path,
        media_root=media_root,
        processing_lease_token=processing_lease_token,
        uploads=[(upload_id, file_path)],
        on_finished=(lambda _upload_id: on_finished()) if on_finished else None,
    )[upload_id]


def ingest_uploads_without_ai(
    *,
    db_path: Path,
    media_root: Path,
    processing_lease_token: str,
    uploads: list[tuple[str, Path]],
    on_finished: Callable[[str], None] | None = None,
) -> dict[str, dict[str, int | str]]:
    """Catalog several uploads under one lease in one transaction.

    Either all of them are cataloged or, on any error, none is.
    """
    media_root = media_root.resolve()
    resolved = [(upload_id, file_path.resolve()) for upload_id, file_path in uploads]
    results: dict[str, dict[str, int | str]] = {}
    duplicates: list[Path] = []
    connection = connect_deferred(db_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            catalog = UploadCatalogWriter(connection)
            for upload_id, file_path in resolved:
                results[upload_id] = _ingest_one(
                    connection, catalog, media_root, upload_id, processing_lease_token, file_path,
                )
                if results[upload_id]["state"] == "duplicate":
                    duplicates.append(file_path)
    finally:
        connection.close()

    for file_path in duplicates:
        file_path.unlink(missing_ok=True)
    if on_finished is not None:
        for upload_id, _file_path in resolved:
            on_finished(upload_id)
    return results


def _ingest_one(
    connection: sqlite3.Connection,
    catalog: UploadCatalogWriter,
    media_root: Path,
    upload_id: str,
    processing_lease_token: str,
    file_path: Path,
) -> dict[str, int | str]:
    """Write one upload's catalog entry inside the caller's transaction."""
    storage_path = file_path.relative_to(media_root).as_posix()
    stat = file_path.stat()
    upload = connection.execute(
        """SELECT filename, expected_hash, captured_at, device_id,
                  source_id, source_name, source_relative_path, source_volume,
                  source_media_store_id, source_generation, source_media_kind
           FROM sync_uploads WHERE id = ? AND processing_lease_token = ?""",
        (upload_id, processing_lease_token),
    ).fetchone()
    if upload is None:
        raise RuntimeError("Processing lease was lost before catalog commit")

    media_id = catalog.find_media_id_by_hash(upload["expected_hash"])
    if media_id is not None:
        sequence = catalog.complete_upload(
            upload=upload,
            upload_id=upload_id,
            lease_token=processing_lease_token,
            media_id=media_id,
            state="duplicate",
        )
        state = "duplicate"
    else:
        library = connection.execute(
            "SELECT id FROM media_libraries WHERE name = 'device-uploads'"
        ).fetchone()
        if library is None:
            cursor = connection.execute(
                "INSERT INTO media_libraries (name, root_path, created_at) VALUES (?, ?, ?)",
                ("device-uploads", str(media_root), now_iso()),
            )
            library_id = int(cursor.lastrowid)
        else:
            library_id = int(library["id"])
            connection.execute(
                "UPDATE media_libraries SET root_path = ? WHERE id = ?",
                (str(media_root), library_id),
            )

        metadata = json.dumps(
            {
                "kind": _media_kind(file_path),
                "captured_at": upload["captured_at"],
                "source_name": upload["source_name"],
                "source_relative_path": upload["source_relative_path"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        cursor = connection.execute(
            """INSERT INTO memes (
                   arquivo, caminho, relative_path, storage_path, library_id,
                   imported_at, file_size, file_mtime, content_hash,
                   model_name, embedding_dim, schema_version, embedding,
                   desc_embedding, metadata_json, created_at, thumb_hash
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, 0, NULL, NULL, ?, ?, '')""",
            (
                upload["filename"],
                str(file_path),
                storage_path,
                storage_path,
                library_id,
                now_iso(),
                stat.st_size,
                # The stored copy's mtime is when it arrived; the gallery dates
                # media by when it was taken, as the device reported it.
                capture_timestamp(upload["captured_at"]) or stat.st_mtime,
                upload["expected_hash"],
                metadata,
                now_iso(),
            ),
        )
        media_id = int(cursor.lastrowid)
        sequence = catalog.complete_upload(
            upload=upload,
            upload_id=upload_id,
            lease_token=processing_lease_token,
            media_id=media_id,
        )
        state = "ready"
    return {
        "upload_id": upload_id,
        "media_id": media_id,
        "state": state,
        "cursor": sequence,
        "path": str(file_path),
    }


def _media_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".3gp"}:
        return "video"
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".gif"}:
        return "image"
    return "file"
