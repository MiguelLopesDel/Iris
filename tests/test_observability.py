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
