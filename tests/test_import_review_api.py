"""HTTP endpoint tests for the import-review API (list / resolve / thumb).

Uses a real temp DB file (endpoints open their own sqlite connection via _import_db)
and a mocked search backend for match-thumbnail lookups. No models or network.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

import server
from core import import_review
from core.indexer_db import init_db


class ASGITestClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.request(method, path, **kwargs)

        return asyncio.run(run())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    conn = init_db(db_path)
    conn.execute("INSERT INTO memes (id, arquivo) VALUES (1, 'match.jpg')")
    import_review.quarantine_candidate(
        conn, job_id="j", candidate_path=str(tmp_path / "dupe_exact.jpg"),
        candidate_hash="h1", candidate_phash=None, candidate_thumb=None,
        detection="exact_hash", match_meme_id=1, score=1.0,
    )
    import_review.quarantine_candidate(
        conn, job_id="j", candidate_path=str(tmp_path / "dupe_fuzzy.jpg"),
        candidate_hash="h2", candidate_phash="ff", candidate_thumb=b"THUMB",
        detection="perceptual", match_meme_id=1, score=0.991,
    )
    conn.commit()
    conn.close()

    monkeypatch.setitem(server._active_config, "db_path", str(db_path))
    backend = SimpleNamespace(
        get_all_records=lambda: [
            SimpleNamespace(db_id=1, arquivo="match.jpg", resolved_path="")
        ]
    )
    monkeypatch.setattr(server, "_backend", backend)
    server._import_job["status"] = "idle"
    server._import_job["id"] = None
    return SimpleNamespace(client=ASGITestClient(server.app), db=db_path, tmp=tmp_path)


def test_list_groups_by_detection(ctx):
    res = ctx.client.get("/api/import/review")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    detections = {c["detection"]: c["count"] for c in body["categories"]}
    assert detections == {"exact_hash": 1, "perceptual": 1}


def test_list_filtered_and_thumb_url(ctx):
    res = ctx.client.get("/api/import/review", params={"detection": "perceptual"})
    items = res.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["candidate_thumb_url"].endswith("/thumb")
    assert item["match_meme_id"] == 1
    # thumb endpoint returns the stored bytes
    thumb = ctx.client.get(item["candidate_thumb_url"])
    assert thumb.status_code == 200 and thumb.content == b"THUMB"


def test_resolve_ignore_removes_from_pending(ctx):
    ids = [item["id"] for item in ctx.client.get(
        "/api/import/review", params={"detection": "perceptual"}).json()["items"]]
    res = ctx.client.post(
        "/api/import/review/resolve",
        data={"ids": ",".join(map(str, ids)), "action": "ignore"},
    )
    assert res.json()["resolved"] == 1
    assert ctx.client.get("/api/import/review").json()["total"] == 1


def test_resolve_trash_calls_move_to_trash(ctx, monkeypatch):
    calls = {}

    def fake_trash(paths):
        calls["paths"] = list(paths)
        return list(paths), []

    monkeypatch.setattr(server, "move_to_trash", fake_trash)
    res = ctx.client.post(
        "/api/import/review/resolve",
        data={"detection": "exact_hash", "action": "trash"},
    )
    body = res.json()
    assert body["moved"] == 1 and body["resolved"] == 1
    assert calls["paths"] == [str(ctx.tmp / "dupe_exact.jpg")]


def test_resolve_import_enqueues_forced_import(ctx, monkeypatch):
    # Candidate file must exist on disk for the "import anyway" path.
    candidate = ctx.tmp / "dupe_fuzzy.jpg"
    candidate.write_bytes(b"\xff\xd8\xff")

    queued = {}
    # The endpoint enqueues (doesn't launch a job per click), so rapid clicks
    # never collide. Patch the queue so no real worker/models start.
    monkeypatch.setattr(
        server, "_enqueue_forced_import",
        lambda paths: queued.setdefault("paths", paths) or len(paths),
    )
    ids = [item["id"] for item in ctx.client.get(
        "/api/import/review", params={"detection": "perceptual"}).json()["items"]]
    res = ctx.client.post(
        "/api/import/review/resolve",
        data={"ids": ",".join(map(str, ids)), "action": "import"},
    )
    assert res.json()["imported_files"] == 1
    assert queued["paths"] == [candidate]
    # marked resolved → no longer pending
    with sqlite3.connect(ctx.db) as conn:
        assert import_review.counts_by_detection(conn).get("perceptual", 0) == 0


def test_resolve_import_does_not_409_when_import_running(ctx, monkeypatch):
    # Even with an import "in flight", per-item import must enqueue (not 409),
    # so the user can click through items without waiting.
    candidate = ctx.tmp / "dupe_fuzzy.jpg"
    candidate.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(server, "_enqueue_forced_import", lambda paths: len(paths))
    server._import_job["status"] = "running"
    try:
        ids = [item["id"] for item in ctx.client.get(
            "/api/import/review", params={"detection": "perceptual"}).json()["items"]]
        res = ctx.client.post(
            "/api/import/review/resolve",
            data={"ids": ",".join(map(str, ids)), "action": "import"},
        )
        assert res.status_code == 200
    finally:
        server._import_job["status"] = "idle"


def test_invalid_action_rejected(ctx):
    res = ctx.client.post("/api/import/review/resolve", data={"ids": "1", "action": "boom"})
    assert res.status_code == 400
