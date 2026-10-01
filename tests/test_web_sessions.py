"""A signed-in browser is a device: listed, revocable, and dead once revoked."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from core.web_sessions import describe_browser

FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Mobile Safari/537.36"
)


def _run(tmp_path: Path, script: str) -> str:
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
        IRIS_LOAD_MODEL="0",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_browsers_are_named_by_what_people_recognise() -> None:
    assert describe_browser(FIREFOX_LINUX) == "Firefox no Linux"
    assert describe_browser(CHROME_ANDROID) == "Chrome no Android"
    assert describe_browser("") == "Navegador"


_ACCOUNT = r'''
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.users_db import create_user

data = Path("data")
create_user(data / "users.db", data, username="ana", is_admin=True,
            password_hash=hash_password("synthetic password 1"))
import server

def browser(user_agent):
    client = TestClient(server.app, headers={"User-Agent": user_agent})
    client.__enter__()
    signed = client.post("/api/auth/login", data={"username": "ana", "password": "synthetic password 1"})
    assert signed.status_code == 200, signed.text
    return client
'''


def test_revoking_a_browser_signs_it_out_on_its_next_request(tmp_path: Path) -> None:
    out = _run(tmp_path, _ACCOUNT + f'''
laptop = browser({FIREFOX_LINUX!r})
phone = browser({CHROME_ANDROID!r})

seen_from_phone = phone.get("/api/sync/devices").json()["devices"]
assert sorted((d["name"], d["platform"], d["current"]) for d in seen_from_phone) == [
    ("Chrome no Android", "web", True), ("Firefox no Linux", "web", False),
]
laptop_id = next(d["id"] for d in seen_from_phone if not d["current"])

assert laptop.get("/api/auth/me").status_code == 200
assert phone.delete("/api/sync/devices/" + laptop_id).status_code == 200

assert laptop.get("/api/auth/me").status_code == 401
page = laptop.get("/", follow_redirects=False)
assert page.status_code == 303 and page.headers["location"] == "/login"
# Signing in again is a new device; the revoked one stays revoked.
again = laptop.post("/api/auth/login", data={{"username": "ana", "password": "synthetic password 1"}})
assert again.status_code == 200
assert phone.get("/api/auth/me").status_code == 200
names = sorted(d["name"] for d in phone.get("/api/sync/devices").json()["devices"])
assert names == ["Chrome no Android", "Firefox no Linux"], names
print("ok")
''')
    assert out.strip().endswith("ok")


def test_signing_out_removes_the_browser_from_the_devices(tmp_path: Path) -> None:
    out = _run(tmp_path, _ACCOUNT + f'''
laptop = browser({FIREFOX_LINUX!r})
phone = browser({CHROME_ANDROID!r})
assert laptop.post("/api/auth/logout").status_code == 200
assert laptop.get("/api/auth/me").status_code == 401
assert [d["name"] for d in phone.get("/api/sync/devices").json()["devices"]] == ["Chrome no Android"]
print("ok")
''')
    assert out.strip().endswith("ok")


def test_a_session_from_before_browsers_were_devices_stays_signed_in(tmp_path: Path) -> None:
    out = _run(tmp_path, _ACCOUNT + f'''
import base64, json
import itsdangerous
from core.users_db import get_user_by_username

user = get_user_by_username(data / "users.db", "ana")
old = {{"user_id": user.id, "session_version": user.session_version}}
signer = itsdangerous.TimestampSigner(str(server.app.state.auth_secret))
cookie = signer.sign(base64.b64encode(json.dumps(old).encode())).decode()

with TestClient(server.app, headers={{"User-Agent": {FIREFOX_LINUX!r}}}) as client:
    client.cookies.set("iris_session", cookie)
    assert client.get("/api/auth/me").status_code == 200
    devices = client.get("/api/sync/devices").json()["devices"]
    assert [(d["name"], d["current"]) for d in devices] == [("Firefox no Linux", True)], devices
    assert client.get("/api/auth/me").status_code == 200
    assert len(client.get("/api/sync/devices").json()["devices"]) == 1  # attached once, not per request
print("ok")
''')
    assert out.strip().endswith("ok")
