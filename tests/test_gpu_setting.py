"""The administrator's GPU choice decides the compute device everywhere."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import compute_device


@pytest.fixture(autouse=True)
def _restore_choice():
    yield
    compute_device.set_gpu_allowed(True)


def _fake(monkeypatch, *, cuda: bool, mps: bool = False) -> None:
    monkeypatch.setattr(
        compute_device, "gpu_status",
        lambda: compute_device.GpuStatus(cuda, "Fake RTX" if cuda else None, None if cuda else "not_visible"),
    )
    monkeypatch.setattr(compute_device, "_mps_available", lambda: mps)


def test_auto_uses_the_gpu_only_when_allowed_and_present(monkeypatch) -> None:
    _fake(monkeypatch, cuda=True)
    assert compute_device.resolve("auto") == "cuda"
    compute_device.set_gpu_allowed(False)
    assert compute_device.resolve("auto") == "cpu"
    # The administrator's choice also wins over an explicit per-import request.
    assert compute_device.resolve("cuda") == "cpu"


def test_without_a_gpu_requests_fall_back_instead_of_failing(monkeypatch) -> None:
    _fake(monkeypatch, cuda=False)
    assert compute_device.resolve("auto") == "cpu"
    assert compute_device.resolve("cuda") == "cpu"
    _fake(monkeypatch, cuda=False, mps=True)
    assert compute_device.resolve("auto") == "mps"
    assert compute_device.resolve("cpu") == "cpu"


def test_this_cpu_install_reports_why_there_is_no_gpu() -> None:
    status = compute_device.gpu_status()
    assert status.available is False
    assert status.reason == "cpu_image"


def test_administrator_toggles_the_gpu_without_restarting(tmp_path: Path) -> None:
    script = r'''
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.users_db import create_user
from core import compute_device

data = Path("data")
create_user(data / "users.db", data, username="root", is_admin=True,
            password_hash=hash_password("synthetic password 1"))
create_user(data / "users.db", data, username="ana",
            password_hash=hash_password("synthetic password 1"))
import server

with TestClient(server.app) as root, TestClient(server.app) as ana:
    for name, client in (("root", root), ("ana", ana)):
        assert client.post("/api/auth/login", data={"username": name, "password": "synthetic password 1"}).status_code == 200
    state = root.get("/api/admin/settings").json()
    assert state["settings"]["gpu"]["value"] == "on"
    assert state["gpu"] == {"available": False, "name": None, "reason": "cpu_image", "in_use": False}
    assert compute_device.gpu_allowed() is True

    # Engines built before the change must not survive it.
    server.app.state.backend_registry._backends[999] = object()
    off = root.put("/api/admin/settings", json={"gpu": "off"})
    assert off.status_code == 200, off.text
    assert off.json()["settings"]["gpu"] == {**off.json()["settings"]["gpu"], "value": "off", "source": "interface"}
    assert compute_device.gpu_allowed() is False
    assert 999 not in server.app.state.backend_registry._backends

    assert root.put("/api/admin/settings", json={"gpu": "maybe"}).status_code == 422
    assert ana.put("/api/admin/settings", json={"gpu": "on"}).status_code == 403
    assert root.delete("/api/admin/settings/gpu").status_code == 200
    assert compute_device.gpu_allowed() is True
print("ok")
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
        IRIS_LOAD_MODEL="0",
        IRIS_BACKUP_DEST=str(tmp_path / "backups"),
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("ok")
