"""The perf probe route reads no library, so it must not load the gallery backend.

Loading it rebuilt the whole catalog after every upload (each completion
invalidates the backend) and blocked the event loop the probe measures.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = r'''
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.backend_registry import BackendRegistry
from core.users_db import create_user

data = Path("data")
create_user(data / "users.db", data, username="alice", password_hash=hash_password("senha segura 123"))
import server

class CountingRegistry(BackendRegistry):
    calls = 0
    def get(self, user_id):
        CountingRegistry.calls += 1
        return super().get(user_id)

server.app.state.backend_registry = CountingRegistry(data / "users.db", load_model=False)
with TestClient(server.app) as client:
    token = client.post("/api/auth/devices/login", data={
        "username": "alice", "password": "senha segura 123", "device_name": "phone", "platform": "android",
    }).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    for _ in range(3):
        probe = client.get("/api/_perf/probe", headers=headers)
        assert probe.status_code == 200, probe.text
        assert "loop_lag_ms_max" in probe.json()
    assert client.get("/api/_perf/probe").status_code == 401  # still authenticated
    assert CountingRegistry.calls == 0, CountingRegistry.calls
print("ok")
'''


def test_the_probe_does_not_load_the_gallery_backend(tmp_path: Path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), IRIS_PERF_PROBE="1",
               IRIS_LOAD_MODEL="0")
    result = subprocess.run([sys.executable, "-c", _SCRIPT], cwd=tmp_path, env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("ok")
