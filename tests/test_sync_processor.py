from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

from core.sync_db import ensure_tables
from core.sync_processor import (
    _claim_processing_job,
    _record_processing_failure,
    _release_processing_lease,
    recover_pending_uploads,
)


def _database_with_jobs(path, *upload_ids: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, file_size INTEGER DEFAULT 0)")
    ensure_tables(conn)
    conn.executemany(
        """INSERT INTO sync_uploads
        (id, device_id, filename, expected_size, expected_hash, state, temp_path,
         created_at, updated_at)
        VALUES (?, 'device', 'photo.jpg', 1, ?, 'pending_processing', '/tmp/photo', '', '')""",
        [(upload_id, f"{upload_id:0<64}") for upload_id in upload_ids],
    )
    conn.commit()
    conn.close()


def test_processing_claim_is_exclusive_across_workers_and_libraries(tmp_path) -> None:
    db_path = tmp_path / "library.db"
    _database_with_jobs(db_path, "upload-one", "upload-two")

    first = _claim_processing_job(db_path, "upload-one", "worker-a")
    assert first == ("worker-a", 1)
    assert _claim_processing_job(db_path, "upload-two", "worker-b") == (
        None,
        "pending_processing",
    )
    assert _claim_processing_job(db_path, "upload-one", "worker-c") == (
        None,
        "processing",
    )

    # Simulate a dead process: both its item lease and library lock have expired.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE sync_uploads SET processing_lease_until = '2000-01-01T00:00:00+00:00' "
        "WHERE id = 'upload-one'"
    )
    conn.execute(
        "UPDATE sync_processing_leases SET lease_until = '2000-01-01T00:00:00+00:00' "
        "WHERE lock_name = 'library-processing'"
    )
    conn.commit()
    conn.close()

    recovered = _claim_processing_job(db_path, "upload-one", "worker-c")
    assert recovered == ("worker-c", 2)
    _release_processing_lease(db_path, "upload-one", "worker-c")
    assert _claim_processing_job(db_path, "upload-two", "worker-b") == (
        "worker-b",
        1,
    )


def test_processing_failures_retry_with_backoff_then_become_terminal(tmp_path) -> None:
    db_path = tmp_path / "library.db"
    _database_with_jobs(db_path, "upload-one")

    for attempt in range(1, 5):
        token = f"worker-{attempt}"
        claim = _claim_processing_job(db_path, "upload-one", token)
        assert claim == (token, attempt)
        result = _record_processing_failure(
            db_path, "upload-one", token, attempt, "OSError"
        )
        expected_state = "pending_processing" if attempt < 4 else "failed_processing"
        assert result["state"] == expected_state
        _release_processing_lease(db_path, "upload-one", token)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT state, processing_next_attempt_at, processing_attempts "
            "FROM sync_uploads WHERE id = 'upload-one'"
        ).fetchone()
        assert row[0] == expected_state
        assert row[2] == attempt
        if attempt < 4:
            assert row[1]
            # Avoid sleeping through the production backoff in this unit test.
            conn.execute(
                "UPDATE sync_uploads SET processing_next_attempt_at = '' "
                "WHERE id = 'upload-one'"
            )
            conn.commit()
        conn.close()


def test_recovery_scanner_does_not_migrate_an_old_library_database(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "legacy.db"
    sqlite3.connect(database).close()
    user = SimpleNamespace(
        id=1,
        db_path=database,
        media_root=tmp_path / "media",
        model_name="none",
    )
    monkeypatch.setattr("core.users_db.list_users", lambda _path: [user])

    recover_pending_uploads(
        users_db_path=tmp_path / "users.db",
        sync_ai_processing=False,
        load_model=False,
        on_finished=lambda _user_id: None,
        stop_event=threading.Event(),
    )

    conn = sqlite3.connect(database)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert tables == set()
