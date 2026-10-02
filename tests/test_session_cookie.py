"""The session cookie is Secure exactly when the browser reached Iris over HTTPS."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.session_cookie import ALWAYS, AUTO, NEVER, https_only_mode

ROOT = Path(__file__).resolve().parents[1]


def test_the_setting_reads_three_modes_and_defaults_to_auto() -> None:
    assert https_only_mode({}) == AUTO
    assert https_only_mode({"IRIS_SESSION_HTTPS_ONLY": "auto"}) == AUTO
    assert https_only_mode({"IRIS_SESSION_HTTPS_ONLY": "true"}) == ALWAYS
    assert https_only_mode({"IRIS_SESSION_HTTPS_ONLY": "False"}) == NEVER
    assert https_only_mode({"IRIS_SESSION_HTTPS_ONLY": "maybe"}) == AUTO


_SIGN_IN = r'''
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.users_db import create_user

data = Path("data")
create_user(data / "users.db", data, username="ana", is_admin=True,
            password_hash=hash_password("synthetic password 1"))
import server

def cookie(base_url="http://testserver", headers=None):
    with TestClient(server.app, base_url=base_url) as client:
        response = client.post("/api/auth/login", headers=headers or {},
                               data={"username": "ana", "password": "synthetic password 1"})
        assert response.status_code == 200, response.text
        return response.headers["set-cookie"]

secure = lambda header: "secure" in [part.strip().lower() for part in header.split(";")]
results = {
    "http": secure(cookie()),
    "https": secure(cookie("https://testserver")),
    "proxied": secure(cookie(headers={"X-Forwarded-Proto": "https"})),
    "proxied-http": secure(cookie(headers={"X-Forwarded-Proto": "http"})),
}
print(results)
'''


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        # Unset: the default. Plain HTTP signs in; HTTPS, direct or proxied, is Secure.
        (None, {"http": False, "https": True, "proxied": True, "proxied-http": False}),
        ("auto", {"http": False, "https": True, "proxied": True, "proxied-http": False}),
        ("true", {"http": True, "https": True, "proxied": True, "proxied-http": True}),
        ("false", {"http": False, "https": False, "proxied": False, "proxied-http": False}),
    ],
)
def test_the_session_cookie_follows_the_connection(tmp_path: Path, setting, expected) -> None:
    env = {k: v for k, v in os.environ.items() if k != "IRIS_SESSION_HTTPS_ONLY"}
    env.update(PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private", IRIS_LOAD_MODEL="0")
    if setting is not None:
        env["IRIS_SESSION_HTTPS_ONLY"] = setting
    result = subprocess.run(
        [sys.executable, "-c", _SIGN_IN], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == str(expected)
