"""Device uploads are dated by when they were taken, including ones stored before the fix."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from core.indexer_db import init_db
from core.media_dates import capture_timestamp

SEPT_29 = datetime(2026, 9, 29, 22, 7, 22, tzinfo=UTC).timestamp()
UPLOADED = datetime(2026, 10, 2, 20, 2, 8, tzinfo=UTC)


def test_capture_time_accepts_the_device_iso_format():
    assert capture_timestamp("2026-09-29T22:07:22Z") == SEPT_29
    assert capture_timestamp("2026-09-29T19:07:22-03:00") == SEPT_29
    assert capture_timestamp("2026-09-29T22:07:22") == SEPT_29


def test_absent_or_implausible_capture_times_are_ignored():
    assert capture_timestamp("") is None
    assert capture_timestamp(None) is None
    assert capture_timestamp("ontem") is None
    assert capture_timestamp("1970-01-01T00:00:00Z") is None
    assert capture_timestamp("2099-01-01T00:00:00Z", now=UPLOADED.timestamp()) is None


def _insert(conn, library_id, name, file_mtime, captured_at):
    conn.execute(
        "INSERT INTO memes (arquivo, caminho, library_id, imported_at, file_mtime, metadata_json, embedding) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL)",
        (name, f"/media/{name}", library_id, UPLOADED.isoformat(), file_mtime,
         json.dumps({"kind": "image", "captured_at": captured_at})),
    )


def test_opening_a_library_repairs_device_uploads_dated_by_arrival(tmp_path: Path):
    db = tmp_path / "iris.db"
    conn = init_db(db)
    device = conn.execute(
        "INSERT INTO media_libraries (name, root_path, created_at) VALUES ('device-uploads', '/media', 'x')"
    ).lastrowid
    imported = conn.execute(
        "INSERT INTO media_libraries (name, root_path, created_at) VALUES ('fotos', '/fotos', 'x')"
    ).lastrowid
    arrival = UPLOADED.timestamp() + 1.5
    _insert(conn, device, "dated-by-arrival.jpg", arrival, "2026-09-29T22:07:22Z")
    _insert(conn, device, "already-right.jpg", SEPT_29, "2026-09-29T22:07:22Z")
    _insert(conn, device, "no-capture-time.jpg", arrival, "")
    # Not from a device: its date came from the file and is left alone.
    _insert(conn, imported, "host-import.jpg", arrival, "2026-09-29T22:07:22Z")
    conn.commit()
    conn.close()

    for _ in range(2):  # idempotent
        init_db(db).close()

    dates = dict(sqlite3.connect(db).execute("SELECT arquivo, file_mtime FROM memes").fetchall())
    assert dates["dated-by-arrival.jpg"] == SEPT_29
    assert dates["already-right.jpg"] == SEPT_29
    assert dates["no-capture-time.jpg"] == arrival
    assert dates["host-import.jpg"] == arrival


def test_the_gallery_backend_repairs_a_library_before_loading_it(tmp_path: Path):
    from core.auth import hash_password
    from core.backend_registry import BackendRegistry
    from core.users_db import create_user

    data = tmp_path / "data"
    user = create_user(data / "users.db", data, username="ana", password_hash=hash_password("lua cheia no mar"))
    conn = init_db(user.db_path)
    device = conn.execute(
        "INSERT INTO media_libraries (name, root_path, created_at) VALUES ('device-uploads', '/media', 'x')"
    ).lastrowid
    _insert(conn, device, "dated-by-arrival.jpg", UPLOADED.timestamp(), "2026-09-29T22:07:22Z")
    conn.commit()
    conn.close()

    BackendRegistry(data / "users.db", load_model=False).get(user.id)

    stored = sqlite3.connect(user.db_path).execute("SELECT file_mtime FROM memes").fetchone()[0]
    assert stored == SEPT_29
