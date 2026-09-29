from __future__ import annotations

import hashlib
import sqlite3
import sys
import threading
from types import ModuleType, SimpleNamespace

from core.sync_db import ensure_tables
from core.sync_processor import (
    _claim_processing_job,
    _record_processing_failure,
    _release_processing_lease,
    process_upload,
)
from core.sync_recovery import _recover_finalizing_upload, recover_pending_uploads
from core.upload_processing_state import record_upload_processing_result


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


def test_process_upload_with_ai_holds_lease_and_records_catalog_origin(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "library.db"
    media_root = tmp_path / "media"
    file_path = media_root / "uploads" / "photo.jpg"
    digest = hashlib.sha256(b"photo bytes").hexdigest()
    _database_with_jobs(database, "upload-ai")
    conn = sqlite3.connect(database)
    conn.execute("ALTER TABLE memes ADD COLUMN content_hash TEXT")
    conn.execute(
        "UPDATE sync_uploads SET filename = ?, expected_size = ?, expected_hash = ?, "
        "temp_path = ?, captured_at = ?, source_id = ?, source_name = ?, "
        "source_relative_path = ?, source_volume = ?, source_media_store_id = ?, "
        "source_generation = ?, source_media_kind = ? WHERE id = ?",
        (
            "photo.jpg", 11, digest, str(file_path), "2026-09-29T10:00:00Z",
            "camera", "Camera", "DCIM/Camera", "external", "media-42", 7,
            "image", "upload-ai",
        ),
    )
    conn.commit()
    conn.close()

    calls: list[tuple[str, object]] = []
    fake_indexer = ModuleType("core.indexer")
    fake_indexer.IndexerConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    fake_indexer.resolve_device = lambda _requested: "cpu"

    def fake_process_images(config, *, explicit_files, dedup_enabled):
        assert config.db_path == database
        assert config.library_root == media_root
        assert explicit_files == [file_path]
        assert dedup_enabled is False
        conn = sqlite3.connect(database)
        job = conn.execute(
            "SELECT state, processing_lease_token, processing_attempts "
            "FROM sync_uploads WHERE id = 'upload-ai'"
        ).fetchone()
        assert job[0] == "processing"
        assert job[1]
        assert job[2] == 1
        conn.execute(
            "INSERT INTO memes (content_hash, file_size) VALUES (?, ?)", (digest, 11)
        )
        conn.commit()
        conn.close()
        calls.append(("process_images", config.model_name))

    fake_indexer.process_images = fake_process_images
    fake_indexer.create_faiss_indices = lambda db_path, model_name: calls.append(
        ("create_faiss_indices", (db_path, model_name))
    )
    monkeypatch.setitem(sys.modules, "core.indexer", fake_indexer)
    callbacks: list[str] = []

    result = process_upload(
        db_path=database,
        media_root=media_root,
        model_name="test-model",
        upload_id="upload-ai",
        file_path=file_path,
        on_finished=lambda: callbacks.append("finished"),
        use_ai=True,
    )

    assert result == {"upload_id": "upload-ai", "state": "ready", "cursor": 2}
    assert calls == [
        ("process_images", "test-model"),
        ("create_faiss_indices", (database, "test-model")),
    ]
    assert callbacks == ["finished"]
    conn = sqlite3.connect(database)
    upload = conn.execute(
        "SELECT state, processing_attempts, processing_lease_token "
        "FROM sync_uploads WHERE id = 'upload-ai'"
    ).fetchone()
    origin = conn.execute(
        """SELECT o.device_id, o.source_id, o.media_store_id, o.source_generation,
                  s.name, s.relative_path, s.volume, s.media_kind
           FROM media_origins o JOIN device_sources s
             ON s.device_id = o.device_id AND s.source_id = o.source_id"""
    ).fetchone()
    changes = conn.execute(
        "SELECT revision, payload_json FROM sync_changes WHERE entity_id = 'upload-ai' "
        "ORDER BY sequence"
    ).fetchall()
    conn.close()

    assert upload == ("ready", 1, None)
    assert origin == ("device", "camera", "media-42", 7, "Camera", "DCIM/Camera", "external", "image")
    assert [change[0] for change in changes] == [2, 3]
    assert '"state":"ready"' in changes[-1][1]


def test_startup_recovery_uses_shared_idempotent_upload_finalization(tmp_path) -> None:
    import hashlib

    database = tmp_path / "library.db"
    media_root = tmp_path / "media"
    payload = b"durably accepted upload"
    source = tmp_path / "sync_uploads" / "upload.part"
    destination = media_root / "uploads" / "device" / "source" / "photo.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)

    _database_with_jobs(database, "upload-one")
    conn = sqlite3.connect(database)
    conn.execute(
        "UPDATE sync_uploads SET filename = ?, expected_size = ?, expected_hash = ?, "
        "state = 'finalizing', temp_path = ?, final_path = ?, captured_at = ? WHERE id = ?",
        (
            "photo.jpg", len(payload), hashlib.sha256(payload).hexdigest(),
            str(source), str(destination), "2026-09-29T00:00:00Z", "upload-one",
        ),
    )
    conn.commit()
    conn.close()
    user = SimpleNamespace(id=1, db_path=database, media_root=media_root)

    assert _recover_finalizing_upload(user, "upload-one") == destination
    assert destination.read_bytes() == payload
    assert not source.exists()
    assert _recover_finalizing_upload(user, "upload-one") is None

    conn = sqlite3.connect(database)
    state = conn.execute(
        "SELECT state FROM sync_uploads WHERE id = 'upload-one'"
    ).fetchone()[0]
    changes = conn.execute(
        "SELECT entity_type, entity_id, operation, revision, payload_json "
        "FROM sync_changes WHERE entity_id = 'upload-one'"
    ).fetchall()
    conn.close()
    assert state == "pending_processing"
    assert len(changes) == 1
    assert changes[0][:4] == ("media", "upload-one", "created", 1)


def test_startup_recovery_rejects_destination_outside_account_media_root(tmp_path) -> None:
    import hashlib

    database = tmp_path / "library.db"
    payload = b"private upload"
    source = tmp_path / "upload.part"
    source.write_bytes(payload)
    _database_with_jobs(database, "upload-one")
    conn = sqlite3.connect(database)
    conn.execute(
        "UPDATE sync_uploads SET expected_size = ?, expected_hash = ?, temp_path = ?, "
        "final_path = ?, state = 'finalizing' WHERE id = ?",
        (len(payload), hashlib.sha256(payload).hexdigest(), str(source),
         str(tmp_path / "outside" / "photo.jpg"), "upload-one"),
    )
    conn.commit()
    conn.close()
    user = SimpleNamespace(id=1, db_path=database, media_root=tmp_path / "media")

    assert _recover_finalizing_upload(user, "upload-one") is None
    assert source.read_bytes() == payload
    conn = sqlite3.connect(database)
    assert conn.execute(
        "SELECT state FROM sync_uploads WHERE id = 'upload-one'"
    ).fetchone()[0] == "finalizing"
    assert conn.execute("SELECT COUNT(*) FROM sync_changes").fetchone()[0] == 0
    conn.close()


def test_processing_result_commits_state_and_change_under_current_lease(tmp_path) -> None:
    database = tmp_path / "library.db"
    _database_with_jobs(database, "upload-one")
    conn = sqlite3.connect(database)
    conn.execute(
        "UPDATE sync_uploads SET processing_lease_token = 'worker-a' WHERE id = ?",
        ("upload-one",),
    )
    conn.commit()

    sequence = record_upload_processing_result(
        conn,
        upload_id="upload-one",
        lease_token="worker-a",
        media_id=42,
    )
    conn.commit()
    assert sequence == 1
    assert conn.execute(
        "SELECT state FROM sync_uploads WHERE id = 'upload-one'"
    ).fetchone()[0] == "ready"
    assert conn.execute(
        "SELECT payload_json FROM sync_changes WHERE sequence = ?", (sequence,)
    ).fetchone()[0] == '{"upload_id":"upload-one","media_id":42,"state":"ready"}'
    conn.close()


def test_processing_result_refuses_stale_lease_without_change_event(tmp_path) -> None:
    database = tmp_path / "library.db"
    _database_with_jobs(database, "upload-one")
    conn = sqlite3.connect(database)
    conn.execute(
        "UPDATE sync_uploads SET processing_lease_token = 'worker-current' WHERE id = ?",
        ("upload-one",),
    )
    conn.commit()

    try:
        record_upload_processing_result(
            conn,
            upload_id="upload-one",
            lease_token="worker-stale",
            media_id=42,
        )
    except RuntimeError as exc:
        assert "lease was lost" in str(exc)
    else:
        raise AssertionError("A stale worker must not commit its catalog result")

    assert conn.execute("SELECT COUNT(*) FROM sync_changes").fetchone()[0] == 0
    assert conn.execute(
        "SELECT state FROM sync_uploads WHERE id = 'upload-one'"
    ).fetchone()[0] == "pending_processing"
    conn.rollback()
    conn.close()


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
