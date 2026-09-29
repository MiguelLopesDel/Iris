"""Albums and search inside a shared space, over HTTP, per role."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_space_search_and_albums_follow_roles(tmp_path: Path) -> None:
    script = r'''
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user

PASSWORD = "synthetic shared password"
data = Path("data")
descriptions = {"alice": "cachorro correndo na praia", "bob": "bolo de aniversário"}
for name in ("root", "alice", "bob", "carol", "dave"):
    user = create_user(data / "users.db", data, username=name,
                       password_hash=hash_password(PASSWORD), is_admin=name == "root")
    init_db(user.db_path).close()
    image = user.media_root / f"{name}.jpg"
    Image.new("RGB", (16, 16), (len(name) * 40, 90, 120)).save(image)
    with sqlite3.connect(user.db_path) as conn:
        conn.execute(
            "INSERT INTO memes (arquivo, caminho, embedding, descricao_ia) VALUES (?, ?, ?, ?)",
            (image.name, str(image), b"\0" * 16, descriptions.get(name, "")),
        )

import server

clients = {}
for name in ("root", "alice", "bob", "carol", "dave"):
    client = TestClient(server.app)
    client.__enter__()
    client.post("/api/auth/login", data={"username": name, "password": PASSWORD})
    clients[name] = client
root, alice, bob, carol, dave = (clients[n] for n in ("root", "alice", "bob", "carol", "dave"))

space = alice.post("/api/spaces", json={"name": "Família"}).json()["space"]["id"]
url = f"/api/spaces/{space}"
alice.post(url + "/members", json={"username": "bob", "role": "contributor"})
alice.post(url + "/members", json={"username": "carol", "role": "viewer"})
dog = alice.post(url + "/items", json={"record_id": 1}).json()["item"]
cake = bob.post(url + "/items", json={"record_id": 1}).json()["item"]

# Search uses the description each photo brought from its author's library.
found = carol.get(url + "/search", params={"q": "Cachorro"}).json()
assert [i["id"] for i in found["items"]] == [dog["id"]]
assert found["semantic"] is False  # no model loaded here: text only
assert [i["id"] for i in carol.get(url + "/search", params={"q": "aniversario"}).json()["items"]] == [cake["id"]]
assert carol.get(url + "/search", params={"q": "neve"}).json()["items"] == []
for outsider in (root, dave):
    assert outsider.get(url + "/search", params={"q": "praia"}).status_code == 404

# Albums: contributors and managers build them; viewers only look.
assert carol.post(url + "/albums", json={"name": "Carol"}).status_code == 403
made = bob.post(url + "/albums", json={"name": "Férias"})
assert made.status_code == 201, made.text
album = made.json()["album"]
assert album["can_edit"] is True and album["count"] == 0
album_url = f"{url}/albums/{album['id']}"
assert bob.post(album_url + "/items", json={"item_ids": [dog["id"], cake["id"]]}).json() == {"added": 2}
assert bob.post(album_url + "/items", json={"item_ids": [99999]}).status_code == 404
assert carol.post(album_url + "/items", json={"item_ids": [dog["id"]]}).status_code == 403

listing = carol.get(url + "/albums").json()
assert listing["can_create"] is False
[seen] = listing["albums"]
assert (seen["name"], seen["count"], seen["can_edit"]) == ("Férias", 2, False)
assert carol.get(seen["cover_url"]).status_code == 200
assert [i["id"] for i in carol.get(album_url + "/items").json()["items"]] == [cake["id"], dog["id"]]
for outsider in (root, dave):
    assert outsider.get(url + "/albums").status_code == 404
    assert outsider.get(album_url + "/items").status_code == 404

# Alice (manager) renames Bob's album; Carol cannot.
assert carol.patch(album_url, json={"name": "X"}).status_code == 403
assert alice.patch(album_url, json={"name": "Praia 2026"}).json()["album"]["name"] == "Praia 2026"

# A photo removed from the space leaves the album view too.
assert alice.delete(f"{url}/items/{dog['id']}").status_code == 204
assert [i["id"] for i in carol.get(album_url + "/items").json()["items"]] == [cake["id"]]
assert bob.delete(f"{album_url}/items/{cake['id']}").status_code == 204
assert carol.get(album_url + "/items").json()["items"] == []

# Deleting the album keeps the photos in the space.
assert carol.delete(album_url).status_code == 403
assert bob.delete(album_url).status_code == 204
assert carol.get(url + "/albums").json()["albums"] == []
assert [i["id"] for i in carol.get(url + "/items").json()["items"]] == [cake["id"]]
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
