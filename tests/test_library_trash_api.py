"""The private library's trash over HTTP, with two real accounts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_trash_hides_restores_and_stays_private(tmp_path: Path) -> None:
    script = r'''
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user

PASSWORD = "synthetic trash password"
data = Path("data")
files = {}
for name in ("alice", "bob"):
    user = create_user(data / "users.db", data, username=name,
                       password_hash=hash_password(PASSWORD), is_admin=name == "alice")
    init_db(user.db_path).close()
    with sqlite3.connect(user.db_path) as conn:
        for n in (1, 2):
            image = user.media_root / f"{name}-{n}.jpg"
            Image.new("RGB", (32, 24), (n * 90, 40, 160)).save(image)
            conn.execute(
                "INSERT INTO memes (arquivo, caminho, embedding, content_hash) VALUES (?, ?, ?, ?)",
                (image.name, str(image), b"\0" * 16, f"{name}-hash-{n}"),
            )
            files[(name, n)] = image

import server

with TestClient(server.app) as alice, TestClient(server.app) as bob:
    for name, client in (("alice", alice), ("bob", bob)):
        assert client.post("/api/auth/login", data={"username": name, "password": PASSWORD}).status_code == 200

    def names(client):
        return sorted(r["arquivo"] for r in client.get("/api/records").json()["records"])

    assert names(alice) == ["alice-1.jpg", "alice-2.jpg"]
    empty = alice.get("/api/trash/items").json()
    assert empty["items"] == [] and empty["trash_days"] == 30

    moved = alice.post("/api/trash", data={"db_ids": "1"})
    assert moved.status_code == 200 and moved.json() == {"moved": 1, "failed": 0}, moved.text
    assert names(alice) == ["alice-2.jpg"]  # gone from the library at once
    assert not files[("alice", 1)].exists()

    [item] = alice.get("/api/trash/items").json()["items"]
    assert item["id"] == 1 and item["name"] == "alice-1.jpg"
    assert item["purge_after"] > item["trashed_at"]
    thumb = alice.get(item["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"

    # Bob sees none of it, and his own item 1 is untouched.
    assert bob.get("/api/trash/items").json()["items"] == []
    assert bob.get(item["thumbnail_url"]).status_code == 404
    denied = bob.post("/api/trash/restore", json={"ids": [1]}).json()
    assert denied["restored"] == [] and denied["missing"] == [1]
    assert names(bob) == ["bob-1.jpg", "bob-2.jpg"]

    restored = alice.post("/api/trash/restore", json={"ids": [1]})
    assert restored.status_code == 200 and restored.json()["restored"] == [1], restored.text
    assert names(alice) == ["alice-1.jpg", "alice-2.jpg"]
    assert files[("alice", 1)].exists()
    assert alice.get("/api/trash/items").json()["items"] == []

    # The retention is an instance setting the administrator can change.
    assert alice.put("/api/admin/settings", json={"library_trash_days": 7}).status_code == 200
    assert alice.get("/api/trash/items").json()["trash_days"] == 7
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_LOAD_MODEL="0",
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_hourly_sweep_empties_both_trashes_that_nobody_opens(tmp_path: Path) -> None:
    script = r'''
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import library_trash, space_catalog
from core.indexer_db import init_db
from core.shared_spaces import create_space
from core.users_db import create_user

data = Path("data")
user = create_user(data / "users.db", data, username="ana", password_hash="x")
init_db(user.db_path).close()
photo = user.media_root / "old.jpg"
photo.write_bytes(b"old photo")
with sqlite3.connect(user.db_path) as conn:
    conn.execute("INSERT INTO memes (id, arquivo, caminho, embedding) VALUES (1, 'old.jpg', ?, ?)",
                 (str(photo), b"\0" * 16))
long_ago = datetime.now(timezone.utc) - timedelta(days=40)
library_trash.move_to_trash(user.db_path, {1: photo}, now=long_ago)
held = library_trash.trashed_file(user.db_path, 1)

space = create_space(data / "users.db", user.id, "Família")
root = space_catalog.space_root(data, space.id)
copy = user.media_root / "shared.jpg"
copy.write_bytes(b"shared photo")
item, _ = space_catalog.add_item(root, copy, "shared.jpg", user.id)
space_catalog.remove_item(root, item.id, user.id, "manager")
with sqlite3.connect(root / "space.db") as conn:
    conn.execute("UPDATE items SET removed_at = ?", (long_ago.isoformat(),))

import server

server._sweep_trash()
assert not held.exists(), "the private trash was not emptied"
assert library_trash.list_trash(user.db_path, 30) == []
assert space_catalog.list_trash(root, user.id, "manager", 10) == []
assert list((root / "media").rglob("*.jpg")) == []
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_LOAD_MODEL="0",
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
