"""Content the library already has is answered at reservation, before any byte is sent."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_a_known_file_is_settled_without_being_sent_again(tmp_path: Path) -> None:
    script = r'''
import hashlib, io
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image
from core.auth import hash_password
from core.users_db import create_user

data = Path("data")
create_user(data / "users.db", data, username="ana", is_admin=True,
            password_hash=hash_password("synthetic password 1"))
import server

buffer = io.BytesIO()
Image.new("RGB", (8, 8), (30, 90, 150)).save(buffer, format="JPEG")
body = buffer.getvalue()
digest = hashlib.sha256(body).hexdigest()

def item(client_id, filename="photo.jpg", source=None):
    entry = {"client_upload_id": client_id, "filename": filename, "size": len(body),
             "sha256": digest, "captured_at": "2026-09-09T12:00:00Z"}
    if source:
        entry["source"] = source
    return entry

with TestClient(server.app) as phone, TestClient(server.app) as browser:
    login = phone.post("/api/auth/devices/login", data={
        "username": "ana", "password": "synthetic password 1", "device_name": "Pixel", "platform": "android",
    })
    phone.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    first = phone.post("/api/sync/uploads/batch", json={"uploads": [item("phone-1")]}).json()["uploads"][0]
    assert first["state"] == "uploading"
    assert phone.put(f"/api/sync/uploads/{first['upload_id']}?offset=0", content=body).status_code == 200
    done = phone.post(f"/api/sync/uploads/{first['upload_id']}/complete").json()
    assert done["state"] == "ready", done
    cursor = phone.get("/api/sync/changes").json()["next_cursor"]

    # The library is now full: a new file would be refused, the known one is not.
    server.app.state.account_quota_bytes = 1
    assert browser.post("/api/auth/login", data={"username": "ana", "password": "synthetic password 1"}).status_code == 200
    folder = {"id": "web-folder", "name": "Viagem", "relative_path": "Viagem", "media_kind": "image"}
    again = browser.post("/api/sync/uploads/batch", json={"uploads": [
        item("web-1", source=folder),
        {**item("web-2", "new.jpg"), "sha256": "e" * 64},
    ]})
    assert again.status_code == 200, again.text
    known, new = again.json()["uploads"]
    # Shaped like any reservation, so existing phone clients need no change.
    assert known["state"] == "duplicate" and known["media_id"] == done["media_id"] and known["upload_id"], known
    assert new["error_code"] == 413, new
    assert not list((data / "users" / "1" / "sync_uploads").glob(f"{known['upload_id']}*"))

    # Retrying the same reservation gives the same answer.
    retried = browser.post("/api/sync/uploads/batch", json={"uploads": [item("web-1", source=folder)]})
    assert retried.json()["uploads"][0]["upload_id"] == known["upload_id"]
    assert retried.json()["uploads"][0]["state"] == "duplicate"
    assert browser.get(f"/api/sync/uploads/{known['upload_id']}").json()["state"] == "duplicate"
    assert browser.post(f"/api/sync/uploads/{known['upload_id']}/complete").json()["state"] == "duplicate"

    # As when a duplicate is found after the transfer: the browser's folder is an origin.
    changes = phone.get(f"/api/sync/changes?cursor={cursor}").json()["changes"]
    assert [c["payload"]["state"] for c in changes] == ["duplicate"], changes
    sources = {s["name"]: s["item_count"] for s in browser.get("/api/sync/sources").json()["sources"]}
    assert sources == {"Viagem": 1}, sources

    single = phone.post("/api/sync/uploads", json=item("ignored"))
    assert single.json()["state"] == "duplicate", single.text
print("ok")
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
        IRIS_LOAD_MODEL="0",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("ok")
