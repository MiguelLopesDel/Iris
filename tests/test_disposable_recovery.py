"""A full temporary instance copy restores account and media boundaries.

This is a rehearsal of the backup contract, not a production backup command.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_two_account_instance_can_be_restored_from_consistent_copies(tmp_path: Path) -> None:
    script = r'''
import hashlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from PIL import Image

from core.auth import hash_password
from core.indexer_db import init_db
from core.shared_spaces import add_member, create_space
from core.space_catalog import add_item, space_root
from core.users_db import create_user

data = Path("data")
original_hashes = {}
for username, color in (("alice", (220, 40, 40)), ("bob", (40, 40, 220))):
    user = create_user(
        data / "users.db", data, username=username,
        password_hash=hash_password("synthetic recovery password"),
        is_admin=username == "alice",
    )
    image = user.media_root / f"{username}.jpg"
    Image.new("RGB", (32, 32), color).save(image)
    original_hashes[username] = hashlib.sha256(image.read_bytes()).hexdigest()
    init_db(user.db_path).close()
    with sqlite3.connect(user.db_path) as connection:
        connection.execute(
            "INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
            (image.name, str(image), b"\0" * 16),
        )

space = create_space(data / "users.db", 1, "Restored family")
add_member(data / "users.db", space.id, 1, "bob", "viewer")
# The shared item is Alice's photo, copied into the space's own store.
shared_item, _ = add_item(
    space_root(data, space.id), data / "users" / "1" / "media" / "alice.jpg", "alice.jpg", 1
)

import server  # creates data/secret_key, also part of the recovery unit

backup = Path("recovery_copy")
backup.mkdir()

def consistent_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as reader, sqlite3.connect(target) as writer:
        reader.backup(writer)

consistent_copy(data / "users.db", backup / "users.db")
shutil.copy2(data / "secret_key", backup / "secret_key")
for username, user_id in (("alice", 1), ("bob", 2)):
    library = data / "users" / str(user_id)
    copied = backup / "users" / str(user_id)
    consistent_copy(library / "iris.db", copied / "iris.db")
    shutil.copytree(library / "media", copied / "media")
    assert hashlib.sha256((copied / "media" / f"{username}.jpg").read_bytes()).hexdigest() == original_hashes[username]
space_dir = space_root(data, space.id)
consistent_copy(space_dir / "space.db", backup / "spaces" / str(space.id) / "space.db")
# Thumbnails are regenerable and deliberately left out.
shutil.copytree(space_dir / "media", backup / "spaces" / str(space.id) / "media")

# This move is confined to pytest's tmp_path. It simulates the original instance
# disappearing without deleting any of its bytes.
data.rename("offline_data")
data.mkdir()
shutil.copy2(backup / "secret_key", data / "secret_key")
consistent_copy(backup / "users.db", data / "users.db")
for user_id in (1, 2):
    copied = backup / "users" / str(user_id)
    library = data / "users" / str(user_id)
    consistent_copy(copied / "iris.db", library / "iris.db")
    shutil.copytree(copied / "media", library / "media")
restored_space = data / "spaces" / str(space.id)
consistent_copy(backup / "spaces" / str(space.id) / "space.db", restored_space / "space.db")
shutil.copytree(backup / "spaces" / str(space.id) / "media", restored_space / "media")

verify = f"SPACE_ID, ITEM_ID, ITEM_SHA = {space.id}, {shared_item.id}, {shared_item.sha256!r}\n" + r"""
from fastapi.testclient import TestClient

import server
from core.users_db import get_user_by_username
from pathlib import Path

with TestClient(server.app) as alice, TestClient(server.app) as bob:
    for client, username in ((alice, "alice"), (bob, "bob")):
        response = client.post("/api/auth/login", data={
            "username": username, "password": "synthetic recovery password"
        })
        assert response.status_code == 200, response.text
        records = client.get("/api/records")
        assert records.status_code == 200, records.text
        assert [record["arquivo"] for record in records.json()["records"]] == [f"{username}.jpg"]
        assert client.get(records.json()["records"][0]["thumbnail_url"]).status_code == 200
        spaces = client.get("/api/spaces").json()["spaces"]
        assert [(space["name"], space["role"]) for space in spaces] == [
            ("Restored family", "manager" if username == "alice" else "viewer")
        ]

    alice_file = get_user_by_username(Path("data/users.db"), "alice").media_root / "alice.jpg"
    bob_file = get_user_by_username(Path("data/users.db"), "bob").media_root / "bob.jpg"
    alice_url = "/media/" + str(alice_file).lstrip("/")
    bob_url = "/media/" + str(bob_file).lstrip("/")
    assert alice.get(alice_url).status_code == 200
    assert bob.get(bob_url).status_code == 200
    assert alice.get(bob_url).status_code == 404
    assert bob.get(alice_url).status_code == 404

    # The shared item comes back with its bytes; a thumbnail is rebuilt on demand.
    item_url = f"/api/spaces/{SPACE_ID}/items/{ITEM_ID}"
    import hashlib
    original = bob.get(item_url + "/original")
    assert original.status_code == 200, original.text
    assert hashlib.sha256(original.content).hexdigest() == ITEM_SHA
    assert bob.get(item_url + "/thumbnail").status_code == 200
    assert bob.get(f"/api/spaces/{SPACE_ID}/storage").json()["used_bytes"] == len(original.content)
"""
result = subprocess.run([sys.executable, "-c", verify], text=True, capture_output=True)
assert result.returncode == 0, result.stdout + result.stderr
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_LOAD_MODEL="0",
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
