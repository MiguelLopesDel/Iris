"""Attaching a legacy catalog, copied to the server, to an existing empty account."""
from __future__ import annotations

import errno
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core import first_setup
from core.auth import hash_password
from core.first_setup import MigrationFailed, SetupError, find_legacy_library
from core.indexer_db import init_db
from core.instance_lock import (
    LOCK_NAME,
    exclusive_maintenance,
    hold_for_server,
    release,
    server_running,
)
from core.library_attach import attach_legacy_library
from core.users_db import create_user

ROOT = Path(__file__).resolve().parents[1]
STALE_ROOT = "/home/someone/Projetos/Meme_Compass/data/library/default"
MODEL = "sentence-transformers/clip-ViT-L-14"


def _server_with_empty_account(data: Path):
    user = create_user(data / "users.db", data, username="ana", is_admin=True,
                       password_hash=hash_password("synthetic password 1"))
    init_db(user.db_path).close()  # what setup without a legacy library leaves
    return user


def _copied_legacy(source: Path, *, model: str = MODEL) -> None:
    """The old project's data as rsync leaves it in data/import on the server."""
    source.mkdir(parents=True)
    conn = init_db(source / "meme_compass_full_v1.db")
    conn.execute(
        "INSERT INTO media_libraries (id, name, root_path, created_at) VALUES (1, 'default', ?, '')",
        (STALE_ROOT,),
    )
    library = source / "library" / "default"
    (library / "2024").mkdir(parents=True)
    (library / "2024" / "clip.mp4").write_bytes(b"video")
    (library / "foto.jpg").write_bytes(b"library photo")
    (source / "media").mkdir()
    (source / "media" / "solta.jpg").write_bytes(b"media photo")
    for caminho, relative, storage, library_id in (
        ("/old/clip.mp4", None, "2024/clip.mp4", 1),
        ("/old/foto.jpg", None, "foto.jpg", 1),
        ("/old/media/solta.jpg", "solta.jpg", None, None),
        ("/old/lost.jpg", None, "lost.jpg", 1),
    ):
        conn.execute(
            "INSERT INTO memes (arquivo, caminho, relative_path, storage_path, library_id, model_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (Path(caminho).name, caminho, relative, storage, library_id, model),
        )
    conn.commit()
    conn.close()
    (source / "meme_compass_full_v1.embedding.vec").write_bytes(b"vectors")


def _opened(db: Path, media_root: Path) -> list[bytes | None]:
    from core.search_engine import IrisEngine

    engine = IrisEngine(db_path=db, media_root=media_root, load_model=False)
    rows = sqlite3.connect(db).execute(
        "SELECT caminho, relative_path, storage_path, library_id FROM memes ORDER BY id"
    ).fetchall()
    out = []
    for caminho, relative, storage, library_id in rows:
        path = Path(engine.resolve_media_path(caminho, relative, storage_path=storage, library_id=library_id))
        out.append(path.read_bytes() if path.is_file() else None)
    return out


def _legacy(source: Path):
    return find_legacy_library(source, source / "media", source / "meme_compass_full_v1.db")


def _tree(root: Path) -> dict[str, bytes]:
    # Not compared: the account registry (only read), SQLite's -wal/-shm, which
    # any read leaves behind, and the server lock file.
    skipped = ("users.db", "-wal", "-shm", LOCK_NAME)
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith(skipped)
    }


def test_the_command_attaches_the_copied_library_and_every_item_opens(tmp_path: Path) -> None:
    data = tmp_path / "data"
    user = _server_with_empty_account(data)
    _copied_legacy(data / "import")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "attach_library.py"), "--user", "ana", "--from", "data/import"],
        cwd=tmp_path, input="s\n", text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)}, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 itens: 3 serão movidos para a conta 'ana', 1 já não têm arquivo" in result.stdout

    assert _opened(user.db_path, user.media_root) == [b"video", b"library photo", b"media photo", None]
    with sqlite3.connect(user.db_path) as conn:
        assert conn.execute("SELECT root_path FROM media_libraries").fetchone()[0] == str(
            user.media_root / "library" / "default"
        )
    assert (user.db_path.parent / "iris.embedding.vec").read_bytes() == b"vectors"
    # The account's empty catalog is kept aside, not deleted; the copy is consumed.
    (aside,) = user.db_path.parent.glob("replaced-*")
    assert (aside / "iris.db").is_file()
    assert not (data / "import" / "meme_compass_full_v1.db").exists()
    assert not any(p.is_file() for p in (data / "import").rglob("*"))


