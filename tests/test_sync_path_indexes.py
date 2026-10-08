"""Checking whether a library file is someone else's reads indexes, not whole tables.

Before an upload claims a library file name, sync checks that no catalog row
and no other upload points to it. The usual answer is "none", and without an
index proving that meant reading every row: a cost per photo that grew with
the library and was paid inside the database's write lock.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import core.sync_upload_service as service_module
from core.sync_upload_service import SyncUploadService
from core.users_db import IrisUser


def _user(root: Path) -> IrisUser:
    return IrisUser(
        id=1, username="alice", password_hash="unused", display_name="", is_admin=False,
        db_path=root / "iris.db", media_root=root / "media", model_name="", session_version=1,
    )


def test_the_ownership_check_uses_indexes_only(tmp_path: Path):
    user = _user(tmp_path)
    connection = SyncUploadService.open_connection(user)
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        service_module._referenced_elsewhere(
            connection, user.media_root / "uploads" / "IMG_0001.jpg", "upload-1", user.media_root,
        )
    finally:
        connection.set_trace_callback(None)

    queries = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    assert len(queries) == 3
    for sql in queries:
        plan = " | ".join(row[-1] for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}"))
        assert "SCAN" not in plan, f"{plan}\n  for: {sql}"
    connection.close()


def test_the_indexes_reach_an_existing_library(tmp_path: Path):
    # A library created before these indexes gets them when it is next opened.
    user = _user(tmp_path)
    SyncUploadService.open_connection(user).close()
    with sqlite3.connect(user.db_path) as conn:
        for name in ("idx_memes_caminho", "idx_memes_storage_path", "idx_sync_uploads_final_path"):
            conn.execute(f"DROP INDEX {name}")
    service_module._prepared_databases.clear()

    SyncUploadService.open_connection(user).close()

    with sqlite3.connect(user.db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_memes_caminho", "idx_memes_storage_path", "idx_sync_uploads_final_path"} <= names
