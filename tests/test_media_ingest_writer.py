from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from core.indexer_db import init_db
from core.media_ingest import ingest_uploads_without_ai
from core.sqlite_write_registry import SQLiteWriteCoordinatorRegistry
from core.sync_db import ensure_tables, now_iso


def _database_with_processing_uploads(
    root: Path,
    payloads: list[bytes],
) -> tuple[Path, Path, str, list[tuple[str, Path]]]:
    db_path = root / "account.db"
    media_root = root / "media"
    media_root.mkdir()
    token = "lease-token"
    uploads: list[tuple[str, Path]] = []
    connection = init_db(db_path)
    try:
        ensure_tables(connection)
        for index, payload in enumerate(payloads):
            upload_id = f"upload-{index}"
            file_path = media_root / f"IMG_{index:04d}.jpg"
            file_path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            connection.execute(
                """INSERT INTO sync_uploads (
                       id, device_id, filename, expected_size, expected_hash,
                       received_size, captured_at, state, temp_path, created_at,
                       updated_at, source_id, source_name, source_relative_path,
                       source_volume, source_media_store_id, source_generation,
                       source_media_kind, processing_lease_token
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'processing', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    upload_id,
                    "device-1",
                    file_path.name,
                    len(payload),
                    digest,
                    len(payload),
                    "2026-10-01T12:00:00Z",
                    now_iso(),
                    now_iso(),
                    f"source-{index}",
                    f"Camera {index}",
                    f"DCIM/Camera/IMG_{index:04d}.jpg",
                    "volume-1",
                    str(index + 1),
                    1,
                    "image",
                    token,
                ),
            )
            uploads.append((upload_id, file_path))
        connection.commit()
    finally:
        connection.close()
    return db_path, media_root, token, uploads


def test_writer_commits_per_item_catalog_and_feed_before_duplicate_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"same image bytes"
    db_path, media_root, token, uploads = _database_with_processing_uploads(
        tmp_path, [payload, payload]
    )
    registry = SQLiteWriteCoordinatorRegistry(
        coordinator_options={"batch_window_s": 0},
    )
    duplicate_path = uploads[1][1]
    unlink_observations: list[tuple[int, list[str], int]] = []
    finished: list[str] = []
    original_unlink = Path.unlink

    def observe_unlink(path: Path, *args, **kwargs):
        if path == duplicate_path:
            with sqlite3.connect(db_path) as connection:
                states = [row[0] for row in connection.execute(
                    "SELECT state FROM sync_uploads ORDER BY id"
                )]
                media_count = connection.execute("SELECT COUNT(*) FROM memes").fetchone()[0]
                feed_count = connection.execute("SELECT COUNT(*) FROM sync_changes").fetchone()[0]
            unlink_observations.append((media_count, states, feed_count))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", observe_unlink)
    try:
        results = ingest_uploads_without_ai(
            db_path=db_path,
            media_root=media_root,
            processing_lease_token=token,
            uploads=uploads,
            on_finished=finished.append,
            write_registry=registry,
        )
    finally:
        assert registry.shutdown(timeout=5)

    assert [results[upload_id]["state"] for upload_id, _ in uploads] == ["ready", "duplicate"]
    assert unlink_observations == [(1, ["ready", "duplicate"], 2)]
    assert finished == ["upload-0", "upload-1"]
    assert uploads[0][1].is_file()
    assert not duplicate_path.exists()
    with sqlite3.connect(db_path) as connection:
        feed = connection.execute(
            "SELECT entity_id, payload_json FROM sync_changes ORDER BY sequence"
        ).fetchall()
        origins = connection.execute("SELECT COUNT(*) FROM media_origins").fetchone()[0]
    assert [entry[0] for entry in feed] == ["upload-0", "upload-1"]
    assert [json.loads(entry[1])["state"] for entry in feed] == ["ready", "duplicate"]
    assert origins == 2


def test_writer_rolls_back_entire_catalog_batch_before_cleanup_or_callbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"same image bytes"
    db_path, media_root, token, uploads = _database_with_processing_uploads(
        tmp_path, [payload, payload]
    )
    import core.media_ingest as media_ingest

    real_ingest_one = media_ingest._ingest_one

    def fail_second(connection, catalog, library_root, lease_token, prepared):
        if prepared.upload_id == "upload-1":
            raise OSError("simulated catalog failure")
        return real_ingest_one(connection, catalog, library_root, lease_token, prepared)

    monkeypatch.setattr(media_ingest, "_ingest_one", fail_second)
    registry = SQLiteWriteCoordinatorRegistry(
        coordinator_options={"batch_window_s": 0},
    )
    finished: list[str] = []
    try:
        with pytest.raises(OSError, match="simulated catalog failure"):
            ingest_uploads_without_ai(
                db_path=db_path,
                media_root=media_root,
                processing_lease_token=token,
                uploads=uploads,
                on_finished=finished.append,
                write_registry=registry,
            )
    finally:
        assert registry.shutdown(timeout=5)

    assert all(path.is_file() for _upload_id, path in uploads)
    assert finished == []
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sync_changes").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sync_uploads WHERE state != 'processing'"
        ).fetchone()[0] == 0
