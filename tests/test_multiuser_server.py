"""Black-box regression for the authenticated private-library boundary."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_authenticated_account_cannot_see_another_library(tmp_path: Path):
    script = r'''
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user

data = Path("data")
alice = create_user(data / "users.db", data, username="alice", password_hash=hash_password("senha segura 123"), is_admin=True)
bob = create_user(data / "users.db", data, username="bob", password_hash=hash_password("senha segura 456"))
init_db(alice.db_path).close()
init_db(bob.db_path).close()
with sqlite3.connect(alice.db_path) as conn:
    conn.execute("INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)", ("somente-alice.jpg", str(alice.media_root / "somente-alice.jpg"), b"\\0" * 16))
secret = bob.media_root / "segredo.jpg"
with sqlite3.connect(bob.db_path) as conn:
    conn.execute("INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)", ("segredo.jpg", str(secret), b"\0" * 16))

import server
with TestClient(server.app) as client:
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/info").status_code == 401
    assert client.post("/api/auth/login", data={"username": "alice", "password": "senha segura 123"}).status_code == 200
    info = client.get("/api/info")
    assert info.status_code == 200
    assert info.json()["db_path"] == ""
    assert info.json()["media_root"] == ""
    # Populate the sorted-record cache with Alice's library first. Bob must not
    # receive that cached list after the account changes.
    alice_records = client.get("/api/records").json()["records"]
    assert [record["arquivo"] for record in alice_records] == ["somente-alice.jpg"]
    assert client.post("/api/auth/logout").status_code == 200
    assert client.post("/api/auth/login", data={"username": "bob", "password": "senha segura 456"}).status_code == 200
    bob_records = client.get("/api/records").json()["records"]
    assert [record["arquivo"] for record in bob_records] == ["segredo.jpg"]
    assert client.get("/media/" + str(secret).lstrip("/")).status_code == 404
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), IRIS_LOAD_MODEL="0")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
