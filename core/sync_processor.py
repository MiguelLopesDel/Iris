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


def process_uploads(
    *,
    db_path: Path,
    media_root: Path,
    model_name: str,
    uploads: list[tuple[str, Path]],
    on_finished,
) -> dict[str, dict[str, int | str] | None]:
    """Catalog several uploads without AI under one lease and one transaction.

    One upload at a time cost a lease claim, a catalog write and a release
    each, every one waiting for SQLite's single writer. If the batch fails as
    a whole, each upload is processed again on its own, which records its
    failure and schedules its retry exactly as before.
    """
    results: dict[str, dict[str, int | str] | None] = {}
    claimed: list[tuple[str, Path]] = []
    with serialize_library_operations(db_path):
        store = UploadProcessingStore(db_path)
        token = uuid.uuid4().hex
        claims = store.claim_many([upload_id for upload_id, _ in uploads], token)
        for upload_id, file_path in uploads:
            claimed_token, detail = claims[upload_id]
            if claimed_token is None:
                results[upload_id] = store.result(upload_id, str(detail))
            else:
                claimed.append((upload_id, file_path))
        if claimed:
            claimed_ids = [upload_id for upload_id, _ in claimed]
            heartbeat_stop = threading.Event()
            heartbeat = threading.Thread(
                target=store.renew_until_stopped,
                args=(claimed_ids, token, heartbeat_stop),
                name=f"iris-sync-lease-{claimed_ids[0][:8]}",
                daemon=True,
            )
            heartbeat.start()
            try:
                from core.media_ingest import ingest_uploads_without_ai

                results.update(ingest_uploads_without_ai(
                    db_path=db_path,
                    media_root=media_root,
                    processing_lease_token=token,
                    uploads=claimed,
                    on_finished=lambda upload_id: _notify_finished(on_finished, upload_id),
                ))
                claimed = []
            except Exception as exc:
                _logger.warning(
                    "sync_batch_processing_failed uploads=%s error_type=%s",
                    len(claimed),
                    type(exc).__name__,
                )
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=2)
                # Uploads left in ``claimed`` are retried one by one below;
                # this failed batch attempt does not count against them.
                store.release(claimed_ids, token, uncount_attempt=bool(claimed))
    # Outside the library lock, which each single attempt takes itself.
    for upload_id, file_path in claimed:
        results[upload_id] = process_upload(
            db_path=db_path, media_root=media_root, model_name=model_name,
            upload_id=upload_id, file_path=file_path, on_finished=on_finished, use_ai=False,
        )
    return results


def _notify_finished(callback, upload_id: str) -> None:
    try:
        callback()
    except Exception as exc:
        _logger.warning(
            "sync_processing_cache_invalidation_failed upload_id=%s error_type=%s",
            upload_id,
            type(exc).__name__,
        )
