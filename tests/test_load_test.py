from __future__ import annotations

import json

from scripts.load_test import Sample, load_accounts, make_path, summarize


def test_mixed_load_scenario_has_browse_and_search_requests():
    assert make_path("mixed", 0, "foto").startswith("/api/search?")
    assert make_path("mixed", 1, "foto").startswith("/api/records?")


def test_load_summary_reports_percentiles_and_errors():
    report = summarize(
        [Sample(10, 200), Sample(20, 200), Sample(50, 500)],
        elapsed_sec=1.0,
        scenario="browse",
        concurrency=2,
    )
    assert report["successes"] == 2
    assert report["errors"] == {"HTTP 500": 1}
    assert report["latency_ms"]["p95"] == 50


def test_load_accounts_reads_disposable_credentials(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([{"username": "load-001", "password": "secret"}]))
    assert load_accounts(path) == [("load-001", "secret")]
