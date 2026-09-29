"""Register uploaded media in a private library without running AI models."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from core.sync_db import append_change, now_iso, record_origin


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
    media_root = media_root.resolve()
    file_path = file_path.resolve()
    storage_path = file_path.relative_to(media_root).as_posix()
    stat = file_path.stat()

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    delete_duplicate_original = False
    try:
        with connection:
            upload = connection.execute(
                """SELECT filename, expected_hash, captured_at, device_id,
                          source_id, source_name, source_relative_path, source_volume,
                          source_media_store_id, source_generation, source_media_kind
                   FROM sync_uploads WHERE id = ? AND processing_lease_token = ?""",
                (upload_id, processing_lease_token),
            ).fetchone()
            if upload is None:
                raise RuntimeError("Processing lease was lost before catalog commit")

            duplicate = connection.execute(
                "SELECT id FROM memes WHERE content_hash = ? LIMIT 1",
                (upload["expected_hash"],),
            ).fetchone()
            if duplicate is not None:
                media_id = int(duplicate["id"])
                record_origin(connection, media_id, upload["device_id"], _source(upload))
                changed = connection.execute(
                    """UPDATE sync_uploads SET state = 'duplicate', updated_at = ?
                       WHERE id = ? AND processing_lease_token = ?""",
                    (now_iso(), upload_id, processing_lease_token),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("Processing lease was lost before catalog commit")
                sequence = append_change(
                    connection,
                    "media",
                    upload_id,
                    "updated",
                    3,
                    {"upload_id": upload_id, "media_id": media_id, "state": "duplicate"},
                )
                state = "duplicate"
                delete_duplicate_original = True
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
                        stat.st_mtime,
                        upload["expected_hash"],
                        metadata,
                        now_iso(),
                    ),
                )
                media_id = int(cursor.lastrowid)
                record_origin(connection, media_id, upload["device_id"], _source(upload))
                changed = connection.execute(
                    """UPDATE sync_uploads SET state = 'ready', updated_at = ?
                       WHERE id = ? AND processing_lease_token = ?""",
                    (now_iso(), upload_id, processing_lease_token),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("Processing lease was lost before catalog commit")
                sequence = append_change(
                    connection,
                    "media",
                    upload_id,
                    "updated",
                    3,
                    {"upload_id": upload_id, "media_id": media_id, "state": "ready"},
                )
                state = "ready"
    finally:
        connection.close()

    if delete_duplicate_original:
        file_path.unlink(missing_ok=True)

    if on_finished is not None:
        on_finished()
    return {
        "upload_id": upload_id,
        "media_id": media_id,
        "state": state,
        "cursor": sequence,
        "path": str(file_path),
    }


def _source(upload: sqlite3.Row) -> dict[str, str | int]:
    return {
        "id": upload["source_id"],
        "name": upload["source_name"],
        "relative_path": upload["source_relative_path"],
        "volume": upload["source_volume"],
        "media_store_id": upload["source_media_store_id"],
        "generation": upload["source_generation"],
        "media_kind": upload["source_media_kind"],
    }


def _media_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".3gp"}:
        return "video"
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".gif"}:
        return "image"
    return "file"
