"""HTTP endpoint tests for the web-enrichment API.

Exercise the real FastAPI routes (validation, response shape, flag wiring) with a
mocked backend connection -- no CLIP/DB/network. The background job is stubbed so
we test request handling deterministically.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from conftest import make_enrichment_conn

import server
from core.web_enrichment import EnrichmentSuggestion, WebSource, insert_suggestion


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
def ctx(monkeypatch):
    conn = make_enrichment_conn(check_same_thread=False)
    mock = MagicMock()
    mock.engine.db.get_connection.return_value = conn
    mock.get_all_records.return_value = []
    server._backend = mock

    captured: dict = {}
    done = threading.Event()

    def capture_job(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        done.set()

    # No missing config and no real job execution.
    monkeypatch.setattr(
        server, "_create_web_enrichment_service",
        lambda *a, **k: SimpleNamespace(missing_config=lambda: []),
    )
    monkeypatch.setattr(server, "_run_web_enrichment_job", capture_job)

    return SimpleNamespace(client=ASGITestClient(server.app), conn=conn,
                           captured=captured, done=done)


def test_create_job_rejects_when_no_valid_ids(ctx) -> None:
    r = ctx.client.post("/api/enrichment/jobs", data={"db_ids": "abc,-,x"})
    assert r.status_code == 400


def test_create_job_rejects_when_provider_config_missing(ctx, monkeypatch) -> None:
    monkeypatch.setattr(
        server, "_create_web_enrichment_service",
        lambda *a, **k: SimpleNamespace(missing_config=lambda: ["SERPAPI_KEY"]),
    )
    r = ctx.client.post("/api/enrichment/jobs", data={"db_ids": "1"})
    assert r.status_code == 400
    assert "SERPAPI_KEY" in r.json()["detail"]


def test_create_job_returns_shape_and_passes_flags(ctx) -> None:
    r = ctx.client.post(
        "/api/enrichment/jobs",
        data={"db_ids": "1", "force": "1", "research": "1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"job_id", "total", "cached", "force", "research"}
    assert body["total"] == 1 and body["force"] is True and body["research"] is True

    assert ctx.done.wait(timeout=5)
    job_id, ids, force, overrides, research = ctx.captured["args"]
    assert ids == [1] and force is True and research is True


def test_create_job_skips_provider_config_for_pure_redistill(ctx, monkeypatch) -> None:
    # An image that already has sources -> a re-distill needs no provider config,
    # so a missing provider config must NOT block it.
    insert_suggestion(
        ctx.conn, "old", 1,
        EnrichmentSuggestion(
            provider="lens",
            sources=(WebSource(title="g", url="https://x.fandom.com"),),
        ),
    )
    monkeypatch.setattr(
        server, "_create_web_enrichment_service",
        lambda *a, **k: SimpleNamespace(missing_config=lambda: ["SERPAPI_KEY"]),
    )
    r = ctx.client.post(
        "/api/enrichment/jobs", data={"db_ids": "1", "force": "1", "research": ""}
    )
    assert r.status_code == 200  # not blocked by the missing provider config


def test_get_job_404_for_unknown(ctx) -> None:
    assert ctx.client.get("/api/enrichment/jobs/nope").status_code == 404


def test_suggestions_list_apply_reject_flow(ctx) -> None:
    sid = insert_suggestion(
        ctx.conn, "job1", 1,
        EnrichmentSuggestion(provider="lens", character="Gojo", tags="gojo"),
    )
    listed = ctx.client.get("/api/enrichment/suggestions", params={"status": "pending"})
    assert listed.status_code == 200
    assert any(s["id"] == sid for s in listed.json()["suggestions"])

    rejected = ctx.client.post(f"/api/enrichment/suggestions/{sid}/reject")
    assert rejected.status_code == 200
    assert ctx.client.get(
        "/api/enrichment/suggestions", params={"status": "pending"}
    ).json()["suggestions"] == []
