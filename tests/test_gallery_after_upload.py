"""Media added after the gallery was first listed must appear in it."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_gallery_lists_media_added_after_its_order_was_cached(tmp_path: Path):
    script = r'''
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user

data = Path("data")
alice = create_user(data / "users.db", data, username="alice", password_hash=hash_password("senha segura 123"))
init_db(alice.db_path).close()

def add(name, mtime):
    with sqlite3.connect(alice.db_path) as conn:
        conn.execute(
            "INSERT INTO memes (arquivo, caminho, file_mtime, embedding) VALUES (?, ?, ?, NULL)",
            (name, str(alice.media_root / name), mtime),
        )

add("antiga.jpg", 1_700_000_000.0)

import server
with TestClient(server.app) as client:
    assert client.post("/api/auth/login", data={"username": "alice", "password": "senha segura 123"}).status_code == 200
    for sort_by in ("data", "importacao", "nome"):
        listed = client.get("/api/records", params={"sort_by": sort_by}).json()["records"]
        assert [r["arquivo"] for r in listed] == ["antiga.jpg"]

    # What finishing a device upload does: a new row, then the account's backend is replaced.
    add("nova.jpg", 1_800_000_000.0)
    server.app.state.backend_registry.invalidate(alice.id)

    listed = client.get("/api/records", params={"sort_by": "data"}).json()["records"]
    assert [r["arquivo"] for r in listed] == ["nova.jpg", "antiga.jpg"], listed
    for sort_by in ("importacao", "nome"):
        assert len(client.get("/api/records", params={"sort_by": sort_by}).json()["records"]) == 2
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), IRIS_LOAD_MODEL="0")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_request_finishing_on_the_replaced_backend_cannot_poison_the_new_order(tmp_path: Path):
    script = r'''
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user

data = Path("data")
alice = create_user(data / "users.db", data, username="alice", password_hash=hash_password("senha segura 123"))
init_db(alice.db_path).close()

def add(name, mtime):
    with sqlite3.connect(alice.db_path) as conn:
        conn.execute(
            "INSERT INTO memes (arquivo, caminho, file_mtime, embedding) VALUES (?, ?, ?, NULL)",
            (name, str(alice.media_root / name), mtime),
        )

add("um.jpg", 1_700_000_000.0)
add("dois.jpg", 1_700_000_100.0)

import server
with TestClient(server.app) as client:
    assert client.post("/api/auth/login", data={"username": "alice", "password": "senha segura 123"}).status_code == 200
    registry = server.app.state.backend_registry
    old = registry.get(alice.id)

    add("tres.jpg", 1_800_000_000.0)
    registry.invalidate(alice.id)
    new = registry.get(alice.id)
    assert new is not old

    # A request that got the old backend before the upload finishes now and
    # stores the old catalogue's order after the invalidation.
    server._sorted_records(old, "data", 0)

    listed = client.get("/api/records", params={"sort_by": "data"}).json()["records"]
    assert [r["arquivo"] for r in listed] == ["tres.jpg", "dois.jpg", "um.jpg"], listed
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), IRIS_LOAD_MODEL="0")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