def test_declining_the_confirmation_changes_nothing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    user = _server_with_empty_account(data)
    _copied_legacy(data / "import")
    before = _tree(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "attach_library.py"), "--user", "ana", "--from", "data/import"],
        cwd=tmp_path, input="n\n", text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)}, check=False,
    )
    assert result.returncode == 1 and "Nada foi alterado" in result.stdout
    assert _tree(tmp_path) == before
    assert user.db_path.is_file()


@pytest.mark.parametrize("occupied", ["items", "media", "open", "model"])
def test_refuses_a_running_server_and_anything_but_an_empty_account_with_the_same_model(tmp_path: Path, occupied: str) -> None:
    data = tmp_path / "data"
    user = _server_with_empty_account(data)
    _copied_legacy(data / "import", model="google/siglip2-base-patch16-224" if occupied == "model" else MODEL)
    holder = None
    if occupied == "items":
        with sqlite3.connect(user.db_path) as conn:
            conn.execute("INSERT INTO memes (arquivo, caminho) VALUES ('phone.jpg', '/x/phone.jpg')")
    elif occupied == "media":
        (user.media_root / "uploads").mkdir()
        (user.media_root / "uploads" / "phone.jpg").write_bytes(b"phone")
    elif occupied == "open":
        holder = hold_for_server(data)  # a running server
    before = _tree(data)

    with pytest.raises(SetupError) as refused:
        attach_legacy_library(data, user, _legacy(data / "import"), data / "import")
    expected = {"items": "já tem 1 itens", "media": "não está vazia", "open": "está rodando",
                "model": "a busca não funcionaria"}[occupied]
    assert expected in str(refused.value)
    if holder is not None:
        release(holder)
    assert _tree(data) == before


def test_a_failure_midway_puts_the_account_and_the_copy_back(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    user = _server_with_empty_account(data)
    _copied_legacy(data / "import")
    before = _tree(data)
    real_move = first_setup._move

    def library_disk_fails(source, destination, *args, **kwargs):
        if Path(source).name == "default":
            raise OSError(errno.EIO, "Input/output error")
        return real_move(source, destination, *args, **kwargs)

    monkeypatch.setattr(first_setup, "_move", library_disk_fails)
    with pytest.raises(MigrationFailed) as failed:
        attach_legacy_library(data, user, _legacy(data / "import"), data / "import")
    assert failed.value.rolled_back and "a conta continua vazia" in str(failed.value)
    assert _tree(data) == before
    assert not list(user.db_path.parent.glob("replaced-*"))
    with sqlite3.connect(data / "import" / "meme_compass_full_v1.db") as conn:
        assert conn.execute("SELECT root_path FROM media_libraries").fetchone()[0] == STALE_ROOT


def test_a_server_started_during_maintenance_waits_for_it(tmp_path: Path) -> None:
    import threading
    import time

    assert not server_running(tmp_path)
    started = threading.Event()
    waited = {}

    def server_starts():
        started.set()
        began = time.monotonic()
        handle = hold_for_server(tmp_path)
        waited["seconds"] = time.monotonic() - began
        release(handle)

    with exclusive_maintenance(tmp_path):
        thread = threading.Thread(target=server_starts)
        thread.start()
        started.wait()
        time.sleep(0.5)
        assert thread.is_alive(), "the server must not start while maintenance runs"
    thread.join(timeout=5)
    assert waited["seconds"] >= 0.4

    handle = hold_for_server(tmp_path)
    assert server_running(tmp_path)
    release(handle)
    assert not server_running(tmp_path)


def test_the_running_server_holds_the_data_folder(tmp_path: Path) -> None:
    script = r"""
from pathlib import Path
from fastapi.testclient import TestClient
from core.instance_lock import server_running
import server

data = Path("data")
assert not server_running(data)
with TestClient(server.app):
    assert server_running(data)
assert not server_running(data)
print("ok")
"""
    env = dict(
        os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false", IRIS_LOAD_MODEL="0",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("ok")
