from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.search_engine import IrisEngine, SearchOptions, normalize_text, parse_query_terms


def make_db(path: Path, media_root: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE media_libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            root_path TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO media_libraries (name, root_path, created_at)
        VALUES ('default', ?, '2026-01-01T00:00:00Z')
        """,
        (str(media_root),),
    )
    conn.execute(
        """
        CREATE TABLE memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT UNIQUE,
            caminho TEXT,
            relative_path TEXT,
            storage_path TEXT,
            library_id INTEGER,
            texto_extraido TEXT,
            descricao_ia TEXT,
            tags TEXT,
            embedding BLOB,
            desc_embedding BLOB
        )
        """
    )
    rows = [
        (
            "cat.jpg",
            str(media_root / "cat.jpg"),
            "cat.jpg",
            "cat.jpg",
            1,
            "gato bravo",
            "angry cat reaction",
            "cat, angry, reaction",
            np.array([1.0, 0.0, 0.0], dtype=np.float32),
            np.array([1.0, 0.0, 0.0], dtype=np.float32),
        ),
        (
            "dog.jpg",
            str(media_root / "dog.jpg"),
            "dog.jpg",
            "dog.jpg",
            1,
            "cachorro feliz",
            "happy dog meme",
            "dog, happy",
            np.array([0.0, 1.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0, 0.0], dtype=np.float32),
        ),
    ]
    for row in rows:
        conn.execute(
            """
            INSERT INTO memes (
                arquivo, caminho, relative_path, storage_path, library_id,
                texto_extraido, descricao_ia, tags, embedding, desc_embedding
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*row[:8], row[8].tobytes(), row[9].tobytes()),
        )
    conn.commit()
    conn.close()


class SearchEngineTests(unittest.TestCase):
    def test_normalize_text_removes_accents_and_punctuation(self) -> None:
        self.assertEqual(normalize_text("Cachorro, NÃO!"), "cachorro nao")

    def test_parse_query_terms_splits_negative_terms(self) -> None:
        positive, negative = parse_query_terms("gato bravo -preto -ruim")
        self.assertEqual(positive, "gato bravo")
        self.assertEqual(negative, ["preto", "ruim"])

    def test_embedding_search_ranks_and_filters_negative_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "media"
            media.mkdir()
            (media / "cat.jpg").write_bytes(b"fake")
            db_path = root / "memes.db"
            make_db(db_path, media)

            engine = IrisEngine(db_path=db_path, media_root=media, load_model=False)
            options = SearchOptions(top_k=5, threshold=-1.0, balance=0.5, text_bonus=2.0)
            query = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            results = engine.search_by_embedding(
                query,
                options,
                text_query="gato bravo",
                translated_query="angry cat",
                negative_terms=[],
            )
            self.assertEqual(results[0].arquivo, "cat.jpg")
            self.assertTrue(results[0].resolved_path.endswith("cat.jpg"))
            self.assertIn("lexical", results[0].score_details)

            filtered = engine.search_by_embedding(
                query,
                options,
                text_query="gato bravo",
                translated_query="angry cat",
                negative_terms=["gato"],
            )
            self.assertNotEqual(filtered[0].arquivo, "cat.jpg")

    def test_resolve_prefers_library_storage_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "library_default"
            media.mkdir()
            (media / "cat.jpg").write_bytes(b"fake")
            db_path = root / "memes.db"
            make_db(db_path, media)

            engine = IrisEngine(db_path=db_path, media_root=root / "other", load_model=False)
            cat = next(record for record in engine.records if record.arquivo == "cat.jpg")
            self.assertEqual(Path(cat.resolved_path).resolve(), (media / "cat.jpg").resolve())


if __name__ == "__main__":
    unittest.main()
