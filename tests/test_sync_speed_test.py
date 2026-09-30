"""The device speed test: measures the path to the server and keeps nothing."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_speed_test_measures_and_discards_payloads(tmp_path: Path):
    script = r'''
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.backend_registry import BackendRegistry
from core.sync_upload_service import SPEED_TEST_MAX_BYTES
from core.users_db import create_user

data = Path("data")
create_user(data / "users.db", data, username="alice", password_hash=hash_password("senha segura 123"), is_admin=True)

import server
server.app.state.backend_registry = BackendRegistry(data / "users.db", load_model=False)
payload = bytes(range(256)) * 4096  # 1 MiB

with TestClient(server.app) as client:
    login = client.post("/api/auth/devices/login", data={
        "username": "alice", "password": "senha segura 123",
        "device_name": "Alice phone", "platform": "android",
    })
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}

    assert client.post("/api/sync/speedtest", content=payload).status_code == 401

    discard = client.post("/api/sync/speedtest", content=payload, headers=headers)
    assert discard.status_code == 200, discard.text
    assert discard.json()["bytes"] == len(payload)
    assert discard.json()["mode"] == "discard"
    assert discard.json()["server_seconds"] >= 0

    disk = client.post("/api/sync/speedtest?mode=disk", content=payload, headers=headers)
    assert disk.status_code == 200, disk.text
    assert disk.json() == {**disk.json(), "bytes": len(payload), "mode": "disk"}

    assert client.post("/api/sync/speedtest?mode=bogus", content=payload, headers=headers).status_code == 422

    too_big = client.post(
        "/api/sync/speedtest", content=b"x",
        headers={**headers, "Content-Length": str(SPEED_TEST_MAX_BYTES + 1)},
    )
    assert too_big.status_code == 413, too_big.text

# Nothing measured is kept: no scratch file, no media, no upload row.
leftovers = [p for p in data.rglob("*") if p.is_file() and "speedtest" in p.name]
assert leftovers == [], leftovers
media = [p for p in data.rglob("*") if p.is_file() and p.parent.name == "media"]
assert media == [], media
print("ok")
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    env.pop("IRIS_DATA_DIR", None)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("ok")
