"""Serialize post-upload indexing without coupling sync routes to server globals."""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.sync_db import append_change, now_iso, record_origin
from core.sync_file_ops import durable_move_upload

_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_logger = logging.getLogger("iris.sync")
_PROCESSING_LOCK_NAME = "library-processing"
_PROCESSING_LEASE_SECONDS = 90
_PROCESSING_HEARTBEAT_SECONDS = 20
_PROCESSING_SCAN_INTERVAL_SECONDS = 15
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
    lock = _locks[str(db_path.resolve())]
    with lock:
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
            conn = sqlite3.connect(db_path)
            with conn:
                upload = conn.execute(
                    """SELECT device_id, expected_hash, source_id, source_name,
                              source_relative_path, source_volume, source_media_store_id,
                              source_generation, source_media_kind
                       FROM sync_uploads WHERE id = ? AND processing_lease_token = ?""",
                    (upload_id, token),
                ).fetchone()
                media_id = None
                if upload is None:
                    raise RuntimeError("Processing lease was lost before catalog commit")
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
                changed = conn.execute(
                    """UPDATE sync_uploads SET state = 'ready', updated_at = ?
                       WHERE id = ? AND processing_lease_token = ?""",
                    (now_iso(), upload_id, token),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("Processing lease was lost before catalog commit")
                append_change(conn, "media", upload_id, "updated", 3, {
                    "upload_id": upload_id, "media_id": media_id, "state": "ready",
                })
            _notify_finished(on_finished, upload_id)
            return {
                "upload_id": upload_id,
                "state": "ready",
                "cursor": _latest_change_cursor(db_path, upload_id),
            }
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
    return (datetime.now(timezone.utc) + timedelta(seconds=_PROCESSING_LEASE_SECONDS)).isoformat()


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
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
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


def _latest_change_cursor(db_path: Path, upload_id: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(sequence) FROM sync_changes WHERE entity_type = 'media' AND entity_id = ?",
            (upload_id,),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def _notify_finished(callback, upload_id: str) -> None:
    try:
        callback()
    except Exception as exc:
        _logger.warning(
            "sync_processing_cache_invalidation_failed upload_id=%s error_type=%s",
            upload_id,
            type(exc).__name__,
        )


def recover_pending_uploads(
    *,
    users_db_path: Path,
    sync_ai_processing: bool,
    load_model: bool,
    on_finished,
    stop_event: threading.Event,
    users=None,
) -> None:
    """Resume durable upload catalog work left pending by a server restart.

    `sync_uploads` is the source of truth: processing state is persisted before
    work begins, so both pending and interrupted processing rows are safe to
    rediscover. A single recovery worker serializes accounts to avoid placing
    model work from multiple libraries on a weak self-hosted host at once.
    """
    if users is None:
        from core.users_db import list_users

        users = list_users(users_db_path)

    use_ai = bool(sync_ai_processing and load_model)
    for user in users:
        if stop_event.is_set():
            return
        try:
            conn = sqlite3.connect(user.db_path, timeout=30)
            has_uploads = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_uploads'"
            ).fetchone()
            if has_uploads is None:
                conn.close()
                continue
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(sync_uploads)")
            }
            if not {"final_path", "processing_next_attempt_at"}.issubset(columns):
                conn.close()
                continue
            jobs = conn.execute(
                """SELECT id, state, temp_path, final_path FROM sync_uploads
                   WHERE state = 'finalizing'
                      OR state = 'processing'
                      OR (state = 'pending_processing'
                          AND (processing_next_attempt_at = '' OR processing_next_attempt_at <= ?))
                   ORDER BY created_at, id""",
                (now_iso(),),
            ).fetchall()
            conn.close()
        except Exception as exc:
            _logger.warning(
                "sync_recovery_library_failed user_id=%s error_type=%s",
                user.id,
                type(exc).__name__,
            )
            continue

        for upload_id, state, temp_path, final_path in jobs:
            if stop_event.is_set():
                return
            if state == "finalizing":
                candidate = _recover_finalizing_upload(user, str(upload_id))
                if candidate is None:
                    continue
            else:
                candidate = Path(final_path) if final_path else Path(temp_path)
            if not candidate.is_file():
                _mark_recovery_failure(
                    user.db_path,
                    str(upload_id),
                    "The accepted original is missing from server storage; recovery is required.",
                )
                _logger.error(
                    "sync_recovery_media_missing user_id=%s upload_id=%s state=%s",
                    user.id,
                    upload_id,
                    state,
                )
                continue
            try:
                process_upload(
                    db_path=user.db_path,
                    media_root=user.media_root,
                    model_name=user.model_name,
                    upload_id=str(upload_id),
                    file_path=candidate,
                    on_finished=lambda user_id=user.id: on_finished(user_id),
                    use_ai=use_ai,
                )
            except Exception as exc:
                _logger.error(
                    "sync_recovery_job_failed user_id=%s upload_id=%s error_type=%s",
                    user.id,
                    upload_id,
                    type(exc).__name__,
                )


