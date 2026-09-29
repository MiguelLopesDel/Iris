"""Persist exclusive upload-processing leases and retry state per library."""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.sync_db import append_change, now_iso
from core.upload_catalog_writer import UploadCatalogWriter

_PROCESSING_LOCK_NAME = "library-processing"
_PROCESSING_LEASE_SECONDS = 90
_PROCESSING_HEARTBEAT_SECONDS = 20
_PROCESSING_MAX_ATTEMPTS = 4
_PROCESSING_RETRY_DELAYS_SECONDS = (30, 120, 600)
_logger = logging.getLogger("iris.sync")


class UploadProcessingStore:
    """Own the SQLite state machine for processing claims, leases, and retries."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def claim(self, upload_id: str, token: str) -> tuple[str | None, int | str]:
        connection = sqlite3.connect(self._db_path, timeout=30)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT state, processing_lease_until, processing_attempts,
                          processing_next_attempt_at
                   FROM sync_uploads WHERE id = ?""",
                (upload_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None, "missing"
            state, lease_until, _attempts, next_attempt = row
            if state in {"ready", "duplicate", "failed_processing"}:
                connection.commit()
                return None, str(state)
            now = now_iso()
            if state not in {"pending_processing", "processing"}:
                connection.commit()
                return None, str(state)
            if state == "pending_processing" and next_attempt and next_attempt > now:
                connection.commit()
                return None, str(state)
            if state == "processing" and lease_until and lease_until > now:
                connection.commit()
                return None, "processing"

            library_lease = connection.execute(
                "SELECT owner_token, lease_until FROM sync_processing_leases "
                "WHERE lock_name = ?",
                (_PROCESSING_LOCK_NAME,),
            ).fetchone()
            if library_lease and library_lease[1] > now and library_lease[0] != token:
                connection.commit()
                return None, "processing" if state == "processing" else "pending_processing"

            lease_until = _lease_deadline()
            connection.execute(
                """INSERT INTO sync_processing_leases (lock_name, owner_token, lease_until)
                   VALUES (?, ?, ?)
                   ON CONFLICT(lock_name) DO UPDATE SET
                       owner_token = excluded.owner_token, lease_until = excluded.lease_until""",
                (_PROCESSING_LOCK_NAME, token, lease_until),
            )
            connection.execute(
                """UPDATE sync_uploads SET state = 'processing', processing_lease_token = ?,
                          processing_lease_until = ?, processing_attempts = processing_attempts + 1,
                          processing_next_attempt_at = '', updated_at = ? WHERE id = ?""",
                (token, lease_until, now, upload_id),
            )
            attempts = connection.execute(
                "SELECT processing_attempts FROM sync_uploads WHERE id = ?", (upload_id,)
            ).fetchone()
            append_change(connection, "media", upload_id, "updated", 2, {
                "upload_id": upload_id,
                "state": "processing",
            })
            connection.commit()
            return token, int(attempts[0])
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew_until_stopped(
        self,
        upload_id: str,
        token: str,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.wait(_PROCESSING_HEARTBEAT_SECONDS):
            connection = sqlite3.connect(self._db_path, timeout=30)
            try:
                connection.execute("BEGIN IMMEDIATE")
                lease_until = _lease_deadline()
                upload = connection.execute(
                    """UPDATE sync_uploads SET processing_lease_until = ?
                       WHERE id = ? AND processing_lease_token = ?""",
                    (lease_until, upload_id, token),
                )
                library = connection.execute(
                    """UPDATE sync_processing_leases SET lease_until = ?
                       WHERE lock_name = ? AND owner_token = ?""",
                    (lease_until, _PROCESSING_LOCK_NAME, token),
                )
                connection.commit()
                if upload.rowcount != 1 or library.rowcount != 1:
                    stop_event.set()
                    return
            except sqlite3.Error as exc:
                connection.rollback()
                _logger.warning(
                    "sync_processing_lease_renewal_failed upload_id=%s error_type=%s",
                    upload_id,
                    type(exc).__name__,
                )
            finally:
                connection.close()

    def release(self, upload_id: str, token: str) -> None:
        connection = sqlite3.connect(self._db_path, timeout=30)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE sync_uploads SET processing_lease_token = NULL,
                          processing_lease_until = '', updated_at = ?
                   WHERE id = ? AND processing_lease_token = ?""",
                (now_iso(), upload_id, token),
            )
            connection.execute(
                "DELETE FROM sync_processing_leases WHERE lock_name = ? AND owner_token = ?",
                (_PROCESSING_LOCK_NAME, token),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            _logger.warning("sync_processing_lease_release_failed upload_id=%s", upload_id)
        finally:
            connection.close()

    def record_failure(
        self,
        upload_id: str,
        token: str,
        attempts: int,
        error_type: str,
    ) -> dict[str, int | str]:
        connection = sqlite3.connect(self._db_path, timeout=30)
        try:
            if attempts < _PROCESSING_MAX_ATTEMPTS:
                delay = _PROCESSING_RETRY_DELAYS_SECONDS[
                    min(attempts - 1, len(_PROCESSING_RETRY_DELAYS_SECONDS) - 1)
                ]
                retry_at = (
                    datetime.now(UTC) + timedelta(seconds=delay)
                ).isoformat()
                state = "pending_processing"
                error = f"Processing failed ({error_type}); retry scheduled."
            else:
                retry_at = ""
                state = "failed_processing"
                error = "Processing failed after automatic retries; check server logs."
            with connection:
                changed = connection.execute(
                    """UPDATE sync_uploads SET state = ?, processing_next_attempt_at = ?,
                              updated_at = ? WHERE id = ? AND processing_lease_token = ?""",
                    (state, retry_at, now_iso(), upload_id, token),
                )
                if changed.rowcount == 1:
                    append_change(connection, "media", upload_id, "updated", 3, {
                        "upload_id": upload_id,
                        "state": state,
                        "retry_at": retry_at,
                        "error": error,
                    })
                else:
                    row = connection.execute(
                        "SELECT state FROM sync_uploads WHERE id = ?", (upload_id,)
                    ).fetchone()
                    return {"upload_id": upload_id, "state": row[0] if row else "missing"}
            return {"upload_id": upload_id, "state": state}
        finally:
            connection.close()

    def result(self, upload_id: str, state: str) -> dict[str, int | str]:
        result: dict[str, int | str] = {"upload_id": upload_id, "state": state}
        connection = sqlite3.connect(self._db_path)
        try:
            catalog = UploadCatalogWriter(connection)
            row = connection.execute(
                "SELECT expected_hash FROM sync_uploads WHERE id = ?", (upload_id,)
            ).fetchone()
            if row is not None and state in {"ready", "duplicate"}:
                result["cursor"] = catalog.latest_change_cursor(upload_id)
                media_id = catalog.find_media_id_by_hash(row[0], latest_first=True)
                if media_id is not None:
                    result["media_id"] = media_id
        finally:
            connection.close()
        return result


def _lease_deadline() -> str:
    return (datetime.now(UTC) + timedelta(seconds=_PROCESSING_LEASE_SECONDS)).isoformat()
