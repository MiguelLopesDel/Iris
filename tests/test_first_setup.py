"""First-run web setup: the installation code gates who becomes administrator."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from core import first_setup


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


def test_the_installation_code_is_stable_private_and_forgiving_to_type(tmp_path: Path) -> None:
    code = first_setup.ensure_setup_code(tmp_path)
    assert code == first_setup.ensure_setup_code(tmp_path), "a restart must not change the code"
    mode = stat.S_IMODE((tmp_path / first_setup.CODE_FILE).stat().st_mode)
    assert mode == 0o600
    assert len(code) == 9 and code[4] == "-"
    assert not set(code.replace("-", "")) & set("01ILO")
    assert first_setup.code_matches(tmp_path, code)
    assert first_setup.code_matches(tmp_path, code.lower().replace("-", " "))
    assert not first_setup.code_matches(tmp_path, "AAAA-AAAA")
    first_setup.clear_setup_code(tmp_path)
    assert not first_setup.code_matches(tmp_path, code)


def test_attempts_are_bounded_within_a_window() -> None:
    now = [0.0]
    limiter = first_setup.AttemptLimiter(max_failures=3, window_seconds=60, clock=lambda: now[0])
    for _ in range(3):
        assert not limiter.blocked()
        limiter.record_failure()
    assert limiter.blocked()
    now[0] = 61.0
    assert not limiter.blocked()


def test_web_setup_creates_the_administrator_once_and_signs_in(tmp_path: Path) -> None:
    out = _run(tmp_path, r'''
from pathlib import Path
from fastapi.testclient import TestClient
import server

data = Path("data")
with TestClient(server.app) as client:
    assert client.get("/healthz").json()["status"] == "setup_required"
    code = (data / "setup_code").read_text().strip()
    assert client.get("/api/setup").json() == {"required": True, "legacy_library": False}
    assert client.get("/setup").status_code == 200
    assert client.get("/api/info").status_code == 503

    base = {"username": "admin", "display_name": "Admin", "password": "senha muito segura"}
    assert client.post("/api/setup", json={**base, "code": "AAAA-AAAA"}).status_code == 403
    assert client.post("/api/setup", json={**base, "code": code, "password": "curta"}).status_code == 422
    assert client.post("/api/setup", json={**base, "code": code, "username": "x"}).status_code == 422

    done = client.post("/api/setup", json={**base, "code": code.lower()})
    assert done.status_code == 201, done.text
    assert done.json()["user"]["is_admin"] is True

    # Signed in, no restart: the library answers and setup is closed for good.
    assert client.get("/api/info").status_code == 200
    assert client.get("/healthz").json()["status"] == "ok"
    assert not (data / "setup_code").exists()
    assert client.get("/api/setup").json() == {"required": False}
    assert client.post("/api/setup", json={**base, "code": code}).status_code == 409
    assert client.get("/setup", follow_redirects=False).status_code == 303
print("ok")
''')
    assert out.strip().endswith("ok")


def test_web_setup_moves_a_legacy_library_into_the_administrator(tmp_path: Path) -> None:
    out = _run(tmp_path, r'''
from pathlib import Path
from fastapi.testclient import TestClient
from core.indexer_db import init_db

data = Path("data")
data.mkdir()
init_db(data / "iris_v1.db").close()
media = Path("media")
media.mkdir()
(media / "foto.jpg").write_bytes(b"jpeg")

import server
with TestClient(server.app) as client:
    assert client.get("/api/setup").json()["legacy_library"] is True
    code = (data / "setup_code").read_text().strip()
    done = client.post("/api/setup", json={
        "code": code, "username": "admin", "password": "senha muito segura",
    })
    assert done.status_code == 201, done.text
    assert done.json()["migrated_legacy_library"] is True

user_root = data / "users" / str(done.json()["user"]["id"])
assert (user_root / "iris.db").is_file()
assert (user_root / "media" / "foto.jpg").read_bytes() == b"jpeg"
assert not (data / "iris_v1.db").exists()
print("ok")
''')
    assert out.strip().endswith("ok")


def test_wrong_codes_are_rate_limited(tmp_path: Path) -> None:
    out = _run(tmp_path, r'''
from fastapi.testclient import TestClient
import server

with TestClient(server.app) as client:
    body = {"code": "AAAA-AAAA", "username": "admin", "password": "senha muito segura"}
    statuses = [client.post("/api/setup", json=body).status_code for _ in range(11)]
    assert statuses[:10] == [403] * 10, statuses
    assert statuses[10] == 429, statuses
print("ok")
''')
    assert out.strip().endswith("ok")


def test_concurrent_submissions_create_a_single_administrator(tmp_path: Path) -> None:
    out = _run(tmp_path, r'''
import asyncio
from pathlib import Path
import httpx
import server
from core import first_setup
from core.users_db import list_users

data = Path("data")
code = first_setup.ensure_setup_code(data)

async def main():
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bodies = [{"code": code, "username": f"admin{i}", "password": "senha muito segura"} for i in range(5)]
        return await asyncio.gather(*(client.post("/api/setup", json=b) for b in bodies))

responses = asyncio.run(main())
statuses = sorted(r.status_code for r in responses)
assert statuses == [201, 409, 409, 409, 409], statuses
assert len(list_users(data / "users.db")) == 1
print("ok")
''')
    assert out.strip().endswith("ok")


def test_terminal_bootstrap_reprompts_a_short_password_instead_of_crashing(tmp_path: Path) -> None:
    args = ["--username", "admin"]
    script = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_admin.py"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run(
        [sys.executable, "-c", (
            "import getpass, runpy, sys\n"
            "answers = iter(['curta', 'senha muito segura', 'senha muito segura'])\n"
            "getpass.getpass = lambda prompt='': next(answers)\n"
            f"sys.argv = ['bootstrap_admin.py', *{args!r}]\n"
            f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
        )],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "pelo menos 12 caracteres" in result.stderr
    assert "Conta administradora criada: admin" in result.stdout
