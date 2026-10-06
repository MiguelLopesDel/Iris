"""Sync commits skip the disk wait; one barrier per request makes them durable."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import core.sync_upload_service as service_module
from core.sync_durability import connect_deferred, make_durable
from core.sync_upload_service import SyncUploadService
from core.users_db import IrisUser


def test_deferred_commits_only_in_wal_mode(tmp_path: Path):
    wal = tmp_path / "wal.db"
    sqlite3.connect(wal).execute("PRAGMA journal_mode=WAL").close()
    rollback = tmp_path / "rollback.db"
    sqlite3.connect(rollback).close()

    # 1 = NORMAL, 2 = FULL (SQLite's default)
    assert connect_deferred(wal).execute("PRAGMA synchronous").fetchone()[0] == 1
    assert connect_deferred(rollback).execute("PRAGMA synchronous").fetchone()[0] == 2


def test_the_barrier_commits_a_write_with_a_full_sync(tmp_path: Path, monkeypatch):
    # A FULL commit syncs the whole WAL only if it writes something.
    db = tmp_path / "iris.db"
    sqlite3.connect(db).execute("PRAGMA journal_mode=WAL").close()
    statements: list[str] = []
    original = sqlite3.connect

    def traced(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)
    make_durable(db)
    make_durable(db)

    assert statements.count("PRAGMA synchronous=FULL") == 2
    assert sum(statement.startswith("INSERT INTO durability_marks") for statement in statements) == 2
    marks = original(db).execute("SELECT COUNT(*) FROM durability_marks").fetchone()[0]
    assert marks == 1


def test_a_completion_batch_syncs_the_disk_once(tmp_path: Path, monkeypatch):
    user = IrisUser(
        id=1, username="alice", password_hash="unused", display_name="", is_admin=False,
        db_path=tmp_path / "iris.db", media_root=tmp_path / "media", model_name="", session_version=1,
    )
    barriers: list[Path] = []
    monkeypatch.setattr(service_module, "make_durable", barriers.append)
    service = SyncUploadService()

    result = asyncio.run(service.complete_upload_batch(
        user, "phone-1", {"uploads": [{"upload_id": f"missing-{index}"} for index in range(3)]},
        sync_ai_processing=False, load_model=False,
        on_finished=lambda: None, log_phase=lambda *a, **k: None,
    ))

    assert [item.get("error_code") for item in result["uploads"]] == [404, 404, 404]
    assert barriers == [user.db_path]