def _mark_recovery_failure(db_path: Path, upload_id: str, safe_error: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        state = conn.execute(
            "SELECT state FROM sync_uploads WHERE id = ?", (upload_id,)
        ).fetchone()
        if state is None or state[0] not in {"pending_processing", "processing"}:
            return
        conn.execute(
            "UPDATE sync_uploads SET state = 'failed_processing', updated_at = ? WHERE id = ?",
            (now_iso(), upload_id),
        )
        append_change(conn, "media", upload_id, "updated", 3, {
            "upload_id": upload_id,
            "state": "failed_processing",
            "error": safe_error,
        })
        conn.commit()
    finally:
        conn.close()


def _recover_finalizing_upload(user, upload_id: str) -> Path | None:
    """Finish a persisted file move intent before resuming catalog work."""
    lock = _locks[str(user.db_path.resolve())]
    with lock:
        conn = sqlite3.connect(user.db_path)
        row = conn.execute(
            """SELECT filename, expected_size, expected_hash, temp_path, final_path,
                      captured_at, state FROM sync_uploads WHERE id = ?""",
            (upload_id,),
        ).fetchone()
        if row is None or row[6] != "finalizing" or not row[4]:
            conn.close()
            return None
        destination = Path(row[4])
        media_root = user.media_root.resolve()
        try:
            destination.resolve().relative_to(media_root)
        except ValueError:
            conn.close()
            _logger.error(
                "sync_recovery_destination_invalid user_id=%s upload_id=%s",
                user.id,
                upload_id,
            )
            return None

        temporary = Path(row[3])
        try:
            durable_move_upload(
                temporary,
                destination,
                upload_id=upload_id,
                expected_size=int(row[1]),
                expected_hash=str(row[2]),
            )
        except (OSError, ValueError) as exc:
            conn.close()
            _logger.error(
                "sync_recovery_finalize_failed user_id=%s upload_id=%s error_type=%s",
                user.id,
                upload_id,
                type(exc).__name__,
            )
            return None

        changed = conn.execute(
            "UPDATE sync_uploads SET state = 'pending_processing', updated_at = ? "
            "WHERE id = ? AND state = 'finalizing'",
            (now_iso(), upload_id),
        )
        if changed.rowcount == 1:
            append_change(conn, "media", upload_id, "created", 1, {
                "upload_id": upload_id,
                "filename": row[0],
                "path": str(destination),
                "captured_at": row[5],
                "state": "pending_processing",
            })
        conn.commit()
        conn.close()
        return destination if changed.rowcount == 1 else None


def start_pending_upload_recovery(
    *,
    users_db_path: Path,
    sync_ai_processing: bool,
    load_model: bool,
    on_finished,
) -> tuple[threading.Event, threading.Thread]:
    """Start one startup reconciliation thread and return its stop handle."""
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_run_pending_upload_recovery,
        kwargs={
            "users_db_path": users_db_path,
            "sync_ai_processing": sync_ai_processing,
            "load_model": load_model,
            "on_finished": on_finished,
            "stop_event": stop_event,
        },
        name="iris-sync-recovery",
        daemon=True,
    )
    worker.start()
    return stop_event, worker


def _run_pending_upload_recovery(
    *,
    users_db_path: Path,
    sync_ai_processing: bool,
    load_model: bool,
    on_finished,
    stop_event: threading.Event,
) -> None:
    from core.users_db import list_users

    try:
        # Account definitions do not change the set of recovery jobs for an
        # existing process: accepted uploads are processed inline and a restart
        # refreshes this snapshot. Avoid rerunning users-db migrations every
        # polling interval alongside authentication traffic.
        users = list_users(users_db_path)
    except Exception as exc:
        _logger.error("sync_recovery_users_failed error_type=%s", type(exc).__name__)
        users = None
    while not stop_event.is_set():
        recover_pending_uploads(
            users_db_path=users_db_path,
            sync_ai_processing=sync_ai_processing,
            load_model=load_model,
            on_finished=on_finished,
            stop_event=stop_event,
            users=users,
        )
        stop_event.wait(_PROCESSING_SCAN_INTERVAL_SECONDS)


def rebuild_indexes(*, db_path: Path, model_name: str, on_finished) -> None:
    """Rebuild a library's FAISS indexes after rows left or returned.

    Serialised with upload processing through the same per-library lock: both
    rewrite the same index files. Until it finishes the engine searches
    exactly, because its indexes no longer match the catalogue's size.
    """
    with _locks[str(db_path.resolve())]:
        try:
            from core.indexer import create_faiss_indices

            create_faiss_indices(db_path, model_name)
        finally:
            on_finished()


def rebuild_indexes_in_background(*, db_path: Path, model_name: str, on_finished) -> None:
    threading.Thread(
        target=rebuild_indexes,
        kwargs={"db_path": db_path, "model_name": model_name, "on_finished": on_finished},
        name="iris-reindex",
        daemon=True,
    ).start()
