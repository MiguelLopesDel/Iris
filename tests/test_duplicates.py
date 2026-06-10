from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.duplicates import find_duplicate_groups
from core.search_engine import IrisEngine


def make_duplicate_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT UNIQUE,
            caminho TEXT,
            texto_extraido TEXT,
            descricao_ia TEXT,
            tags TEXT,
            content_hash TEXT,
            embedding BLOB,
            desc_embedding BLOB
        )
        """
    )
    rows = [
        ("a.jpg", "hash-a", np.array([1.0, 0.0, 0.0], dtype=np.float32)),
        ("b.jpg", "hash-b", np.array([0.999, 0.02, 0.0], dtype=np.float32)),
        ("c.jpg", "hash-c", np.array([0.0, 1.0, 0.0], dtype=np.float32)),
        ("d.jpg", "hash-c", np.array([0.0, 0.95, 0.05], dtype=np.float32)),
    ]
    for name, content_hash, embedding in rows:
        conn.execute(
            """
            INSERT INTO memes (
                arquivo, caminho, texto_extraido, descricao_ia, tags,
                content_hash, embedding, desc_embedding
            )
            VALUES (?, ?, '', '', '', ?, ?, ?)
            """,
            (name, name, content_hash, embedding.tobytes(), embedding.tobytes()),
        )
    conn.commit()
    conn.close()


class DuplicateTests(unittest.TestCase):
    def test_find_duplicate_groups_uses_similarity_and_exact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memes.db"
            make_duplicate_db(db_path)
            engine = IrisEngine(db_path=db_path, load_model=False)

            groups = find_duplicate_groups(engine, threshold=0.98, max_neighbors=2, require_existing_files=False)
            grouped_files = [sorted(item.arquivo for item in group.items) for group in groups]

            self.assertIn(["a.jpg", "b.jpg"], grouped_files)
            self.assertIn(["c.jpg", "d.jpg"], grouped_files)


if __name__ == "__main__":
    unittest.main()
