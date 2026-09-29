from __future__ import annotations

import json
import logging

from core.observability import JsonFormatter, request_path


def test_json_logs_include_operational_fields_without_unrelated_record_data():
    record = logging.LogRecord("iris", logging.INFO, __file__, 1, "completed", (), None)
    record.event = "http_request_completed"
    record.request_id = "abc123"
    record.path = "/api/info"
    record.status_code = 200
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "http_request_completed"
    assert payload["request_id"] == "abc123"
    assert payload["path"] == "/api/info"
    assert payload["status_code"] == 200


def test_request_path_drops_query_string():
    assert request_path("/api/search?q=private+query") == "/api/search"


def test_json_logs_keep_safe_sync_phase_metrics_but_omit_unapproved_fields():
    record = logging.LogRecord("iris", logging.INFO, __file__, 1, "phase", (), None)
    record.event = "sync_phase_completed"
    record.phase = "chunk_durable"
    record.phase_ms = 12.3
    record.bytes = 1024
    record.item_count = 1
    record.local_path = "/private/photo.jpg"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["phase"] == "chunk_durable"
    assert payload["phase_ms"] == 12.3
    assert payload["bytes"] == 1024
    assert payload["item_count"] == 1
    assert "local_path" not in payload
