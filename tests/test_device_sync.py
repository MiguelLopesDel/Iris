"""Black-box coverage for device credentials and resumable sync uploads."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_device_can_upload_and_read_incremental_changes(tmp_path: Path):
    script = r'''
import io
import hashlib
import sqlite3
import asyncio
from types import SimpleNamespace
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image
from core.auth import hash_password
from core.backend_registry import BackendRegistry
from core.users_db import create_user, get_user_by_username
from routers.sync import upload_chunk

data = Path("data")
create_user(data / "users.db", data, username="alice", password_hash=hash_password("senha segura 123"), is_admin=True)

import server
class CountingRegistry(BackendRegistry):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_calls = 0

    def get(self, user_id):
        self.get_calls += 1
        return super().get(user_id)

registry = CountingRegistry(data / "users.db", load_model=False)
server.app.state.backend_registry = registry
image = Image.new("RGB", (8, 8), (30, 90, 150))
buffer = io.BytesIO()
image.save(buffer, format="JPEG")
body = buffer.getvalue()
with TestClient(server.app) as client:
    login = client.post("/api/auth/devices/login", data={
        "username": "alice", "password": "senha segura 123",
        "device_name": "Alice phone", "platform": "android",
    })
    assert login.status_code == 200, login.text
    session = login.json()
    token = session["access_token"]
    headers = {"Authorization": "Bearer " + token}
    refreshed = client.post("/api/auth/devices/refresh", data={
        "device_id": session["device_id"], "refresh_token": session["refresh_token"],
    })
    assert refreshed.status_code == 200, refreshed.text
    headers = {"Authorization": "Bearer " + refreshed.json()["access_token"]}
    assert client.get("/api/sync/devices", headers=headers).json()["devices"][0]["name"] == "Alice phone"

    def batch_item(client_upload_id, filename, digest):
        return {
            "client_upload_id": client_upload_id,
            "filename": filename,
            "size": 0,
            "sha256": digest,
            "captured_at": "2026-09-09T12:00:00Z",
        }

    batch_payload = {"uploads": [
        batch_item("phone-job-1", "batch-one.jpg", "a" * 64),
        batch_item("phone-job-2", "batch-two.jpg", "b" * 64),
    ]}
    reserved = client.post("/api/sync/uploads/batch", headers=headers, json=batch_payload)
    assert reserved.status_code == 200, reserved.text
    reserved_items = reserved.json()["uploads"]
    assert len(reserved_items) == 2
    assert all(item["state"] == "uploading" and item["offset"] == 0 for item in reserved_items)
    retried = client.post("/api/sync/uploads/batch", headers=headers, json=batch_payload)
    assert retried.status_code == 200, retried.text
    assert [item["upload_id"] for item in retried.json()["uploads"]] == [
        item["upload_id"] for item in reserved_items
    ], "a lost batch response must be safely replayable"
    changed = client.post(
        "/api/sync/uploads/batch",
        headers=headers,
        json={"uploads": [batch_item("phone-job-1", "batch-one.jpg", "c" * 64)]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["uploads"][0]["error_code"] == 409

    malformed_batches = [
        {"uploads": [None]},
        {"uploads": [batch_item("duplicate-id", "one.jpg", "1" * 64),
                      batch_item("duplicate-id", "two.jpg", "2" * 64)]},
        {"uploads": [batch_item("", "missing-id.jpg", "3" * 64)]},
    ]
    for malformed_batch in malformed_batches:
        rejected = client.post("/api/sync/uploads/batch", headers=headers, json=malformed_batch)
        assert rejected.status_code == 400, rejected.text

    # Single and batch reservations must apply the same validation contract.
    invalid_single = client.post("/api/sync/uploads", headers=headers, json={
        "filename": "photo.jpg", "size": True, "sha256": "z" * 64,
    })
    assert invalid_single.status_code == 400, invalid_single.text
    invalid_batch = client.post("/api/sync/uploads/batch", headers=headers, json={
        "uploads": [
            {**batch_item("bad-size", "photo.jpg", "a" * 64), "size": True},
            {**batch_item("bad-hash", "photo.jpg", "z" * 64), "size": 0},
            {**batch_item("bad-source", "photo.jpg", "a" * 64),
             "source": {"media_kind": "audio"}},
        ],
    })
    assert invalid_batch.status_code == 200, invalid_batch.text
    assert [item["error_code"] for item in invalid_batch.json()["uploads"]] == [400, 400, 400]

    user_db = sqlite3.connect(data / "users" / "1" / "iris.db")
    assert user_db.execute(
        "SELECT COUNT(*) FROM sync_uploads WHERE client_upload_id IN ('duplicate-id', '')"
    ).fetchone()[0] == 0, "structurally invalid batches must be rejected before reserving any item"
    user_db.close()

    other_device_login = client.post("/api/auth/devices/login", data={
        "username": "alice", "password": "senha segura 123",
        "device_name": "Alice second phone", "platform": "android",
    })
    assert other_device_login.status_code == 200, other_device_login.text
    other_device_headers = {"Authorization": "Bearer " + other_device_login.json()["access_token"]}
    isolated_reservation = client.post(
        "/api/sync/uploads/batch", headers=other_device_headers,
        json={"uploads": [batch_item("phone-job-1", "batch-one.jpg", "a" * 64)]},
    )
    assert isolated_reservation.status_code == 200, isolated_reservation.text
    assert isolated_reservation.json()["uploads"][0]["upload_id"] != reserved_items[0]["upload_id"]
    hidden_from_other_device = client.get(
        "/api/sync/uploads/" + reserved_items[0]["upload_id"],
        headers=other_device_headers,
    )
    assert hidden_from_other_device.status_code == 404

    started = client.post("/api/sync/uploads", headers=headers, json={
        "filename": "photo.jpg", "size": len(body), "sha256": hashlib.sha256(body).hexdigest(),
        "captured_at": "2026-09-09T12:00:00Z",
        "source": {
            "id": "external:camera:image", "name": "Camera",
            "relative_path": "../../must-not-be-used", "volume": "external",
            "media_store_id": "42", "generation": 7, "media_kind": "image",
        },
    })
    assert started.status_code == 200, started.text
    upload_id = started.json()["upload_id"]

    class InterruptedRequest:
        app = SimpleNamespace(state=server.app.state)
        state = SimpleNamespace(
            iris_device_id=session["device_id"],
            iris_user=get_user_by_username(data / "users.db", "alice"),
        )
        headers = {"content-length": "2"}

        async def stream(self):
            yield body[:2]
            raise OSError("simulated disconnected upload")

    try:
        asyncio.run(upload_chunk(InterruptedRequest(), upload_id, offset=0))
    except OSError:
        pass
    else:
        raise AssertionError("the simulated interrupted upload should fail")

    db = sqlite3.connect(data / "users" / "1" / "iris.db")
    temporary_path = db.execute(
        "SELECT temp_path FROM sync_uploads WHERE id = ?", (upload_id,)
    ).fetchone()[0]
    assert Path(temporary_path).stat().st_size == 0, "unacknowledged chunk bytes must be rolled back"
    db.close()

    # Simulate a process crash after writing to disk but before committing the
    # SQLite offset. The retry must discard this stale tail before appending.
    Path(temporary_path).write_bytes(body[:2])

    chunk = client.put("/api/sync/uploads/" + upload_id + "?offset=0", headers=headers, content=body)
    assert chunk.status_code == 200, chunk.text
    completed = client.post("/api/sync/uploads/" + upload_id + "/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == "ready"
    assert isinstance(completed.json()["media_id"], int)
    completed_path = Path(completed.json()["path"])
    assert completed_path.read_bytes() == body
    assert "must-not-be-used" not in str(completed_path)
    assert completed_path.name.endswith("-photo.jpg")
    # Dated by when it was taken (the device's captured_at), not when it arrived.
    dated = sqlite3.connect(data / "users" / "1" / "iris.db")
    assert dated.execute(
        "SELECT file_mtime FROM memes WHERE id = ?", (completed.json()["media_id"],)
    ).fetchone()[0] == 1788955200.0
    dated.close()

    # Simulate a crash after the durable filesystem move but before the
    # database row leaves `finalizing`. Replaying batch reservation must return
    # the same upload, and completion must recover idempotently from the
    # already-committed destination.
    recovery_body = b"recovered after finalizing crash"
    recovery_hash = hashlib.sha256(recovery_body).hexdigest()
    recovery_client_id = "phone-job-recovery"
    recovery_metadata = {
        **batch_item(recovery_client_id, "recovered.jpg", recovery_hash),
        "size": len(recovery_body),
    }
    recovery_reservation = client.post(
        "/api/sync/uploads/batch", headers=headers,
        json={"uploads": [recovery_metadata]},
    )
    assert recovery_reservation.status_code == 200, recovery_reservation.text
    [recovery_upload] = recovery_reservation.json()["uploads"]
    recovery_upload_id = recovery_upload["upload_id"]
    recovery_chunk = client.put(
        f"/api/sync/uploads/{recovery_upload_id}?offset=0",
        headers=headers,
        content=recovery_body,
    )
    assert recovery_chunk.status_code == 200, recovery_chunk.text

    recovery_user = get_user_by_username(data / "users.db", "alice")
    recovery_destination = (
        recovery_user.media_root / "uploads" / "recovery" / "recovered.jpg"
    )
    db = sqlite3.connect(recovery_user.db_path)
    recovery_temp_path = db.execute(
        "SELECT temp_path FROM sync_uploads WHERE id = ?", (recovery_upload_id,)
    ).fetchone()[0]
    db.execute(
        "UPDATE sync_uploads SET state = 'finalizing', final_path = ? WHERE id = ?",
        (str(recovery_destination), recovery_upload_id),
    )
    db.commit()
    db.close()

    from core.sync_file_ops import durable_move_upload
    durable_move_upload(
        Path(recovery_temp_path),
        recovery_destination,
        upload_id=recovery_upload_id,
        expected_size=len(recovery_body),
        expected_hash=recovery_hash,
    )
    retried_reservation = client.post(
        "/api/sync/uploads/batch", headers=headers,
        json={"uploads": [recovery_metadata]},
    )
    assert retried_reservation.status_code == 200, retried_reservation.text
    [recovered_reservation] = retried_reservation.json()["uploads"]
    assert recovered_reservation["upload_id"] == recovery_upload_id
    assert recovered_reservation["offset"] == len(recovery_body)
    assert recovered_reservation["state"] == "uploading"

    recovered_completion = client.post(
        f"/api/sync/uploads/{recovery_upload_id}/complete", headers=headers,
    )
    assert recovered_completion.status_code == 200, recovered_completion.text
    assert recovered_completion.json()["state"] == "ready"
    assert Path(recovered_completion.json()["path"]).read_bytes() == recovery_body

    # Batch finalization must return one independent result per upload, allow
    # successful siblings when one ID is missing, and safely replay after a
    # lost response without inserting the same media/change twice.
    second_image = Image.new("RGB", (9, 8), (180, 40, 90))
    second_buffer = io.BytesIO()
    second_image.save(second_buffer, format="JPEG")
    second_body = second_buffer.getvalue()
    second_hash = hashlib.sha256(second_body).hexdigest()
    second_reservation = client.post("/api/sync/uploads/batch", headers=headers, json={
        "uploads": [{
            **batch_item("phone-job-3", "second.jpg", second_hash),
            "size": len(second_body),
        }],
    })
    assert second_reservation.status_code == 200, second_reservation.text
    second_upload_id = second_reservation.json()["uploads"][0]["upload_id"]
    second_chunk = client.put(
        "/api/sync/uploads/" + second_upload_id + "?offset=0",
        headers=headers, content=second_body,
    )
    assert second_chunk.status_code == 200, second_chunk.text
    completion_payload = {"uploads": [
        {"upload_id": upload_id},
        {"upload_id": second_upload_id},
        {"upload_id": "not-owned-or-missing"},
    ]}
    batch_completed = client.post(
        "/api/sync/uploads/complete-batch", headers=headers, json=completion_payload,
    )
    assert batch_completed.status_code == 200, batch_completed.text
    batch_results = batch_completed.json()["uploads"]
    assert [item["upload_id"] for item in batch_results] == [
        upload_id, second_upload_id, "not-owned-or-missing",
    ]
    assert [item.get("state") for item in batch_results[:2]] == ["ready", "ready"]
    assert batch_results[2]["error_code"] == 404
    replayed_batch = client.post(
        "/api/sync/uploads/complete-batch", headers=headers, json=completion_payload,
    )
    assert replayed_batch.status_code == 200, replayed_batch.text
    assert [item.get("state") for item in replayed_batch.json()["uploads"][:2]] == ["ready", "ready"]
    db = sqlite3.connect(data / "users" / "1" / "iris.db")
    assert db.execute("SELECT COUNT(*) FROM memes").fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM sync_changes WHERE entity_type = 'media'").fetchone()[0] == 9
    db.close()

    sources = client.get("/api/sync/sources", headers=headers).json()["sources"]
    assert sources[0]["name"] == "Camera"
    assert sources[0]["relative_path"] == "../../must-not-be-used"
    assert sources[0]["item_count"] == 1
    # Sync authenticates and scopes every request without constructing the
    # search backend. Gallery access constructs it on demand and sees uploads.
    assert registry.get_calls == 0
    page = client.get("/api/records", headers=headers)
    assert page.status_code == 200, page.text
    assert registry.get_calls == 1
    assert page.json()["total"] == 3
    record = next(item for item in page.json()["records"] if item["arquivo"] == "photo.jpg")
    assert record["arquivo"] == "photo.jpg"
    assert record["thumbnail_url"].startswith("/thumbs/0/")
    assert client.get(record["thumbnail_url"], headers=headers).status_code == 200
    db = sqlite3.connect(data / "users" / "1" / "iris.db")
    row = db.execute("SELECT embedding, desc_embedding FROM memes").fetchone()
    assert row == (None, None), row
    stored_bytes = db.execute("SELECT SUM(file_size) FROM memes").fetchone()[0]
    assert stored_bytes == len(body) + len(recovery_body) + len(second_body), stored_bytes
    db.close()

    # Quota checks must use the catalog, not recursively stat every media file
    # on every upload. Pending uploads also reserve their declared bytes.
    uploaded_bytes = len(body) + len(recovery_body) + len(second_body)
    server.app.state.account_quota_bytes = uploaded_bytes + 1
    from pathlib import Path
    original_rglob = Path.rglob
    def unexpected_media_walk(self, *args, **kwargs):
        raise AssertionError("upload quota check recursively walked the media tree")
    Path.rglob = unexpected_media_walk
    try:
        next_upload = client.post("/api/sync/uploads", headers=headers, json={
            "filename": "next.jpg", "size": 1, "sha256": "a" * 64,
        })
        assert next_upload.status_code == 200, next_upload.text
        over_quota = client.post("/api/sync/uploads", headers=headers, json={
            "filename": "over.jpg", "size": 1, "sha256": "b" * 64,
        })
        assert over_quota.status_code == 413, over_quota.text
        server.app.state.account_quota_bytes = uploaded_bytes + 2
        partially_reserved = client.post("/api/sync/uploads/batch", headers=headers, json={
            "uploads": [
                {**batch_item("quota-job-1", "quota-one.jpg", "d" * 64), "size": 1},
                {**batch_item("quota-job-2", "quota-two.jpg", "e" * 64), "size": 1},
            ],
        })
        assert partially_reserved.status_code == 200, partially_reserved.text
        assert [item.get("error_code") for item in partially_reserved.json()["uploads"]] == [None, 413]
    finally:
        Path.rglob = original_rglob

    changes = client.get("/api/sync/changes", headers=headers).json()
    assert changes["changes"][0]["operation"] == "created"
    assert changes["changes"][0]["payload"]["state"] == "pending_processing"
    assert changes["changes"][-1]["payload"]["state"] == "ready"
    assert client.delete("/api/sync/devices/" + session["device_id"], headers=headers).status_code == 200
    assert client.get("/api/sync/devices", headers=headers).status_code == 401
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_LOAD_MODEL="1",
        IRIS_SYNC_AI_PROCESSING="0",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
