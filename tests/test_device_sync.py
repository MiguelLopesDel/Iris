"""Black-box coverage for device credentials and resumable sync uploads."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_device_can_upload_and_read_incremental_changes(tmp_path: Path):
    script = r'''
import hashlib
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.users_db import create_user

data = Path("data")
create_user(data / "users.db", data, username="alice", password_hash=hash_password("senha segura 123"), is_admin=True)

import server
body = b"iris sync payload"
with TestClient(server.app) as client:
    login = client.post("/api/auth/devices/login", data={
        "username": "alice", "password": "senha segura 123",
        "device_name": "Alice phone", "platform": "android",
    })
    assert login.status_code == 200, login.text
    session = login.json()
    token = session["access_token"]
    headers = {"Authorization": "Bearer " + token}
    refreshed = client.post("/api/auth/devices/refresh", data={
        "device_id": session["device_id"], "refresh_token": session["refresh_token"],
    })
    assert refreshed.status_code == 200, refreshed.text
    headers = {"Authorization": "Bearer " + refreshed.json()["access_token"]}
    assert client.get("/api/sync/devices", headers=headers).json()["devices"][0]["name"] == "Alice phone"
    started = client.post("/api/sync/uploads", headers=headers, json={
        "filename": "photo.jpg", "size": len(body), "sha256": hashlib.sha256(body).hexdigest(),
        "captured_at": "2026-09-09T12:00:00Z",
    })
    assert started.status_code == 200, started.text
    upload_id = started.json()["upload_id"]
    chunk = client.put("/api/sync/uploads/" + upload_id + "?offset=0", headers=headers, content=body)
    assert chunk.status_code == 200, chunk.text
    completed = client.post("/api/sync/uploads/" + upload_id + "/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == "pending_processing"
    assert Path(completed.json()["path"]).read_bytes() == body
    changes = client.get("/api/sync/changes", headers=headers).json()
    assert changes["changes"][0]["operation"] == "created"
    assert changes["changes"][0]["payload"]["state"] == "pending_processing"
    assert client.delete("/api/sync/devices/" + session["device_id"], headers=headers).status_code == 200
    assert client.get("/api/sync/devices", headers=headers).status_code == 401
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), IRIS_LOAD_MODEL="0")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
