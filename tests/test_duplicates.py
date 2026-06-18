from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import faiss
import numpy as np

from core.duplicates import (
    DuplicateGroup,
    DuplicateItem,
    _merge_by_best_pair,
    find_duplicate_groups,
)
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


def _group(*items: DuplicateItem) -> DuplicateGroup:
    return DuplicateGroup(group_id=0, kind="exact_or_visual", score=1.0, items=list(items))


def _item(index: int, name: str) -> DuplicateItem:
    return DuplicateItem(index=index, arquivo=name, resolved_path=name, score_to_anchor=1.0)


class BestPairMergeTests(unittest.TestCase):
    """Two clusters whose centroids are far apart but which share a near-identical
    cross-cluster item pair must be merged — the case the top-k FAISS window misses."""

    def _matrix(self) -> np.ndarray:
        # A,C anchor opposite subspaces; B and D point the same way (a duplicate pair).
        m = np.array(
            [
                [1.0, 0.0, 0.0, 0.0],  # 0 A
                [0.0, 1.0, 0.0, 0.0],  # 1 B
                [0.0, 0.0, 0.0, 1.0],  # 2 C
                [0.0, 1.0, 0.0, 0.0],  # 3 D == B direction
            ],
            dtype=np.float32,
        )
        faiss.normalize_L2(m)
        return m

    def test_merges_groups_sharing_a_close_cross_pair(self) -> None:
        matrix = self._matrix()
        groups = [
            _group(_item(0, "a.jpg"), _item(1, "b.jpg")),
            _group(_item(2, "c.jpg"), _item(3, "d.jpg")),
        ]
        merged = _merge_by_best_pair(groups, matrix, threshold=0.98)
        self.assertEqual(len(merged), 1)
        names = sorted(it.arquivo for it in merged[0].items)
        self.assertEqual(names, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])

    def test_keeps_groups_without_a_close_cross_pair(self) -> None:
        matrix = self._matrix()
        # D now points away from B, so no cross-group pair clears the threshold.
        matrix[3] = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
        faiss.normalize_L2(matrix)
        groups = [
            _group(_item(0, "a.jpg"), _item(1, "b.jpg")),
            _group(_item(2, "c.jpg"), _item(3, "d.jpg")),
        ]
        merged = _merge_by_best_pair(groups, matrix, threshold=0.98)
        self.assertEqual(len(merged), 2)

    def test_never_bridges_different_media_types(self) -> None:
        matrix = self._matrix()  # item 1 and item 3 are identical direction
        groups = [
            _group(_item(0, "a.jpg"), _item(1, "b.jpg")),
            _group(_item(2, "c.mp4"), _item(3, "d.mp4")),
        ]
        merged = _merge_by_best_pair(groups, matrix, threshold=0.98)
        self.assertEqual(len(merged), 2)


if __name__ == "__main__":
    unittest.main()
