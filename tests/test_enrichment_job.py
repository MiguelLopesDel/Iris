"""Deterministic tests for the web-enrichment job orchestration.

These cover the branching logic in ``server._run_web_enrichment_job`` (cache
skip, reuse-sources/re-distill, fresh Lens search, error handling) with a fake
service -- no network and no browser. If these pass, the job wiring works.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest
from conftest import make_enrichment_conn

import server
from core.web_enrichment import (
    EnrichmentSuggestion,
    WebSource,
    create_job,
    insert_suggestion,
    list_suggestions,
)


class _FakeService:
    """Records which path the job took, without touching Lens/AI."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def enrich_path(self, path, vocabulary=None) -> EnrichmentSuggestion:
        self.calls.append(("enrich_path", str(path)))
        return EnrichmentSuggestion(
            provider="lens",
            character="FromLens",
            sources=(WebSource(title="m", url="https://knowyourmeme.com/x"),),
        )

    def redistill(self, sources, vocabulary=None) -> EnrichmentSuggestion:
        self.calls.append(("redistill", len(sources)))
        return EnrichmentSuggestion(provider="redistill", character="FromRedistill")


@pytest.fixture
def wired(monkeypatch):
    """Wire the job runner to an in-memory DB, a fake record and a fake service."""
    conn = make_enrichment_conn()
    real_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    real_file.write(b"x")
    real_file.close()
    service = _FakeService()

    monkeypatch.setattr(server, "_backend_connection", lambda *a, **k: conn)
    monkeypatch.setattr(server, "_create_web_enrichment_service", lambda *a, **k: service)
    monkeypatch.setattr(
        server,
        "_record_by_db_id",
        lambda db_id: SimpleNamespace(arquivo="x.jpg", resolved_path=real_file.name),
    )
    yield SimpleNamespace(conn=conn, service=service)
    os.unlink(real_file.name)


def _seed_suggestion(conn, *, with_sources: bool) -> None:
    sources = (
        (WebSource(title="Gojo", url="https://x.fandom.com", domain="x.fandom.com"),)
        if with_sources
        else ()
    )
    insert_suggestion(
        conn, "old-job", 1,
        EnrichmentSuggestion(provider="lens", character="Old", sources=sources),
    )


def test_job_skips_cached_when_not_forced(wired) -> None:
    _seed_suggestion(wired.conn, with_sources=True)
    job_id = create_job(wired.conn, [1])

    server._run_web_enrichment_job(job_id, [1], force=False)

    assert wired.service.calls == []  # neither Lens nor AI ran


def test_job_reuses_sources_on_force_without_research(wired) -> None:
    _seed_suggestion(wired.conn, with_sources=True)
    job_id = create_job(wired.conn, [1])

    server._run_web_enrichment_job(job_id, [1], force=True, research=False)

    # Re-distilled the cached source; never re-opened Lens.
    assert wired.service.calls == [("redistill", 1)]
    latest = list_suggestions(wired.conn, "pending")[0]
    assert latest["character"] == "FromRedistill"


def test_job_does_fresh_lens_search_when_research(wired) -> None:
    _seed_suggestion(wired.conn, with_sources=True)
    job_id = create_job(wired.conn, [1])

    server._run_web_enrichment_job(job_id, [1], force=True, research=True)

    assert wired.service.calls[0][0] == "enrich_path"  # fresh Lens, not redistill


def test_job_first_time_runs_lens(wired) -> None:
    job_id = create_job(wired.conn, [1])  # no prior suggestion/sources

    server._run_web_enrichment_job(job_id, [1], force=False)

    assert len(wired.service.calls) == 1
    assert wired.service.calls[0][0] == "enrich_path"


def test_job_records_error_suggestion_on_failure(wired, monkeypatch) -> None:
    def boom(path, vocabulary=None):
        raise RuntimeError("falha simulada")

    wired.service.enrich_path = boom  # type: ignore[method-assign]
    job_id = create_job(wired.conn, [1])

    server._run_web_enrichment_job(job_id, [1], force=False)

    suggestion = list_suggestions(wired.conn, "pending")[0]
    assert "falha simulada" in suggestion["error_message"]
