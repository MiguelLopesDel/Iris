"""iris-sync, the desktop folder sync, against a real server.

It signs in as a device and sends what the library lacks over the phone's
resumable protocol; these tests pin what a user relies on: new photos arrive,
nothing is sent twice, a changed file is looked at again, and a revoked
session stops with a clear message instead of retrying forever.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

import iris_sync  # noqa: E402

PASSWORD = "synthetic password 1"


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
        IRIS_BACKEND_PRELOAD="0",
    )
    subprocess.run(
        [sys.executable, "-c", (
            "from pathlib import Path\n"
            "from core.auth import hash_password\n"
            "from core.indexer_db import init_db\n"
            "from core.users_db import create_user\n"
            f"ana = create_user(Path('data/users.db'), Path('data'), username='ana', is_admin=True,"
            f" password_hash=hash_password({PASSWORD!r}))\n"
            "init_db(ana.db_path).close()\n"
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


@pytest.fixture
def desktop(tmp_path: Path, monkeypatch):
    """This computer: its config and state live in the test's directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    photos = tmp_path / "Imagens"
    (photos / "Viagem").mkdir(parents=True)
    return photos


def _photo(path: Path, color: tuple[int, int, int], age_seconds: int = 3600) -> Path:
    Image.new("RGB", (16, 16), color).save(path)
    old = time.time() - age_seconds
    os.utime(path, (old, old))
    return path


def _library_count(data: Path) -> int:
    db = next((data / "users").glob("*/*.db"))
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0]


def _wait_for_library(data: Path, expected: int) -> None:
    for _ in range(100):
        if _library_count(data) == expected:
            return
        time.sleep(0.1)
    assert _library_count(data) == expected


def test_a_folder_reaches_the_library_once(server, desktop):
    base, data = server
    _photo(desktop / "IMG_0001.jpg", (200, 40, 40))
    _photo(desktop / "Viagem" / "IMG_0002.jpg", (40, 200, 40))
    _photo(desktop / "Viagem" / "IMG_0003.png", (40, 40, 200))
    (desktop / "notas.txt").write_text("not media")

    config = iris_sync.login(base, "ana", PASSWORD, "Computador de teste")
    config.folders = [str(desktop)]
    config.save()
    state = iris_sync.State()

    first = iris_sync.sync_once(iris_sync.Client(config), config, state, log=lambda _m: None)
    assert (first.sent, first.already, first.failed) == (3, 0, 0)
    _wait_for_library(data, 3)

    # Unchanged files are not even hashed again: nothing to check, nothing sent.
    second = iris_sync.sync_once(iris_sync.Client(config), config, state, log=lambda _m: None)
    assert (second.sent, second.already, second.failed) == (0, 0, 0)

    # A new photo goes; a copy of one already there is found by content.
    _photo(desktop / "Viagem" / "IMG_0004.jpg", (90, 90, 90))
    (desktop / "copia.jpg").write_bytes((desktop / "IMG_0001.jpg").read_bytes())
    os.utime(desktop / "copia.jpg", (time.time() - 3600,) * 2)
    third = iris_sync.sync_once(iris_sync.Client(config), config, state, log=lambda _m: None)
    assert (third.sent, third.already, third.failed) == (1, 1, 0)
    _wait_for_library(data, 4)


def test_a_file_still_being_written_waits_for_the_next_pass(server, desktop):
    base, _data = server
    _photo(desktop / "importando.jpg", (10, 10, 10), age_seconds=0)
    config = iris_sync.login(base, "ana", PASSWORD, "Computador de teste")
    config.folders = [str(desktop)]
    state = iris_sync.State()

    summary = iris_sync.sync_once(iris_sync.Client(config), config, state, log=lambda _m: None)

    assert (summary.sent, summary.waiting) == (0, 1)


def test_a_session_ended_on_the_server_stops_with_a_clear_message(server, desktop):
    base, _data = server
    _photo(desktop / "IMG_0001.jpg", (200, 40, 40))
    config = iris_sync.login(base, "ana", PASSWORD, "Computador de teste")
    config.folders = [str(desktop)]
    config.refresh_token = "revoked"  # as after "Sair" in Sistema → Dispositivos

    with pytest.raises(iris_sync.SyncError, match="iris-sync login"):
        iris_sync.sync_once(iris_sync.Client(config), config, iris_sync.State(), log=lambda _m: None)


def test_the_refresh_token_is_kept_only_readable_by_this_user(server, desktop):
    base, _data = server
    iris_sync.login(base, "ana", PASSWORD, "Computador de teste")

    mode = (iris_sync.config_dir() / "config.json").stat().st_mode & 0o777

    assert mode == 0o600
