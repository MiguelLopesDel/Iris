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


class DescriptionMatrixGapTests(unittest.TestCase):
    """One missing description embedding must not disable the whole catalogue."""

    def _engine(self, with_gap: bool):
        import numpy as np

        from core.search_engine import IrisEngine

        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "memes.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE memes (id INTEGER PRIMARY KEY, arquivo TEXT, caminho TEXT,"
            " texto_extraido TEXT, descricao_ia TEXT, embedding BLOB, desc_embedding BLOB)"
        )
        vector = np.ones(8, dtype=np.float32).tobytes()
        rows = [
            ("a.jpg", vector, vector),
            ("b.jpg", vector, None if with_gap else vector),
            ("c.jpg", vector, vector),
        ]
        for name, emb, desc in rows:
            conn.execute(
                "INSERT INTO memes (arquivo, caminho, texto_extraido, descricao_ia,"
                " embedding, desc_embedding) VALUES (?, ?, '', '', ?, ?)",
                (name, name, emb, desc),
            )
        conn.commit()
        conn.close()
        return IrisEngine(db_path=db_path, load_model=False)

    def test_a_gap_leaves_the_matrix_usable(self) -> None:
        """It used to return None for the matrix, switching off search entirely."""
        engine = self._engine(with_gap=True)

        self.assertIsNotNone(engine.desc_matrix)
        self.assertEqual(engine.desc_matrix.shape[0], 3)
        # The item without a description simply never matches.
        self.assertEqual(float(engine.desc_matrix[1].sum()), 0.0)

    def test_records_share_memory_with_the_matrix(self) -> None:
        """A second copy per record doubled what an open catalogue costs."""
        engine = self._engine(with_gap=False)

        for position, record in enumerate(engine.records):
            self.assertIs(record.embedding.base, engine.image_matrix)
            self.assertTrue((record.embedding == engine.image_matrix[position]).all())

    def test_an_item_without_a_description_is_judged_on_its_image_alone(self) -> None:
        """Never blended against a stand-in value.

        A zero row keeps the matrix aligned with the records, but it is not a
        neutral score: cosine is signed, so zero outranks anything genuinely
        dissimilar. Measured before the fix, an item with no description ranked
        second against a query opposed to the catalogue, beating two real items
        whose scores were negative.

        A sentinel score does not solve it either, because the value survives
        the blend: -2.0 at a balance of 0.5 subtracts a full point from the
        item, and at a balance of 1.0 subtracts nothing. The decision belongs
        where the score is combined, not inside the numbers.
        """
        engine = self._engine(with_gap=True)

        self.assertTrue(engine._has_description(0))
        self.assertFalse(engine._has_description(1))
        self.assertTrue(engine._has_description(2))

    def test_a_catalogue_without_any_gap_needs_no_check(self) -> None:
        engine = self._engine(with_gap=False)

        self.assertTrue(all(engine._has_description(i) for i in range(len(engine.records))))


class WeightedSignalMeanTests(unittest.TestCase):
    """Ranking averages the signals an item has, never a stand-in for one it lacks.

    The rule arrived through three wrong answers: None, then zero, then a
    sentinel below the cosine domain. Each was an attempt to pick a value that
    represents absence, and the problem was that absence should not be in the
    arithmetic at all.
    """

    def test_both_signals_present_is_the_ordinary_blend(self) -> None:
        from core.search_engine import _weighted_mean

        result = _weighted_mean([(0.5, True, 1.0), (0.5, True, 0.0)])

        self.assertAlmostEqual(result, 0.5)

    def test_a_missing_signal_does_not_drag_the_score_down(self) -> None:
        """The item is judged on what it has, not penalised for what it lacks."""
        from core.search_engine import _weighted_mean

        with_desc = _weighted_mean([(0.5, True, 0.8), (0.5, True, 0.8)])
        without_desc = _weighted_mean([(0.5, True, 0.8), (0.5, False, 0.0)])

        self.assertAlmostEqual(with_desc, without_desc)

    def test_a_zero_weight_signal_is_not_averaged_in(self) -> None:
        from core.search_engine import _weighted_mean

        result = _weighted_mean([(1.0, True, 0.9), (0.0, True, -0.9)])

        self.assertAlmostEqual(result, 0.9)

    def test_no_available_signal_carries_weight_means_no_score(self) -> None:
        """balance=0 on an item with no description.

        Falling back to the image would answer a question the user did not ask:
        they set the image weight to zero.
        """
        from core.search_engine import _weighted_mean

        self.assertIsNone(_weighted_mean([(0.0, True, 0.9), (1.0, False, 0.0)]))

    def test_the_mirror_case_is_also_refused(self) -> None:
        """balance=1 on an item that only has a description."""
        from core.search_engine import _weighted_mean

        self.assertIsNone(_weighted_mean([(1.0, False, 0.0), (0.0, True, 0.9)]))

    def test_an_item_with_no_description_is_dropped_when_balance_is_zero(self) -> None:
        """End to end, not just the helper."""
        import numpy as np

        from core.search_engine import SearchOptions

        engine = DescriptionMatrixGapTests._engine(DescriptionMatrixGapTests(), with_gap=True)
        query = np.ones((1, 8), dtype=np.float32)
        options = SearchOptions(top_k=10, threshold=-2.0, balance=0.0, text_bonus=1.0, lexical_weight=0.0)

        scores, _ = engine._score_candidates(
            query, [0, 1, 2], options, text_query="", translated_query="", negative_terms=[]
        )

        self.assertNotIn(1, scores, "item sem descrição pontuou com peso de imagem zero")
        self.assertIn(0, scores)
        self.assertIn(2, scores)
