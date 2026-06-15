"""API smoke tests for the FastAPI server.

Verifies routing, parameter validation, response format, and static file serving.
DB-dependent tests are skipped unless TEST_DB env var is set.
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from PIL import Image

os.environ["CUDA_VISIBLE_DEVICES"] = ""


# ── Mock backend fixture ────────────────────────────────────────────────────


class ASGITestClient:
    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def run_request():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(run_request())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)


@pytest.fixture(scope="module")
def client():
    """ASGI client with a mocked backend so no CLIP/DB is loaded."""
    import server

    # Mock the backend singleton with a MagicMock that returns valid data
    mock = MagicMock()
    mock.get_total_records.return_value = 100
    mock.get_all_records.return_value = []
    mock.list_collections.return_value = []
    mock.list_concepts.return_value = []
    mock.has_concept_tables.return_value = False
    mock.search_text.return_value = []
    mock.search_image.return_value = []
    mock.search_similar.return_value = []
    mock.random_results.return_value = []
    mock.find_duplicate_groups.return_value = []
    mock.get_record.return_value = None

    server._backend = mock

    yield ASGITestClient(server.app)


# ── Smoke tests ──────────────────────────────────────────────────────────────


class TestPageServing:
    def test_index_html_loads(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "<!DOCTYPE html>" in r.text
        assert "Iris" in r.text

    def test_static_css(self, client):
        css = Path("static/style.css").read_text(encoding="utf-8")
        assert "font-family" in css.lower() or "sans-serif" in css.lower()

    def test_static_js_served(self, client):
        for mod in ["api.js", "gallery.js", "search.js", "app.js"]:
            assert (Path("static") / mod).is_file(), f"{mod} missing"


class TestInfoEndpoint:
    def test_info_json(self, client):
        r = client.get("/api/info")
        assert r.status_code == 200
        assert "total_records" in r.json()
        assert "missing_count" in r.json()
        assert "extension_counts" in r.json()


class TestSystemEndpoints:
    def test_import_requires_source(self, client):
        r = client.post("/api/import", data={})
        assert r.status_code == 400

    def test_restore_requires_confirmation(self, client):
        r = client.post(
            "/api/backup/restore",
            data={"confirm": "false"},
            files={"file": ("backup.zip", b"not-used", "application/zip")},
        )
        assert r.status_code == 400

    def test_filesystem_lists_directory(self, client, tmp_path):
        (tmp_path / "child").mkdir()
        r = client.get("/api/filesystem", params={"path": str(tmp_path)})
        assert r.status_code == 200
        assert r.json()["directories"][0]["name"] == "child"


class TestRecordsValidation:
    def test_per_page_minimum(self, client):
        r = client.get("/api/records?page=1&per_page=3")
        assert r.status_code == 422  # per_page >= 12

    def test_per_page_maximum(self, client):
        r = client.get("/api/records?page=1&per_page=600")
        assert r.status_code == 422  # per_page <= 500

    def test_page_minimum(self, client):
        r = client.get("/api/records?page=0&per_page=12")
        assert r.status_code == 422  # page >= 1


class TestSearchValidation:
    def test_search_requires_query(self, client):
        r = client.get("/api/search")
        assert r.status_code == 422

    def test_search_image_requires_file(self, client):
        r = client.post("/api/search/image")
        assert r.status_code == 422


class TestCollectionsValidation:
    def test_create_needs_name(self, client):
        r = client.post("/api/collections")
        assert r.status_code == 422


class TestConceptsValidation:
    def test_create_needs_name(self, client):
        r = client.post("/api/concepts")
        assert r.status_code == 422

    def test_create_with_references_processes_upload(self, client, monkeypatch):
        import server

        async def run_inline(function, *args):
            return function(*args)

        monkeypatch.setattr(server, "run_in_threadpool", run_inline)
        server._backend.create_concept.return_value = 12
        server._backend.encode_image.return_value = [[0.1, 0.2, 0.3]]
        image = io.BytesIO()
        Image.new("RGB", (8, 8), color=(20, 40, 60)).save(image, format="PNG")

        r = client.post(
            "/api/concepts/with-references",
            data={"name": "Teste", "category": "objeto"},
            files={"files": ("reference.png", image.getvalue(), "image/png")},
        )

        assert r.status_code == 200
        assert r.json()["references"] == 1
        server._backend.add_reference.assert_called()


class TestStaticFiles:
    def test_thumbs_404(self, client):
        r = client.get("/thumbs/nonexistent_abc123.jpg")
        assert r.status_code == 404

    def test_media_404(self, client):
        r = client.get("/media/nonexistent/path.mp4")
        assert r.status_code == 404


class TestRecordDetail:
    def test_detail_404_invalid_index(self, client):
        r = client.get("/api/records/99999999")
        assert r.status_code == 404


def test_duplicates_include_generated_thumbnail(monkeypatch, tmp_path):
    import server

    media_path = tmp_path / "duplicate.jpg"
    Image.new("RGB", (24, 24), color=(120, 80, 40)).save(media_path)
    item = SimpleNamespace(
        index=7,
        arquivo=media_path.name,
        resolved_path=str(media_path),
        score_to_anchor=1.0,
    )
    group = SimpleNamespace(
        group_id=1,
        kind="exact",
        score=1.0,
        items=[item],
    )
    backend = MagicMock()
    backend.find_duplicate_groups.return_value = [group]
    monkeypatch.setattr(server, "_backend", backend)
    monkeypatch.setattr(server, "_THUMB_DIR", tmp_path / "thumbs")

    payload = asyncio.run(
        server.get_duplicates(threshold=0.985, max_neighbors=12, min_group_size=1)
    )

    thumbnail_url = payload["groups"][0]["items"][0]["thumbnail_url"]
    assert thumbnail_url.startswith("/thumbs/")
    assert list((tmp_path / "thumbs").glob("*.jpg"))


class TestTrashValidation:
    def test_trash_needs_db_ids(self, client):
        r = client.post("/api/trash")
        assert r.status_code == 422


# ── Integration tests (require TEST_DB) ────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("TEST_DB") or not Path(os.environ.get("TEST_DB", "")).exists(),
    reason="TEST_DB not set",
)
class TestWithDatabase:
    @pytest.fixture(scope="class")
    def db_client(self):
        import server
        from core.backend import create_backend

        db = os.environ["TEST_DB"]
        server._backend = create_backend(db_path=db, media_root="media", load_model=False)
        yield ASGITestClient(server.app)

    def test_info_total(self, db_client):
        r = db_client.get("/api/info")
        assert r.status_code == 200
        assert r.json()["total_records"] > 0

    def test_records_pagination(self, db_client):
        r = db_client.get("/api/records?page=1&per_page=12")
        assert r.status_code == 200
        d = r.json()
        assert d["total"] > 0
        assert len(d["records"]) > 0

    def test_records_media_type_image(self, db_client):
        r = db_client.get("/api/records?page=1&per_page=12&media_type=image")
        assert r.status_code == 200
        for rec in r.json()["records"]:
            assert rec["media_type"] == "image"

    def test_records_media_type_video(self, db_client):
        r = db_client.get("/api/records?page=1&per_page=12&media_type=video")
        assert r.status_code == 200
        for rec in r.json()["records"]:
            assert rec["media_type"] == "video"

    def test_search_text(self, db_client):
        r = db_client.get("/api/search?q=test&top_k=5")
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        for rec in data["results"]:
            assert "score" in rec

    def test_record_detail(self, db_client):
        r = db_client.get("/api/records?page=1&per_page=12")
        recs = r.json()["records"]
        if recs:
            idx = recs[0]["index"]
            r2 = db_client.get(f"/api/records/{idx}")
            assert r2.status_code == 200
            assert "collections" in r2.json()

    def test_collections_list(self, db_client):
        r = db_client.get("/api/collections")
        assert r.status_code == 200
        assert "collections" in r.json()

    def test_collection_members(self, db_client):
        r = db_client.get("/api/collections")
        cols = r.json().get("collections", [])
        if cols:
            col_id = cols[0]["id"]
            r2 = db_client.get(f"/api/collections/{col_id}/members")
            assert r2.status_code == 200
            data = r2.json()
            assert "db_ids" in data
            assert "records" in data
            for rec in data["records"]:
                assert "thumbnail_url" in rec

    def test_duplicates(self, db_client):
        r = db_client.get("/api/duplicates?threshold=0.985&max_neighbors=5")
        assert r.status_code == 200

    def test_thumbnail_served(self, db_client):
        r = db_client.get("/api/records?page=1&per_page=12")
        for rec in r.json()["records"]:
            if rec["thumbnail_url"]:
                r2 = db_client.get(rec["thumbnail_url"])
                assert r2.status_code == 200
                assert r2.headers["content-type"] == "image/jpeg"
                break
