"""Persist exclusive upload-processing leases and retry state per library."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.sqlite_write_registry import SQLiteWriteCoordinatorRegistry
from core.sync_db import append_change, now_iso
from core.sync_durability import connect_deferred
from core.upload_catalog_writer import UploadCatalogWriter

_PROCESSING_LOCK_NAME = "library-processing"
_PROCESSING_LEASE_SECONDS = 90
_PROCESSING_HEARTBEAT_SECONDS = 20
_PROCESSING_MAX_ATTEMPTS = 4
_PROCESSING_RETRY_DELAYS_SECONDS = (30, 120, 600)
_logger = logging.getLogger("iris.sync")


class UploadProcessingStore:
    """Own the SQLite state machine for processing claims, leases, and retries."""

    def __init__(
        self,
        db_path: Path,
        write_registry: SQLiteWriteCoordinatorRegistry | None = None,
    ) -> None:
        self._db_path = db_path
        self._write_registry = write_registry

    def _write[T](self, callback: Callable[[sqlite3.Connection], T]) -> T:
        if self._write_registry is not None:
            return self._write_registry.submit(self._db_path, callback).result()
        connection = connect_deferred(self._db_path, timeout=30)
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = callback(connection)
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim(self, upload_id: str, token: str) -> tuple[str | None, int | str]:
        return self.claim_many([upload_id], token)[upload_id]

    def claim_many(
        self,
        upload_ids: list[str],
        token: str,
    ) -> dict[str, tuple[str | None, int | str]]:
        """Claim uploads for processing under one library lease, in one transaction.

        Each answer is ``(token, attempts)`` when claimed, or ``(None, state)``
        when the upload is not this caller's to process now.
        """

        def claim_batch(connection: sqlite3.Connection):
            now = now_iso()
            lease_until = _lease_deadline()
            return {
                upload_id: _claim_row(connection, upload_id, token, now, lease_until)
                for upload_id in upload_ids
            }

        return self._write(claim_batch)

    def renew_until_stopped(
        self,
        upload_id: str | list[str],
        token: str,
        stop_event: threading.Event,
    ) -> None:
        upload_ids = [upload_id] if isinstance(upload_id, str) else list(upload_id)
        marks = ", ".join("?" * len(upload_ids))
        while not stop_event.wait(_PROCESSING_HEARTBEAT_SECONDS):

            def renew(connection: sqlite3.Connection) -> tuple[int, int]:
                lease_until = _lease_deadline()
                upload = connection.execute(
                    f"""UPDATE sync_uploads SET processing_lease_until = ?
                        WHERE id IN ({marks}) AND processing_lease_token = ?""",
                    (lease_until, *upload_ids, token),
                )
                library = connection.execute(
                    """UPDATE sync_processing_leases SET lease_until = ?
                       WHERE lock_name = ? AND owner_token = ?""",
                    (lease_until, _PROCESSING_LOCK_NAME, token),
                )
                return upload.rowcount, library.rowcount

            try:
                upload_count, library_count = self._write(renew)
                if upload_count < 1 or library_count != 1:
                    stop_event.set()
                    return
            except (sqlite3.Error, RuntimeError) as exc:
                _logger.warning(
                    "sync_processing_lease_renewal_failed upload_id=%s error_type=%s",
                    upload_ids[0],
                    type(exc).__name__,
                )

    def release(
        self,
        upload_id: str | list[str],
        token: str,
        *,
        uncount_attempt: bool = False,
    ) -> None:
        """Give the uploads and the library lease back.

        ``uncount_attempt`` takes back the attempt the claim counted, for a
        batch that failed as a whole before each upload is tried on its own.
        """
        upload_ids = [upload_id] if isinstance(upload_id, str) else list(upload_id)
        attempt_change = 1 if uncount_attempt else 0
        marks = ", ".join("?" * len(upload_ids))
        try:

            def release(connection: sqlite3.Connection) -> None:
                connection.execute(
                    f"""UPDATE sync_uploads SET processing_lease_token = NULL,
                           processing_lease_until = '', updated_at = ?,
                           processing_attempts = MAX(0, processing_attempts - ?)
                    WHERE id IN ({marks}) AND processing_lease_token = ?""",
                    (now_iso(), attempt_change, *upload_ids, token),
                )
                connection.execute(
                    "DELETE FROM sync_processing_leases WHERE lock_name = ? AND owner_token = ?",
                    (_PROCESSING_LOCK_NAME, token),
                )

            self._write(release)
        except (sqlite3.Error, RuntimeError):
            _logger.warning("sync_processing_lease_release_failed upload_id=%s", upload_ids[0])

    def record_failure(
        self,
        upload_id: str,
        token: str,
        attempts: int,
        error_type: str,
    ) -> dict[str, int | str]:
        def record(connection: sqlite3.Connection) -> dict[str, int | str]:
            if attempts < _PROCESSING_MAX_ATTEMPTS:
                delay = _PROCESSING_RETRY_DELAYS_SECONDS[
                    min(attempts - 1, len(_PROCESSING_RETRY_DELAYS_SECONDS) - 1)
                ]
                retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
                state = "pending_processing"
                error = f"Processing failed ({error_type}); retry scheduled."
            else:
                retry_at = ""
                state = "failed_processing"
                error = "Processing failed after automatic retries; check server logs."
            changed = connection.execute(
                """UPDATE sync_uploads SET state = ?, processing_next_attempt_at = ?,
                              updated_at = ? WHERE id = ? AND processing_lease_token = ?""",
                (state, retry_at, now_iso(), upload_id, token),
            )
            if changed.rowcount == 1:
                append_change(
                    connection,
                    "media",
                    upload_id,
                    "updated",
                    3,
                    {
                        "upload_id": upload_id,
                        "state": state,
                        "retry_at": retry_at,
                        "error": error,
                    },
                )
            else:
                row = connection.execute(
                    "SELECT state FROM sync_uploads WHERE id = ?", (upload_id,)
                ).fetchone()
                return {"upload_id": upload_id, "state": row[0] if row else "missing"}
            return {"upload_id": upload_id, "state": state}

        return self._write(record)

    def result(self, upload_id: str, state: str) -> dict[str, int | str]:
        result: dict[str, int | str] = {"upload_id": upload_id, "state": state}
        connection = connect_deferred(self._db_path)
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


def _claim_row(
    connection: sqlite3.Connection,
    upload_id: str,
    token: str,
    now: str,
    lease_until: str,
) -> tuple[str | None, int | str]:
    """Claim one upload inside the caller's write transaction."""
    row = connection.execute(
        """SELECT state, processing_lease_until, processing_attempts,
                  processing_next_attempt_at
           FROM sync_uploads WHERE id = ?""",
        (upload_id,),
    ).fetchone()
    if row is None:
        return None, "missing"
    state, upload_lease_until, _attempts, next_attempt = row
    if state in {"ready", "duplicate", "failed_processing"}:
        return None, str(state)
    if state not in {"pending_processing", "processing"}:
        return None, str(state)
    if state == "pending_processing" and next_attempt and next_attempt > now:
        return None, str(state)
    if state == "processing" and upload_lease_until and upload_lease_until > now:
        return None, "processing"

    library_lease = connection.execute(
        "SELECT owner_token, lease_until FROM sync_processing_leases WHERE lock_name = ?",
        (_PROCESSING_LOCK_NAME,),
    ).fetchone()
    if library_lease and library_lease[1] > now and library_lease[0] != token:
        return None, "processing" if state == "processing" else "pending_processing"

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
    append_change(
        connection,
        "media",
        upload_id,
        "updated",
        2,
        {
            "upload_id": upload_id,
            "state": "processing",
        },
    )
    return token, int(attempts[0])


def _lease_deadline() -> str:
    return (datetime.now(UTC) + timedelta(seconds=_PROCESSING_LEASE_SECONDS)).isoformat()
