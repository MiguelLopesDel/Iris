"""API smoke tests for the FastAPI server.

Verifies routing, parameter validation, response format, and static file serving.
DB-dependent tests are skipped unless TEST_DB env var is set.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ["CUDA_VISIBLE_DEVICES"] = ""


# ── Mock backend fixture ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """TestClient with a mocked backend so no CLIP/DB is loaded."""
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

    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


# ── Smoke tests ──────────────────────────────────────────────────────────────


class TestPageServing:
    def test_index_html_loads(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "<!DOCTYPE html>" in r.text
        assert "Iris" in r.text

    def test_static_css(self, client):
        r = client.get("/static/style.css")
        assert r.status_code == 200
        assert "font-family" in r.text.lower() or "sans-serif" in r.text.lower()

    def test_static_js_served(self, client):
        for mod in ["api.js", "gallery.js", "search.js", "app.js"]:
            r = client.get(f"/static/{mod}")
            assert r.status_code == 200, f"{mod} not served"


class TestInfoEndpoint:
    def test_info_json(self, client):
        r = client.get("/api/info")
        assert r.status_code == 200
        assert "total_records" in r.json()


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
        with TestClient(server.app, raise_server_exceptions=False) as c:
            yield c

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
