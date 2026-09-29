"""Coordinate durable upload processing without coupling routes to server state."""
from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

from core.library_operation_lock import serialize_library_operations
from core.upload_processing_store import UploadProcessingStore

_logger = logging.getLogger("iris.sync")


def process_upload(
    *,
    db_path: Path,
    media_root: Path,
    model_name: str,
    upload_id: str,
    file_path: Path,
    on_finished,
    use_ai: bool = True,
) -> dict[str, int | str] | None:
    """Claim and catalog one upload under a database-backed per-library lease."""
    with serialize_library_operations(db_path):
        store = UploadProcessingStore(db_path)
        token = uuid.uuid4().hex
        claimed = store.claim(upload_id, token)
        if claimed[0] is None:
            return store.result(upload_id, claimed[1])
        attempts = claimed[1]
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=store.renew_until_stopped,
            args=(upload_id, token, heartbeat_stop),
            name=f"iris-sync-lease-{upload_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            if not use_ai:
                from core.media_ingest import ingest_upload_without_ai

                return ingest_upload_without_ai(
                    db_path=db_path,
                    media_root=media_root,
                    upload_id=upload_id,
                    processing_lease_token=token,
                    file_path=file_path,
                    on_finished=lambda: _notify_finished(on_finished, upload_id),
                )
            from core.indexed_media_ingest import ingest_upload_with_ai

            return ingest_upload_with_ai(
                db_path=db_path,
                media_root=media_root,
                model_name=model_name,
                upload_id=upload_id,
                processing_lease_token=token,
                file_path=file_path,
                on_finished=lambda: _notify_finished(on_finished, upload_id),
            )
        except Exception as exc:
            _logger.error(
                "sync_processing_attempt_failed upload_id=%s attempt=%s error_type=%s",
                upload_id,
                attempts,
                type(exc).__name__,
            )
            return store.record_failure(upload_id, token, attempts, type(exc).__name__)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=2)
            store.release(upload_id, token)


def _notify_finished(callback, upload_id: str) -> None:
    try:
        callback()
    except Exception as exc:
        _logger.warning(
            "sync_processing_cache_invalidation_failed upload_id=%s error_type=%s",
            upload_id,
            type(exc).__name__,
        )
