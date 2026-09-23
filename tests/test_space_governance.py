"""Space governance: roles, removal, leaving, and the last-manager rule."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from core import shared_spaces
from core.shared_spaces import (
    SpaceLastManager,
    SpaceMemberNotFound,
    SpaceNotFound,
    SpacePermissionDenied,
)
from core.users_db import create_user


@pytest.fixture
def registry(tmp_path: Path) -> tuple[Path, int, dict[str, int]]:
    db = tmp_path / "users.db"
    ids = {
        name: create_user(db, tmp_path, username=name, password_hash="x").id
        for name in ("alice", "bob", "carol", "dave")
    }
    space = shared_spaces.create_space(db, ids["alice"], "Family")
    shared_spaces.add_member(db, space.id, ids["alice"], "bob", "contributor")
    shared_spaces.add_member(db, space.id, ids["alice"], "carol", "viewer")
    return db, space.id, ids


def _roles(db: Path, space_id: int) -> dict[str, str]:
    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT u.username, m.role FROM shared_space_members m "
            "JOIN users u ON u.id = m.user_id WHERE m.space_id = ?",
            (space_id,),
        ).fetchall()
    return dict(rows)


def test_only_managers_change_roles(registry) -> None:
    db, space, ids = registry
    with pytest.raises(SpacePermissionDenied):
        shared_spaces.change_role(db, space, ids["bob"], ids["carol"], "manager")
    with pytest.raises(SpaceNotFound):
        shared_spaces.change_role(db, space, ids["dave"], ids["carol"], "manager")
    with pytest.raises(SpaceMemberNotFound):
        shared_spaces.change_role(db, space, ids["alice"], ids["dave"], "viewer")
    with pytest.raises(ValueError):
        shared_spaces.change_role(db, space, ids["alice"], ids["carol"], "owner")
    member = shared_spaces.change_role(db, space, ids["alice"], ids["carol"], "contributor")
    assert member.role == "contributor" and _roles(db, space)["carol"] == "contributor"


def test_the_last_manager_cannot_step_down_or_leave(registry) -> None:
    db, space, ids = registry
    with pytest.raises(SpaceLastManager):
        shared_spaces.change_role(db, space, ids["alice"], ids["alice"], "viewer")
    with pytest.raises(SpaceLastManager):
        shared_spaces.remove_member(db, space, ids["alice"], ids["alice"])

    shared_spaces.change_role(db, space, ids["alice"], ids["bob"], "manager")
    shared_spaces.change_role(db, space, ids["alice"], ids["alice"], "viewer")
    assert _roles(db, space) == {"alice": "viewer", "bob": "manager", "carol": "viewer"}
    with pytest.raises(SpaceLastManager):
        shared_spaces.remove_member(db, space, ids["bob"], ids["bob"])


def test_members_leave_and_managers_remove(registry) -> None:
    db, space, ids = registry
    with pytest.raises(SpacePermissionDenied):
        shared_spaces.remove_member(db, space, ids["carol"], ids["bob"])
    shared_spaces.remove_member(db, space, ids["carol"], ids["carol"])  # leaving
    shared_spaces.remove_member(db, space, ids["alice"], ids["bob"])  # removal
    assert _roles(db, space) == {"alice": "manager"}
    with pytest.raises(SpaceNotFound):
        shared_spaces.member_role(db, space, ids["bob"])
    # Re-inviting works: the old membership is gone, not hidden.
    shared_spaces.add_member(db, space, ids["alice"], "bob", "viewer")
    assert _roles(db, space)["bob"] == "viewer"


def test_two_managers_demoting_each_other_at_once_leave_one(registry) -> None:
    db, space, ids = registry
    for _ in range(25):
        shared_spaces.change_role(db, space, ids["alice"], ids["bob"], "manager")
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def demote(
            actor: str, target: str, barrier=barrier, outcomes=outcomes
        ) -> None:
            barrier.wait()
            try:
                shared_spaces.change_role(db, space, ids[actor], ids[target], "viewer")
                outcomes.append("ok")
            except (SpaceLastManager, SpacePermissionDenied) as exc:
                outcomes.append(type(exc).__name__)

        threads = [
            threading.Thread(target=demote, args=("alice", "bob")),
            threading.Thread(target=demote, args=("bob", "alice")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # Both requests got a clean answer (no "database is locked" surfacing
        # as a server error), and exactly one manager is left.
        assert sorted(outcomes) in (["SpaceLastManager", "ok"], ["SpacePermissionDenied", "ok"])
        managers = [name for name, role in _roles(db, space).items() if role == "manager"]
        assert len(managers) == 1, (outcomes, _roles(db, space))
        # Restore the starting point: alice manager, bob contributor.
        survivor = managers[0]
        shared_spaces.change_role(db, space, ids[survivor], ids["alice"], "manager")
        shared_spaces.change_role(db, space, ids["alice"], ids["bob"], "contributor")


def test_governance_over_http(tmp_path: Path) -> None:
    script = r'''
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
for name in ("root", "alice", "bob", "carol", "dave"):
    user = create_user(data / "users.db", data, username=name,
                       password_hash=hash_password(PASSWORD), is_admin=name == "root")
    init_db(user.db_path).close()
    image = user.media_root / f"{name}.jpg"
    Image.new("RGB", (16, 16), (len(name) * 40, 90, 120)).save(image)
    with sqlite3.connect(user.db_path) as conn:
        conn.execute("INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
                     (image.name, str(image), b"\0" * 16))

import server

clients = {}
for name in ("root", "alice", "bob", "carol", "dave"):
    client = TestClient(server.app)
    client.__enter__()
    assert client.post("/api/auth/login", data={"username": name, "password": PASSWORD}).status_code == 200
    clients[name] = client
root, alice, bob, carol, dave = (clients[n] for n in ("root", "alice", "bob", "carol", "dave"))

space = alice.post("/api/spaces", json={"name": "Family"}).json()["space"]["id"]
url = f"/api/spaces/{space}"
for name, role in (("bob", "contributor"), ("carol", "viewer")):
    assert alice.post(url + "/members", json={"username": name, "role": role}).status_code == 201
members = alice.get(url + "/members").json()["members"]
ids = {m["username"]: m["user_id"] for m in members}
assert [m["is_you"] for m in members if m["username"] == "alice"] == [True]
member = lambda name: f"{url}/members/{ids[name]}"

# Outsiders, the instance administrator included, cannot touch membership.
for outsider in (root, dave):
    assert outsider.patch(member("carol"), json={"role": "manager"}).status_code == 404
    assert outsider.delete(member("carol")).status_code == 404
# Non-managers cannot change others.
assert carol.patch(member("bob"), json={"role": "viewer"}).status_code == 403
assert bob.delete(member("carol")).status_code == 403
assert alice.patch(member("carol"), json={"role": "owner"}).status_code == 422
assert alice.patch(f"{url}/members/99999", json={"role": "viewer"}).status_code == 404

# The last manager is protected.
assert alice.patch(member("alice"), json={"role": "viewer"}).status_code == 409
assert alice.delete(member("alice")).status_code == 409

# Bob contributes, then is removed: his access ends at once, his photo stays.
item = bob.post(url + "/items", json={"record_id": 1}).json()["item"]
assert bob.get(item["original_url"]).status_code == 200
assert alice.delete(member("bob")).status_code == 204
assert bob.get(item["original_url"]).status_code == 404
assert bob.get(url + "/items").status_code == 404
assert bob.get("/api/spaces").json()["spaces"] == []
kept = carol.get(url + "/items").json()["items"]
assert [(i["id"], i["added_by_username"]) for i in kept] == [(item["id"], "bob")]
assert hashlib.sha256(carol.get(item["original_url"]).content).hexdigest() == item["sha256"]

# Carol leaves on her own; Alice promotes a re-invited Bob and steps down.
assert carol.delete(member("carol")).status_code == 204
assert carol.get(url).status_code == 404
assert alice.post(url + "/members", json={"username": "bob", "role": "viewer"}).status_code == 201
promoted = alice.patch(member("bob"), json={"role": "manager"})
assert promoted.status_code == 200 and promoted.json()["member"]["role"] == "manager"
assert alice.patch(member("alice"), json={"role": "viewer"}).status_code == 200
assert alice.post(url + "/members", json={"username": "carol", "role": "viewer"}).status_code == 403
assert bob.delete(member("bob")).status_code == 409
assert bob.delete(member("alice")).status_code == 204
assert [m["username"] for m in bob.get(url + "/members").json()["members"]] == ["bob"]
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
