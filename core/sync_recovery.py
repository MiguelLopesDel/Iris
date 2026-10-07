"""Find and resume accepted uploads left unfinished by interruptions."""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from core.library_operation_lock import serialize_library_operations
from core.sqlite_write_registry import SQLiteWriteCoordinatorRegistry
from core.sync_db import append_change, now_iso
from core.upload_finalization import move_upload_into_library, record_upload_finalized
from core.upload_processing_workers import UploadProcessingWorkers

_logger = logging.getLogger("iris.sync")
_RECOVERY_SCAN_INTERVAL_SECONDS = 15


def recover_pending_uploads(
    *,
    users_db_path: Path,
    sync_ai_processing: bool,
    load_model: bool,
    on_finished,
    stop_event: threading.Event,
    processing_workers: UploadProcessingWorkers,
    write_registry: SQLiteWriteCoordinatorRegistry | None = None,
    users=None,
) -> None:
    """Resume persisted finalization and catalog work for each account.

    ``sync_uploads`` is the source of truth. File finalization is idempotent,
    and catalog work is claimed through the same durable lease as live uploads.
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
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_uploads)")}
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
                candidate = _recover_finalizing_upload(user, str(upload_id), write_registry)
                if candidate is None:
                    continue
            else:
                candidate = Path(final_path) if final_path else Path(temp_path)
            if not candidate.is_file():
                _mark_recovery_failure(
                    user.db_path,
                    str(upload_id),
                    "The accepted original is missing from server storage; recovery is required.",
                    write_registry=write_registry,
                )
                _logger.error(
                    "sync_recovery_media_missing user_id=%s upload_id=%s state=%s",
                    user.id,
                    upload_id,
                    state,
                )
                continue
            try:
                processing_workers.submit(
                    user,
                    str(upload_id),
                    candidate,
                    use_ai=use_ai,
                    on_finished=on_finished,
                )
            except Exception as exc:
                _logger.error(
                    "sync_recovery_job_failed user_id=%s upload_id=%s error_type=%s",
                    user.id,
                    upload_id,
                    type(exc).__name__,
                )


def _mark_recovery_failure(
    db_path: Path,
    upload_id: str,
    safe_error: str,
    *,
    write_registry: SQLiteWriteCoordinatorRegistry | None = None,
) -> None:
    def mark(conn: sqlite3.Connection) -> None:
        state = conn.execute("SELECT state FROM sync_uploads WHERE id = ?", (upload_id,)).fetchone()
        if state is None or state[0] not in {"pending_processing", "processing"}:
            return
        conn.execute(
            "UPDATE sync_uploads SET state = 'failed_processing', updated_at = ? WHERE id = ?",
            (now_iso(), upload_id),
        )
        append_change(
            conn,
            "media",
            upload_id,
            "updated",
            3,
            {
                "upload_id": upload_id,
                "state": "failed_processing",
                "error": safe_error,
            },
        )

    if write_registry is not None:
        write_registry.submit(db_path, mark).result()
    else:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            mark(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def _recover_finalizing_upload(
    user,
    upload_id: str,
    write_registry: SQLiteWriteCoordinatorRegistry | None = None,
) -> Path | None:
    """Finish one persisted original move before handing it to the processor."""
    with serialize_library_operations(user.db_path):
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
        try:
            move_upload_into_library(
                Path(row[3]),
                destination,
                media_root=user.media_root,
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

        conn.close()

        def record(connection: sqlite3.Connection) -> int | None:
            return record_upload_finalized(
                connection,
                upload_id=upload_id,
                filename=row[0],
                destination=destination,
                captured_at=row[5],
                commit=False,
            )

        try:
            if write_registry is None:
                conn = sqlite3.connect(user.db_path)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    sequence = record(conn)
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            else:
                sequence = write_registry.submit(user.db_path, record).result()
        except sqlite3.Error:
            _logger.error(
                "sync_recovery_record_failed user_id=%s upload_id=%s",
                user.id,
                upload_id,
            )
            return None
        return destination if sequence is not None else None


def start_pending_upload_recovery(
    *,
    users_db_path: Path,
    sync_ai_processing: bool,
    load_model: bool,
    on_finished,
    processing_workers: UploadProcessingWorkers,
    write_registry: SQLiteWriteCoordinatorRegistry | None = None,
) -> tuple[threading.Event, threading.Thread]:
    """Start the periodic recovery worker and return its shutdown handles."""
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_run_pending_upload_recovery,
        kwargs={
            "users_db_path": users_db_path,
            "sync_ai_processing": sync_ai_processing,
            "load_model": load_model,
            "on_finished": on_finished,
            "stop_event": stop_event,
            "processing_workers": processing_workers,
            "write_registry": write_registry,
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
    processing_workers: UploadProcessingWorkers,
    write_registry: SQLiteWriteCoordinatorRegistry | None = None,
) -> None:
    from core.users_db import list_users

    try:
        # Account definitions do not change the set of recovery jobs for an
        # existing process: a restart refreshes this snapshot. Avoid rerunning
        # users-db migrations every polling interval alongside auth traffic.
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
            processing_workers=processing_workers,
            write_registry=write_registry,
        )
        stop_event.wait(_RECOVERY_SCAN_INTERVAL_SECONDS)
