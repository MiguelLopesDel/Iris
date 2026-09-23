"""Shared-space storage policy over HTTP: trash, restore, quota, saving a copy, config."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SETUP = r'''
import hashlib
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
for username, colour in (("alice", (200, 30, 30)), ("bob", (30, 200, 30)),
                         ("carol", (30, 30, 200)), ("dave", (90, 90, 90))):
    user = create_user(data / "users.db", data, username=username,
                       password_hash=hash_password(PASSWORD))
    init_db(user.db_path).close()
    image = user.media_root / f"{username}.jpg"
    Image.new("RGB", (40, 30), colour).save(image)
    with sqlite3.connect(user.db_path) as conn:
        conn.execute("INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
                     (image.name, str(image), b"\0" * 16))
    private[username] = (user, image)

create_user(data / "users.db", data, username="root",
            password_hash=hash_password(PASSWORD), is_admin=True)

import server

def login(name):
    client = TestClient(server.app)
    client.__enter__()
    assert client.post("/api/auth/login", data={"username": name, "password": PASSWORD}).status_code == 200
    return client
'''


def _run(tmp_path: Path, body: str, **env_overrides: str) -> subprocess.CompletedProcess:
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_LOAD_MODEL="0",
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
        **env_overrides,
    )
    return subprocess.run(
        [sys.executable, "-c", _SETUP + body],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_trash_restore_and_saving_a_copy(tmp_path: Path) -> None:
    body = r'''
alice, bob, carol, dave = (login(n) for n in ("alice", "bob", "carol", "dave"))
space = alice.post("/api/spaces", json={"name": "Family"}).json()["space"]["id"]
url = f"/api/spaces/{space}"
alice.post(url + "/members", json={"username": "bob", "role": "contributor"})
alice.post(url + "/members", json={"username": "carol", "role": "viewer"})

storage = carol.get(url + "/storage").json()
assert storage == {"used_bytes": 0, "quota_bytes": 10995116277760, "trash_days": 30}
assert dave.get(url + "/storage").status_code == 404

bob_item = bob.post(url + "/items", json={"record_id": 1}).json()["item"]
alice_item = alice.post(url + "/items", json={"record_id": 1}).json()["item"]
used = carol.get(url + "/storage").json()["used_bytes"]
assert used == bob_item["size_bytes"] + alice_item["size_bytes"]

# Trash: removal hides, the trash shows it with its purge date, by role.
assert alice.delete(url + f"/items/{bob_item['id']}").status_code == 204
assert bob.delete(url + f"/items/{alice_item['id']}").status_code == 403
trash = alice.get(url + "/trash").json()["items"]
assert [i["id"] for i in trash] == [bob_item["id"]]
assert trash[0]["purge_after"] > trash[0]["removed_at"]
assert "original_url" not in trash[0]
assert [i["id"] for i in bob.get(url + "/trash").json()["items"]] == [bob_item["id"]]
assert carol.get(url + "/trash").json()["items"] == []
assert dave.get(url + "/trash").status_code == 404
assert carol.get(url + "/storage").json()["used_bytes"] == used  # trash still counts

# Restore: author or manager; viewers and outsiders cannot.
restore = url + f"/trash/{bob_item['id']}/restore"
assert carol.post(restore).status_code == 403
assert dave.post(restore).status_code == 404
restored = bob.post(restore)
assert restored.status_code == 200, restored.text
assert restored.json()["item"]["original_url"].endswith("/original")
assert bob.post(restore).status_code == 404
assert alice.get(url + "/trash").json()["items"] == []

# A viewer keeps a copy in their own library; it enters the processing queue
# like a device upload, and saving again is not a second copy.
save = url + f"/items/{bob_item['id']}/save"
assert dave.post(save).status_code == 404
saved = carol.post(save)
assert saved.status_code == 201, saved.text
assert saved.json()["state"] == "pending_processing"
carol_user, _ = private["carol"]
copies = list((carol_user.media_root / "shared").rglob("*.jpg"))
assert len(copies) == 1
assert hashlib.sha256(copies[0].read_bytes()).hexdigest() == bob_item["sha256"]
again = carol.post(save)
assert again.status_code == 200 and again.json()["upload_id"] == saved.json()["upload_id"]
changes = carol.get("/api/sync/changes").json()["changes"]
assert any(c["payload"].get("upload_id") == saved.json()["upload_id"] for c in changes)
# The copy is Carol's: Bob's library did not change, the space item is intact.
assert list((private["bob"][0].media_root).rglob("shared")) == []
assert carol.get(url + f"/items/{bob_item['id']}/original").status_code == 200
'''
    result = _run(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr


def test_space_quota_is_enforced(tmp_path: Path) -> None:
    body = r'''
alice = login("alice")
space = alice.post("/api/spaces", json={"name": "Tiny"}).json()["space"]["id"]
size = private["alice"][1].stat().st_size
response = alice.post(f"/api/spaces/{space}/items", json={"record_id": 1})
assert response.status_code == 507, response.text
assert alice.get(f"/api/spaces/{space}/storage").json() == {
    "used_bytes": 0, "quota_bytes": size - 1, "trash_days": 7,
}
assert list(Path("data/spaces").rglob("*.jpg")) == []
'''
    # The synthetic JPEG size is deterministic for this Pillow build; measure it.
    size = subprocess.run(
        [sys.executable, "-c",
         "import io;from PIL import Image;b=io.BytesIO();"
         "Image.new('RGB',(40,30),(200,30,30)).save(b,'JPEG');print(len(b.getvalue()))"],
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    result = _run(
        tmp_path,
        body,
        IRIS_SPACE_QUOTA_BYTES=str(int(size) - 1),
        IRIS_SPACE_TRASH_DAYS="7",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_impossible_storage_choice_stops_the_server(tmp_path: Path) -> None:
    result = _run(tmp_path, "", IRIS_SPACE_STORAGE="dedupe-magic")
    assert result.returncode != 0
    assert "StorageConfigError" in result.stderr
    assert "IRIS_SPACE_STORAGE" in result.stderr


def test_administrator_configures_storage_from_the_interface(tmp_path: Path) -> None:
    body = r'''
root, alice, bob = login("root"), login("alice"), login("bob")
anonymous = TestClient(server.app)
assert anonymous.get("/api/admin/settings").status_code == 401
assert alice.get("/api/admin/settings").status_code == 403
assert alice.put("/api/admin/settings", json={"space_trash_days": 1}).status_code == 403

state = root.get("/api/admin/settings").json()
assert state["settings"]["space_quota_bytes"]["source"] == "env"
assert state["settings"]["space_trash_days"]["source"] == "default"
assert state["storage"]["strategy"] in {"reflink", "copy"}
assert state["storage"]["warning"] is None

# Invalid values are refused and nothing is stored.
for bad in ({"space_trash_days": 0}, {"space_storage": "magic"}, {"nope": 1}, {}):
    assert root.put("/api/admin/settings", json=bad).status_code == 422, bad
assert root.get("/api/admin/settings").json()["settings"]["space_trash_days"]["source"] == "default"

# A tiny quota set in the interface applies at once, without a restart.
space = alice.post("/api/spaces", json={"name": "Family"}).json()["space"]["id"]
saved = root.put("/api/admin/settings", json={
    "space_quota_bytes": 1, "space_trash_days": 9, "space_storage": "copy",
})
assert saved.status_code == 200, saved.text
settings = saved.json()["settings"]
assert settings["space_quota_bytes"] == {
    "value": 1, "source": "interface", "env_value": 10995116277760, "default": 10995116277760,
}
assert saved.json()["storage"]["strategy"] == "copy"
assert alice.get(f"/api/spaces/{space}/storage").json()["trash_days"] == 9
assert alice.post(f"/api/spaces/{space}/items", json={"record_id": 1}).status_code == 507

# Back to the installer's value: adding works again.
reset = root.delete("/api/admin/settings/space_quota_bytes")
assert reset.status_code == 200, reset.text
assert reset.json()["settings"]["space_quota_bytes"]["source"] == "env"
added = alice.post(f"/api/spaces/{space}/items", json={"record_id": 1})
assert added.status_code == 201, added.text
assert root.delete("/api/admin/settings/unknown").status_code == 404

# Configuring the instance grants no access to a space's media.
item = added.json()["item"]
assert root.get(f"/api/spaces/{space}/items").status_code == 404
assert root.get(item["original_url"]).status_code == 404

# The interface value survives a restart and wins over .env.
import subprocess, sys
check = "import server; s = server.app.state.space_storage; print(s.trash_days, s.strategy)"
out = subprocess.run([sys.executable, "-c", check], text=True, capture_output=True)
assert out.stdout.split() == ["9", "copy"], out.stdout + out.stderr
'''
    result = _run(tmp_path, body, IRIS_SPACE_QUOTA_BYTES="10995116277760")
    assert result.returncode == 0, result.stdout + result.stderr
