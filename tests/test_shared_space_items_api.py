"""Shared-space items end to end: roles, isolation and independence from the private copy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_space_items_access_matrix(tmp_path: Path) -> None:
    script = r'''
import hashlib
import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user

PASSWORD = "synthetic shared password"
data = Path("data")
private = {}
# root administers the instance and is never invited; dave is never invited.
for username, count, is_admin in (
    ("root", 1, True), ("alice", 3, False), ("bob", 2, False),
    ("carol", 1, False), ("dave", 1, False),
):
    user = create_user(
        data / "users.db", data, username=username,
        password_hash=hash_password(PASSWORD), is_admin=is_admin,
    )
    init_db(user.db_path).close()
    files = []
    for n in range(count):
        image = user.media_root / f"{username}-{n}.jpg"
        seed = sum(map(ord, username)) + n * 40
        Image.new("RGB", (48, 32), (seed % 256, (seed * 3) % 256, (seed * 7) % 256)).save(image)
        with sqlite3.connect(user.db_path) as conn:
            conn.execute(
                "INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
                (image.name, str(image), b"\0" * 16),
            )
        files.append(image)
    private[username] = files

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

import server

trash = Path("trash")
trash.mkdir()

def fake_trash(paths):
    # Stands in for the system trash: the private original leaves the library.
    moved = []
    for raw in paths:
        os.replace(raw, trash / Path(raw).name)
        moved.append(str(Path(raw).resolve()))
    return moved, []

server.move_to_trash = fake_trash

clients = {name: TestClient(server.app) for name in ("root", "alice", "bob", "carol", "dave")}
for client in clients.values():
    client.__enter__()
root, alice, bob, carol, dave = (clients[n] for n in ("root", "alice", "bob", "carol", "dave"))
try:
    anonymous = TestClient(server.app)
    assert anonymous.get("/api/spaces/1/items").status_code == 401
    for name, client in clients.items():
        assert client.post(
            "/api/auth/login", data={"username": name, "password": PASSWORD}
        ).status_code == 200

    space_id = alice.post("/api/spaces", json={"name": "Family"}).json()["space"]["id"]
    other_id = alice.post("/api/spaces", json={"name": "Alice only"}).json()["space"]["id"]
    url = f"/api/spaces/{space_id}"
    assert alice.post(url + "/members", json={"username": "bob", "role": "contributor"}).status_code == 201
    assert alice.post(url + "/members", json={"username": "carol", "role": "viewer"}).status_code == 201

    # Contributor adds from their own library; the same bytes again are not new.
    added = bob.post(url + "/items", json={"record_id": 1})
    assert added.status_code == 201, added.text
    bob_item = added.json()["item"]
    assert added.json()["created"] is True
    assert bob_item["name"] == "bob-0.jpg" and bob_item["added_by_username"] == "bob"
    assert bob_item["sha256"] == sha(private["bob"][0])
    again = bob.post(url + "/items", json={"record_id": 1})
    assert again.status_code == 200 and again.json()["item"]["id"] == bob_item["id"]

    # A private id is resolved only in the caller's library: id 3 exists in
    # Alice's, not in Bob's, and id 1 is Bob's own photo, never Alice's.
    assert bob.post(url + "/items", json={"record_id": 3}).status_code == 404
    assert bob.post(url + "/items", json={"record_id": 999}).status_code == 404
    assert bob.post(url + "/items", json={"record_id": 0}).status_code == 422

    # Viewer: reads, never writes.
    assert carol.post(url + "/items", json={"record_id": 1}).status_code == 403
    page = carol.get(url + "/items").json()
    assert [item["id"] for item in page["items"]] == [bob_item["id"]]
    assert page["items"][0]["can_remove"] is False
    thumb = carol.get(bob_item["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"
    original = carol.get(bob_item["original_url"])
    assert original.status_code == 200
    assert hashlib.sha256(original.content).hexdigest() == bob_item["sha256"]
    assert carol.get(url + f"/items/{bob_item['id']}").json()["item"]["name"] == "bob-0.jpg"

    # Outsiders -- including the instance administrator -- see nothing, and a
    # missing space looks the same as a forbidden one.
    item_url = url + f"/items/{bob_item['id']}"
    for outsider in (root, dave):
        for path in (url + "/items", item_url, item_url + "/thumbnail", item_url + "/original"):
            assert outsider.get(path).status_code == 404, path
        assert outsider.post(url + "/items", json={"record_id": 1}).status_code == 404
        assert outsider.delete(item_url).status_code == 404
    assert carol.get("/api/spaces/9999/items").status_code == 404

    # An item id does not cross into another space, even for its manager.
    other_item = f"/api/spaces/{other_id}/items/{bob_item['id']}"
    assert alice.get(other_item).status_code == 404
    assert alice.get(other_item + "/original").status_code == 404
    assert bob.get(other_item + "/original").status_code == 404

    # Pagination: newest first, cursor by item id.
    second = bob.post(url + "/items", json={"record_id": 2}).json()["item"]
    alice_item = alice.post(url + "/items", json={"record_id": 3}).json()["item"]
    assert alice_item["sha256"] == sha(private["alice"][2])
    first_page = carol.get(url + "/items?limit=2").json()
    assert [i["id"] for i in first_page["items"]] == [alice_item["id"], second["id"]]
    rest = carol.get(url + f"/items?limit=2&before={first_page['next_before']}").json()
    assert [i["id"] for i in rest["items"]] == [bob_item["id"]]
    assert rest["next_before"] is None

    # Bob trashes his private original: the shared copy stays intact.
    bob_private_media = "/media/" + str(private["bob"][0]).lstrip("/")
    assert bob.post("/api/trash", data={"db_ids": "1"}).json()["moved"] == 1
    assert bob.get(bob_private_media).status_code == 404
    kept = carol.get(bob_item["original_url"])
    assert kept.status_code == 200
    assert hashlib.sha256(kept.content).hexdigest() == bob_item["sha256"]

    # Device tokens pass through the same membership gate.
    token = carol.post("/api/auth/devices/login", data={
        "username": "carol", "password": PASSWORD,
        "device_name": "Carol phone", "platform": "android",
    }).json()["access_token"]
    device = TestClient(server.app)
    headers = {"Authorization": "Bearer " + token}
    assert device.get(url + "/items", headers=headers).status_code == 200
    assert device.get(bob_item["original_url"], headers=headers).status_code == 200
    assert device.delete(item_url, headers=headers).status_code == 403

    # Removal: viewer never, contributor only own, manager any.
    alice_item_url = url + f"/items/{alice_item['id']}"
    assert carol.delete(item_url).status_code == 403
    assert bob.delete(alice_item_url).status_code == 403
    assert bob.delete(item_url).status_code == 204
    assert carol.get(item_url).status_code == 404
    assert carol.get(item_url + "/original").status_code == 404
    assert alice.delete(url + f"/items/{second['id']}").status_code == 204
    assert alice.delete(alice_item_url).status_code == 204
    assert carol.get(url + "/items").json()["items"] == []

    # Removal hides; it does not erase the stored bytes.
    stored = [p for p in (data / "spaces" / str(space_id) / "media").rglob("*") if p.is_file()]
    assert len(stored) == 3
    # Alice's private copy was never touched by removing it from the space.
    assert private["alice"][2].exists()
finally:
    for client in clients.values():
        client.__exit__(None, None, None)
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
