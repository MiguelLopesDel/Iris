from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill_metadata import run_backfill  # noqa: E402


def _make_db(rows: list[tuple[int, str]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, metadata_json TEXT)")
    conn.executemany("INSERT INTO memes (id, metadata_json) VALUES (?, ?)", rows)
    conn.commit()
    return conn


class BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        # An image with EXIF (Software → app), a plain image (no metadata).
        self.with_exif = d / "photo.jpg"
        exif = Image.Exif()
        exif[305] = "WhatsApp"
        exif[306] = "2026:06:18 14:30:00"
        Image.new("RGB", (8, 8), (1, 2, 3)).save(self.with_exif, exif=exif)
        self.plain = d / "plain.png"
        Image.new("RGB", (8, 8), (4, 5, 6)).save(self.plain)
        self.paths = {1: str(self.with_exif), 2: str(self.plain), 3: str(d / "gone.jpg")}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _resolver(self, meme_id: int):
        return self.paths.get(meme_id)

    def test_dry_run_reports_without_writing(self) -> None:
        conn = _make_db([(1, ""), (2, ""), (3, "")])
        stats = run_backfill(conn, self._resolver, apply=False)
        self.assertEqual(stats["scanned"], 3)
        self.assertEqual(stats["found"], 1)       # only photo.jpg has metadata
        self.assertEqual(stats["updated"], 0)     # dry-run writes nothing
        self.assertEqual(stats["missing"], 1)     # gone.jpg
        self.assertEqual(stats["no_metadata"], 1)  # plain.png
        stored = conn.execute("SELECT metadata_json FROM memes WHERE id = 1").fetchone()[0]
        self.assertEqual(stored, "")

    def test_apply_writes_metadata(self) -> None:
        conn = _make_db([(1, ""), (2, ""), (3, "")])
        stats = run_backfill(conn, self._resolver, apply=True)
        self.assertEqual(stats["updated"], 1)
        meta = json.loads(conn.execute("SELECT metadata_json FROM memes WHERE id = 1").fetchone()[0])
        self.assertEqual(meta["source_app"], "WhatsApp")
        self.assertEqual(meta["captured_at"], "2026-06-18T14:30:00")
        # rows without usable metadata are left untouched (retryable later)
        self.assertEqual(conn.execute("SELECT metadata_json FROM memes WHERE id = 2").fetchone()[0], "")

    def test_only_empty_skips_already_filled(self) -> None:
        conn = _make_db([(1, '{"source_app": "x"}')])
        stats = run_backfill(conn, self._resolver, apply=True, only_empty=True)
        self.assertEqual(stats["scanned"], 0)  # row already has metadata_json

    def test_missing_column_added_on_apply(self) -> None:
        # Simulate a pre-feature DB: memes without the metadata_json column.
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO memes (id) VALUES (?)", [(1,), (2,)])
        conn.commit()

        # Dry-run must not alter the schema.
        run_backfill(conn, self._resolver, apply=False)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memes)")}
        self.assertNotIn("metadata_json", cols)

        # Apply adds the column and fills it.
        stats = run_backfill(conn, self._resolver, apply=True)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memes)")}
        self.assertIn("metadata_json", cols)
        self.assertEqual(stats["updated"], 1)


if __name__ == "__main__":
    unittest.main()
