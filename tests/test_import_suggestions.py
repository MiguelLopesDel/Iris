from __future__ import annotations

import json
import sqlite3
import unittest

from core.import_suggestions import suggest_collections


def _make_conn(rows: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE memes (id INTEGER PRIMARY KEY, imported_at TEXT, metadata_json TEXT)"
    )
    for i, meta in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO memes (id, imported_at, metadata_json) VALUES (?, '2026-06-18T00:00:00', ?)",
            (i, json.dumps(meta)),
        )
    conn.commit()
    return conn


class SuggestionTests(unittest.TestCase):
    def test_groups_by_date_app_location_device(self) -> None:
        rows = [
            {"captured_at": "2026-06-01T10:00:00", "source_app": "WhatsApp", "device": "Apple iPhone 13"},
            {"captured_at": "2026-06-15T10:00:00", "source_app": "WhatsApp", "device": "Apple iPhone 13"},
            {"captured_at": "2026-06-20T10:00:00", "source_app": "WhatsApp", "device": "Apple iPhone 13"},
        ]
        suggestions = suggest_collections(_make_conn(rows), min_count=3)
        names = {s["name"] for s in suggestions}
        self.assertIn("Junho 2026", names)
        self.assertIn("Mídias do WhatsApp", names)
        self.assertIn("Fotos do Apple iPhone 13", names)

    def test_location_name_uses_city(self) -> None:
        rows = [{"location_label": "Lisboa, PT"} for _ in range(4)]
        suggestions = suggest_collections(_make_conn(rows), min_count=3)
        location = next(s for s in suggestions if s["dimension"] == "location")
        self.assertEqual(location["name"], "Fotos em Lisboa")
        self.assertEqual(location["count"], 4)

    def test_screenshot_naming(self) -> None:
        rows = [{"source_app": "Captura de tela"} for _ in range(3)]
        suggestions = suggest_collections(_make_conn(rows), min_count=3)
        self.assertEqual(suggestions[0]["name"], "Capturas de tela")

    def test_min_count_filters_small_groups(self) -> None:
        rows = [
            {"source_app": "Instagram"},
            {"source_app": "Instagram"},  # only 2 — below default threshold of 3
            {"source_app": "WhatsApp"},
            {"source_app": "WhatsApp"},
            {"source_app": "WhatsApp"},
        ]
        suggestions = suggest_collections(_make_conn(rows), min_count=3)
        names = {s["name"] for s in suggestions}
        self.assertIn("Mídias do WhatsApp", names)
        self.assertNotIn("Mídias do Instagram", names)

    def test_db_ids_reflect_members(self) -> None:
        rows = [{"source_app": "WhatsApp"} for _ in range(3)]
        suggestions = suggest_collections(_make_conn(rows), min_count=3)
        app = next(s for s in suggestions if s["dimension"] == "source_app")
        self.assertEqual(sorted(app["db_ids"]), [1, 2, 3])

    def test_missing_metadata_column_returns_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY)")
        self.assertEqual(suggest_collections(conn, since="2026-01-01"), [])


if __name__ == "__main__":
    unittest.main()
