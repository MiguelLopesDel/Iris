"""Serialize post-upload indexing without coupling sync routes to server globals."""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.library_operation_lock import serialize_library_operations
from core.sync_db import append_change, now_iso

_logger = logging.getLogger("iris.sync")
_PROCESSING_LOCK_NAME = "library-processing"
_PROCESSING_LEASE_SECONDS = 90
_PROCESSING_HEARTBEAT_SECONDS = 20
_PROCESSING_MAX_ATTEMPTS = 4
_PROCESSING_RETRY_DELAYS_SECONDS = (30, 120, 600)


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
        token = uuid.uuid4().hex
        claimed = _claim_processing_job(db_path, upload_id, token)
        if claimed[0] is None:
            return _upload_result(db_path, upload_id, claimed[1])
        attempts = claimed[1]
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=_renew_processing_lease,
            args=(db_path, upload_id, token, heartbeat_stop),
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
            return _record_processing_failure(
                db_path, upload_id, token, attempts, type(exc).__name__
            )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=2)
            _release_processing_lease(db_path, upload_id, token)


def _claim_processing_job(db_path: Path, upload_id: str, token: str) -> tuple[str | None, int | str]:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT state, processing_lease_until, processing_attempts,
                      processing_next_attempt_at
               FROM sync_uploads WHERE id = ?""",
            (upload_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None, "missing"
        state, lease_until, attempts, next_attempt = row
        if state in {"ready", "duplicate", "failed_processing"}:
            conn.commit()
            return None, str(state)
        now = now_iso()
        if state not in {"pending_processing", "processing"}:
            conn.commit()
            return None, str(state)
        if state == "pending_processing" and next_attempt and next_attempt > now:
            conn.commit()
            return None, str(state)
        if state == "processing" and lease_until and lease_until > now:
            conn.commit()
            return None, "processing"

        library_lease = conn.execute(
            "SELECT owner_token, lease_until FROM sync_processing_leases WHERE lock_name = ?",
            (_PROCESSING_LOCK_NAME,),
        ).fetchone()
        if library_lease and library_lease[1] > now and library_lease[0] != token:
            conn.commit()
            return None, "processing" if state == "processing" else "pending_processing"

        lease_until = _lease_deadline()
        conn.execute(
            """INSERT INTO sync_processing_leases (lock_name, owner_token, lease_until)
               VALUES (?, ?, ?)
               ON CONFLICT(lock_name) DO UPDATE SET
                   owner_token = excluded.owner_token, lease_until = excluded.lease_until""",
            (_PROCESSING_LOCK_NAME, token, lease_until),
        )
        conn.execute(
            """UPDATE sync_uploads SET state = 'processing', processing_lease_token = ?,
                      processing_lease_until = ?, processing_attempts = processing_attempts + 1,
                      processing_next_attempt_at = '', updated_at = ? WHERE id = ?""",
            (token, lease_until, now, upload_id),
        )
        attempts_row = conn.execute(
            "SELECT processing_attempts FROM sync_uploads WHERE id = ?", (upload_id,)
        ).fetchone()
        append_change(conn, "media", upload_id, "updated", 2, {
            "upload_id": upload_id,
            "state": "processing",
        })
        conn.commit()
        return token, int(attempts_row[0])
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _lease_deadline() -> str:
    return (datetime.now(UTC) + timedelta(seconds=_PROCESSING_LEASE_SECONDS)).isoformat()


def _renew_processing_lease(
    db_path: Path,
    upload_id: str,
    token: str,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(_PROCESSING_HEARTBEAT_SECONDS):
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute("BEGIN IMMEDIATE")
            lease_until = _lease_deadline()
            upload = conn.execute(
                """UPDATE sync_uploads SET processing_lease_until = ?
                   WHERE id = ? AND processing_lease_token = ?""",
                (lease_until, upload_id, token),
            )
            library = conn.execute(
                """UPDATE sync_processing_leases SET lease_until = ?
                   WHERE lock_name = ? AND owner_token = ?""",
                (lease_until, _PROCESSING_LOCK_NAME, token),
            )
            conn.commit()
            if upload.rowcount != 1 or library.rowcount != 1:
                stop_event.set()
                return
        except sqlite3.Error as exc:
            conn.rollback()
            _logger.warning(
                "sync_processing_lease_renewal_failed upload_id=%s error_type=%s",
                upload_id,
                type(exc).__name__,
            )
        finally:
            conn.close()


def _release_processing_lease(db_path: Path, upload_id: str, token: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE sync_uploads SET processing_lease_token = NULL,
                      processing_lease_until = '', updated_at = ?
               WHERE id = ? AND processing_lease_token = ?""",
            (now_iso(), upload_id, token),
        )
        conn.execute(
            "DELETE FROM sync_processing_leases WHERE lock_name = ? AND owner_token = ?",
            (_PROCESSING_LOCK_NAME, token),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        _logger.warning("sync_processing_lease_release_failed upload_id=%s", upload_id)
    finally:
        conn.close()


def _record_processing_failure(
    db_path: Path,
    upload_id: str,
    token: str,
    attempts: int,
    error_type: str,
) -> dict[str, int | str]:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        if attempts < _PROCESSING_MAX_ATTEMPTS:
            delay = _PROCESSING_RETRY_DELAYS_SECONDS[min(attempts - 1, len(_PROCESSING_RETRY_DELAYS_SECONDS) - 1)]
            retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            state = "pending_processing"
            error = f"Processing failed ({error_type}); retry scheduled."
        else:
            retry_at = ""
            state = "failed_processing"
            error = "Processing failed after automatic retries; check server logs."
        with conn:
            changed = conn.execute(
                """UPDATE sync_uploads SET state = ?, processing_next_attempt_at = ?,
                          updated_at = ?
                   WHERE id = ? AND processing_lease_token = ?""",
                (state, retry_at, now_iso(), upload_id, token),
            )
            if changed.rowcount == 1:
                append_change(conn, "media", upload_id, "updated", 3, {
                    "upload_id": upload_id,
                    "state": state,
                    "retry_at": retry_at,
                    "error": error,
                })
            else:
                row = conn.execute(
                    "SELECT state FROM sync_uploads WHERE id = ?", (upload_id,)
                ).fetchone()
                return {"upload_id": upload_id, "state": row[0] if row else "missing"}
        return {"upload_id": upload_id, "state": state}
    finally:
        conn.close()


def _upload_result(db_path: Path, upload_id: str, state: str) -> dict[str, int | str]:
    result: dict[str, int | str] = {"upload_id": upload_id, "state": state}
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT expected_hash FROM sync_uploads WHERE id = ?", (upload_id,)
        ).fetchone()
        if row is not None and state in {"ready", "duplicate"}:
            media = conn.execute(
                "SELECT id FROM memes WHERE content_hash = ? ORDER BY id DESC LIMIT 1",
                (row[0],),
            ).fetchone()
            cursor = conn.execute(
                "SELECT MAX(sequence) FROM sync_changes WHERE entity_type = 'media' AND entity_id = ?",
                (upload_id,),
            ).fetchone()
            result["cursor"] = int(cursor[0] or 0)
            if media is not None:
                result["media_id"] = int(media[0])
    finally:
        conn.close()
    return result


def _notify_finished(callback, upload_id: str) -> None:
    try:
        callback()
    except Exception as exc:
        _logger.warning(
            "sync_processing_cache_invalidation_failed upload_id=%s error_type=%s",
            upload_id,
            type(exc).__name__,
        )
