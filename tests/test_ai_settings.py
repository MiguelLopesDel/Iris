"""Who processes what: the instance's choice of where AI runs and each account's consent."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core import ai_policy, instance_settings
from core.users_db import create_user, set_user_ai_enabled, user_ai_enabled

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

import iris_ai_worker  # noqa: E402
import iris_sync  # noqa: E402

MODEL = "sentence-transformers/clip-ViT-L-14"
PASSWORD = "synthetic password 1"


@pytest.fixture
def users_db(tmp_path: Path) -> Path:
    path = tmp_path / "users.db"
    create_user(path, tmp_path, username="alice", password_hash="x", is_admin=True)
    return path


def _alice(path: Path):
    return SimpleNamespace(id=1)


def test_accounts_consent_by_default_and_can_opt_out(users_db):
    assert user_ai_enabled(users_db, 1) is True

    set_user_ai_enabled(users_db, 1, False)

    assert user_ai_enabled(users_db, 1) is False


@pytest.mark.parametrize(
    ("mode", "consent", "server", "workers"),
    [
        ("server", True, True, False),
        ("worker", True, False, True),
        ("off", True, False, False),
        ("server", False, False, False),
        ("worker", False, False, False),
    ],
)
def test_where_ai_runs_follows_the_instance_and_the_owner(users_db, mode, consent, server, workers):
    instance_settings.save(users_db, {"ai_processing": mode}, actor_id=1)
    set_user_ai_enabled(users_db, 1, consent)

    assert ai_policy.on_server(users_db, _alice(users_db), load_model=True) is server
    assert ai_policy.for_workers(users_db, _alice(users_db)) is workers


def test_without_models_loaded_the_server_never_processes(users_db):
    instance_settings.save(users_db, {"ai_processing": "server"}, actor_id=1)

    assert ai_policy.on_server(users_db, _alice(users_db), load_model=False) is False


def test_only_known_models_can_be_moved_to():
    with pytest.raises(instance_settings.SettingError):
        instance_settings.parse("ai_model_next", "someone/any-model")
    assert instance_settings.parse("ai_model_next", "") == ""


# ── Round trip against a real server ────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    home = tmp_path / "server"
    home.mkdir()
    env = dict(
        os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false", IRIS_LOAD_MODEL="0", IRIS_THUMBNAIL_WARMUP="0",
        IRIS_BACKEND_PRELOAD="0", IRIS_MODEL=MODEL, IRIS_AI_PROCESSING="worker",
    )
    subprocess.run(
        [sys.executable, "-c", (
            "import hashlib, sqlite3\n"
            "from pathlib import Path\n"
            "from PIL import Image\n"
            "from core.auth import hash_password\n"
            "from core.indexer_db import init_db\n"
            "from core.users_db import create_user\n"
            "for name, admin in (('ana', True), ('bob', False)):\n"
            f"    user = create_user(Path('data/users.db'), Path('data'), username=name, is_admin=admin,"
            f" password_hash=hash_password({PASSWORD!r}))\n"
            "    init_db(user.db_path).close()\n"
            "    user.media_root.mkdir(parents=True, exist_ok=True)\n"
            "    with sqlite3.connect(user.db_path) as conn:\n"
            "        for i in range(2):\n"
            "            path = user.media_root / f'{name}_{i}.jpg'\n"
            "            Image.new('RGB', (16, 16), (i * 90, 80, 160 if admin else 20)).save(path)\n"
            "            digest = hashlib.sha256(path.read_bytes()).hexdigest()\n"
            "            conn.execute('INSERT INTO memes (arquivo, caminho, file_mtime, content_hash)'\n"
            "                         ' VALUES (?, ?, ?, ?)', (path.name, str(path), 1.7e9 + i, digest))\n"
        )],
        cwd=home, env=env, check=True, timeout=60,
    )
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--app-dir", str(ROOT),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=home, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")
        yield base, home / "data"
    finally:
        process.terminate()
        process.wait(timeout=20)


def _result(seed: int) -> dict:
    vector = np.random.default_rng(seed).standard_normal(768).astype(np.float32)
    return {"model_name": MODEL, "embedding_dim": 768,
            "embedding": base64.b64encode(vector.tobytes()).decode(), "texto_extraido": "lido"}


def _pipeline(seen: list[str]):
    def process(files, model_name, _workdir):
        assert model_name == MODEL
        out = {}
        for path in files:
            seen.append(hashlib.sha256(path.read_bytes()).hexdigest())
            out[path.name] = _result(len(seen))
        return out
    return process


def _client(base: str, tmp_path: Path, monkeypatch, username: str) -> iris_sync.Client:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / f"config-{username}"))
    config = iris_sync.login(base, username, PASSWORD, f"PC de {username}",
                             iris_ai_worker.WorkerConfig, platform="ai-worker")
    return iris_sync.Client(config)


def _processed(data: Path, username_index: int) -> int:
    db = sorted((data / "users").glob("*/*.db"))[username_index]
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM memes WHERE embedding IS NOT NULL").fetchone()[0]


def test_an_administrators_worker_serves_every_account_that_consents(server, tmp_path, monkeypatch):
    base, data = server
    bob = _client(base, tmp_path, monkeypatch, "bob")
    status, body = bob.request("PUT", "/api/me/ai", json_body={"enabled": False})
    assert (status, body["enabled"]) == (200, False)
    ana = _client(base, tmp_path, monkeypatch, "ana")

    iris_ai_worker.work_once(ana, process=_pipeline([]), log=lambda _m: None)
    assert (_processed(data, 0), _processed(data, 1)) == (2, 0)  # bob said no

    bob.request("PUT", "/api/me/ai", json_body={"enabled": True})
    iris_ai_worker.work_once(ana, process=_pipeline([]), log=lambda _m: None)
    assert _processed(data, 1) == 2

    # The administrator sees processing computers and counts per account.
    status, body = ana.request("GET", "/api/admin/ai")
    assert status == 200 and body["mode"] == "worker"
    assert [w["done"] for w in body["workers"] if w["account"] == "ana"] == [4]
    assert {a["username"]: a["without_ai"] for a in body["accounts"]} == {"ana": 0, "bob": 0}


def test_an_accounts_own_worker_never_touches_other_libraries(server, tmp_path, monkeypatch):
    base, data = server
    bob = _client(base, tmp_path, monkeypatch, "bob")

    status, body = bob.request("POST", "/api/ai-jobs/claim?limit=10")

    assert status == 200
    assert {job["id"].split(".")[0] for job in body["jobs"]} == {"2"}
    status, _ = bob.request("GET", "/api/ai-jobs/1.1/file")
    assert status == 404


def test_with_ai_on_the_server_workers_get_no_work(server, tmp_path, monkeypatch):
    base, _data = server
    ana = _client(base, tmp_path, monkeypatch, "ana")
    status, _ = ana.request("PUT", "/api/admin/settings", json_body={"ai_processing": "server"})
    assert status == 200

    status, body = ana.request("POST", "/api/ai-jobs/claim?limit=10")

    assert (status, body["jobs"]) == (200, [])


def test_forgetting_erases_what_ai_derived_and_keeps_the_photos(server, tmp_path, monkeypatch):
    base, data = server
    ana = _client(base, tmp_path, monkeypatch, "ana")
    iris_ai_worker.work_once(ana, process=_pipeline([]), log=lambda _m: None)
    assert _processed(data, 0) == 2

    status, body = ana.request("POST", "/api/me/ai/forget")

    assert (status, body["forgotten"]) == (200, 2)
    db = sorted((data / "users").glob("*/*.db"))[0]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*), SUM(texto_extraido != '') FROM memes").fetchone() == (2, 0)
    assert _processed(data, 0) == 0
