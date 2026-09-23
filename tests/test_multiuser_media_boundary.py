"""Private media must stay private across accounts, sessions, and devices."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_private_media_boundary_with_two_accounts(tmp_path: Path) -> None:
    script = r'''
import sqlite3
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user

data = Path("data")
users = {}
for username, color in (("alice", (220, 40, 40)), ("bob", (40, 40, 220))):
    user = create_user(
        data / "users.db",
        data,
        username=username,
        password_hash=hash_password("senha segura 123"),
        is_admin=username == "alice",
    )
    image = user.media_root / f"only-{username}.jpg"
    Image.new("RGB", (32, 32), color).save(image)
    init_db(user.db_path).close()
    with sqlite3.connect(user.db_path) as conn:
        conn.execute(
            "INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
            (image.name, str(image), b"\0" * 16),
        )
    users[username] = (user, image)

import server

with TestClient(server.app) as alice, TestClient(server.app) as bob:
    assert alice.get("/api/records").status_code == 401
    assert alice.post(
        "/api/auth/login",
        data={"username": "alice", "password": "senha segura 123"},
    ).status_code == 200
    assert bob.post(
        "/api/auth/login",
        data={"username": "bob", "password": "senha segura 123"},
    ).status_code == 200

    alice_rows = alice.get("/api/records").json()["records"]
    bob_rows = bob.get("/api/records").json()["records"]
    assert [row["arquivo"] for row in alice_rows] == ["only-alice.jpg"]
    assert [row["arquivo"] for row in bob_rows] == ["only-bob.jpg"]

    alice_thumb = alice_rows[0]["thumbnail_url"]
    bob_thumb = bob_rows[0]["thumbnail_url"]
    alice_media = "/media/" + str(users["alice"][1]).lstrip("/")
    bob_media = "/media/" + str(users["bob"][1]).lstrip("/")
    assert alice.get(alice_thumb).status_code == 200
    assert bob.get(bob_thumb).status_code == 200
    assert alice.get(alice_media).status_code == 200
    assert bob.get(bob_media).status_code == 200

    # Alice is the instance administrator; that does not grant Bob's bytes.
    assert alice.get(bob_thumb).status_code == 404
    assert alice.get(bob_media).status_code == 404
    assert bob.get(alice_thumb).status_code == 404
    assert bob.get(alice_media).status_code == 404

    # The legacy cached-thumbnail route also stays within each account root.
    assert alice.get("/thumbs/" + bob_thumb.rsplit("/", 1)[1]).status_code == 404
    assert bob.get("/thumbs/" + alice_thumb.rsplit("/", 1)[1]).status_code == 404

    # Device tokens use the same media boundary as web sessions.
    login = alice.post(
        "/api/auth/devices/login",
        data={
            "username": "alice",
            "password": "senha segura 123",
            "device_name": "Alice phone",
            "platform": "android",
        },
    )
    assert login.status_code == 200, login.text
    device = TestClient(server.app)
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert device.get(alice_media, headers=headers).status_code == 200
    assert device.get(bob_media, headers=headers).status_code == 404
    assert device.get(bob_thumb, headers=headers).status_code == 404

    bob_login = bob.post(
        "/api/auth/devices/login",
        data={
            "username": "bob",
            "password": "senha segura 123",
            "device_name": "Bob phone",
            "platform": "android",
        },
    )
    assert bob_login.status_code == 200, bob_login.text
    bob_headers = {"Authorization": "Bearer " + bob_login.json()["access_token"]}
    payload = b"synthetic upload"
    started = device.post(
        "/api/sync/uploads",
        headers=bob_headers,
        json={
            "filename": "private-upload.jpg",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    assert started.status_code == 200, started.text
    upload_url = "/api/sync/uploads/" + started.json()["upload_id"]
    assert device.get(upload_url, headers=headers).status_code == 404
    assert device.put(upload_url + "?offset=0", headers=headers, content=payload).status_code == 404
    assert device.post(upload_url + "/complete", headers=headers).status_code == 404
    assert device.get(upload_url, headers=bob_headers).json()["offset"] == 0
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
