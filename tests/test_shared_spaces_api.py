"""First shared-space slice: identity, membership and private visibility."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_space_membership_is_not_instance_administration(tmp_path: Path) -> None:
    script = r'''
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from core.auth import hash_password
from core.users_db import create_user

data = Path("data")
for username in ("alice", "bob", "carol", "dave", "erin"):
    create_user(
        data / "users.db", data, username=username,
        password_hash=hash_password("synthetic shared password"),
        is_admin=username == "alice",
    )

import server

with (
    TestClient(server.app) as alice,
    TestClient(server.app) as bob,
    TestClient(server.app) as carol,
    TestClient(server.app) as dave,
    TestClient(server.app) as erin,
):
    assert bob.get("/api/spaces").status_code == 401
    for username, client in (
        ("alice", alice), ("bob", bob), ("carol", carol),
        ("dave", dave), ("erin", erin),
    ):
        response = client.post("/api/auth/login", data={
            "username": username, "password": "synthetic shared password"
        })
        assert response.status_code == 200, response.text

    assert bob.post("/api/spaces", json={"name": "   "}).status_code == 422
    created = bob.post("/api/spaces", json={"name": "  Family  "})
    assert created.status_code == 201, created.text
    space = created.json()["space"]
    assert space["name"] == "Family"
    assert space["role"] == "manager"
    space_id = space["id"]
    url = f"/api/spaces/{space_id}"

    assert [item["id"] for item in bob.get("/api/spaces").json()["spaces"]] == [space_id]
    assert alice.get("/api/spaces").json()["spaces"] == []
    assert alice.get(url).status_code == 404  # instance admin cannot peek
    assert alice.get(url + "/members").status_code == 404
    assert carol.get(url).status_code == 404
    assert carol.post(url + "/members", json={"username": "alice", "role": "manager"}).status_code == 404

    invite = bob.post(url + "/members", json={"username": "carol", "role": "viewer"})
    assert invite.status_code == 201, invite.text
    assert invite.json()["member"]["role"] == "viewer"
    assert bob.post(url + "/members", json={"username": "carol", "role": "viewer"}).status_code == 409
    assert bob.post(url + "/members", json={"username": "nobody", "role": "viewer"}).status_code == 404
    assert bob.post(url + "/members", json={"username": "dave", "role": "owner"}).status_code == 422

    assert carol.get(url).json()["space"]["role"] == "viewer"
    assert len(carol.get(url + "/members").json()["members"]) == 2
    assert carol.post(url + "/members", json={"username": "alice", "role": "manager"}).status_code == 403

    assert bob.post(url + "/members", json={"username": "dave", "role": "contributor"}).status_code == 201
    assert dave.get(url).json()["space"]["role"] == "contributor"
    assert dave.post(url + "/members", json={"username": "alice", "role": "viewer"}).status_code == 403

    assert bob.post(url + "/members", json={"username": "erin", "role": "manager"}).status_code == 201
    assert erin.post(url + "/members", json={"username": "alice", "role": "viewer"}).status_code == 201
    assert alice.get(url).json()["space"]["role"] == "viewer"

    device_login = carol.post("/api/auth/devices/login", data={
        "username": "carol", "password": "synthetic shared password",
        "device_name": "Carol phone", "platform": "android",
    })
    assert device_login.status_code == 200, device_login.text
    token = device_login.json()["access_token"]
    with TestClient(server.app) as device:
        response = device.get("/api/spaces", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200
        assert response.json()["spaces"][0]["role"] == "viewer"

with sqlite3.connect(data / "users.db") as connection:
    assert connection.execute("SELECT COUNT(*) FROM shared_spaces").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM shared_space_members").fetchone()[0] == 5
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
