"""Serialize post-upload indexing without coupling sync routes to server globals."""
from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from pathlib import Path

from core.sync_db import append_change, ensure_tables, now_iso, record_origin

_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


def process_upload(*, db_path: Path, media_root: Path, model_name: str, upload_id: str, file_path: Path, on_finished) -> None:
    """Index one accepted upload and emit its terminal sync state.

    Work is deliberately serialized per library because FAISS rebuilds and SQLite
    writes are not safe to run concurrently for the same account.
    """
    lock = _locks[str(db_path.resolve())]
    with lock:
        conn = sqlite3.connect(db_path)
        ensure_tables(conn)
        conn.execute("UPDATE sync_uploads SET state = 'processing', updated_at = ? WHERE id = ?", (now_iso(), upload_id))
        append_change(conn, "media", upload_id, "updated", 2, {"upload_id": upload_id, "state": "processing"})
        conn.commit()
        conn.close()
        try:
            from core.indexer import (
                IndexerConfig,
                create_faiss_indices,
                process_images,
                resolve_device,
            )

            config = IndexerConfig(
                media_dir=file_path.parent, db_path=db_path, model_name=model_name,
                batch_size=1, device=resolve_device("auto"), recursive=False, limit=None,
                rebuild_faiss_only=False, caption_model="none", whisper_model="none",
                sample_manifest=None, library_name="media", library_root=media_root,
                copy_to_library=False, extract_faces=True,
            )
            process_images(config, explicit_files=[file_path], dedup_enabled=False)
            create_faiss_indices(db_path, model_name)
            state, error = "ready", ""
        except Exception as exc:
            state, error = "failed_processing", str(exc)[:500]
        conn = sqlite3.connect(db_path)
        ensure_tables(conn)
        conn.execute("UPDATE sync_uploads SET state = ?, updated_at = ? WHERE id = ?", (state, now_iso(), upload_id))
        media_id = None
        if state == "ready":
            upload = conn.execute(
                """SELECT device_id, expected_hash, source_id, source_name,
                          source_relative_path, source_volume, source_media_store_id,
                          source_generation, source_media_kind
                   FROM sync_uploads WHERE id = ?""",
                (upload_id,),
            ).fetchone()
            if upload is not None:
                media = conn.execute(
                    "SELECT id FROM memes WHERE content_hash = ? ORDER BY id DESC LIMIT 1",
                    (upload[1],),
                ).fetchone()
                if media is not None:
                    media_id = int(media[0])
                    record_origin(conn, media_id, upload[0], {
                        "id": upload[2], "name": upload[3], "relative_path": upload[4],
                        "volume": upload[5], "media_store_id": upload[6],
                        "generation": upload[7], "media_kind": upload[8],
                    })
        append_change(conn, "media", upload_id, "updated", 3, {
            "upload_id": upload_id, "media_id": media_id, "state": state, "error": error,
        })
        conn.commit()
        conn.close()
        on_finished()
