"""Search-quality tests — does the engine actually return the right images, in the
right order, respecting proximity?

These run without the CLIP model: we feed hand-crafted embeddings whose geometry we
control, query with an explicit vector via ``search_by_embedding`` (the same ranking
path ``search_text``/``search_image`` use after encoding), and assert on the resulting
order/score. That makes the ranking quality deterministic and CI-safe.

The last test is a real acceptance gate over a golden set (recall@10/@20). It needs a
GPU-built index + a filled golden query file, so it's skipped unless these env vars are
set:
    IRIS_EVAL_DB           path to an indexed .db
    IRIS_EVAL_QUERIES      path to a golden queries.json (expected files per query)
    IRIS_EVAL_MEDIA_ROOT   (optional) media root, default "media"
    IRIS_EVAL_MIN_RECALL10 (optional) default 0.90
    IRIS_EVAL_MIN_RECALL20 (optional) default 0.95
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.evaluation import SearchEvalResult, aggregate_metrics
from core.search_engine import IrisEngine, SearchOptions


def _vec(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def build_quality_engine(tmp: Path, specs: list[dict]) -> IrisEngine:
    """Create a throwaway DB + engine from explicit per-record embeddings.

    Each spec: {arquivo, image, desc?, ocr?, tags?, descricao?}. ``desc`` defaults to
    ``image`` so the description channel mirrors the visual one unless a test cares.
    """
    media = tmp / "media"
    media.mkdir(exist_ok=True)
    db_path = tmp / "quality.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE media_libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE, root_path TEXT, created_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO media_libraries (name, root_path, created_at) VALUES ('default', ?, '2026-01-01T00:00:00Z')",
        (str(media),),
    )
    conn.execute(
        """
        CREATE TABLE memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT UNIQUE, caminho TEXT, relative_path TEXT, storage_path TEXT,
            library_id INTEGER, texto_extraido TEXT, descricao_ia TEXT, tags TEXT,
            embedding BLOB, desc_embedding BLOB
        )
        """
    )
    for spec in specs:
        name = spec["arquivo"]
        (media / name).write_bytes(b"fake")
        image = spec["image"].astype(np.float32)
        desc = spec.get("desc", spec["image"]).astype(np.float32)
        conn.execute(
            """
            INSERT INTO memes (
                arquivo, caminho, relative_path, storage_path, library_id,
                texto_extraido, descricao_ia, tags, embedding, desc_embedding
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                name, str(media / name), name, name,
                spec.get("ocr", ""), spec.get("descricao", ""), spec.get("tags", ""),
                image.tobytes(), desc.tobytes(),
            ),
        )
    conn.commit()
    conn.close()
    return IrisEngine(db_path=db_path, media_root=media, load_model=False)


def _opts(**kw) -> SearchOptions:
    base = dict(top_k=50, threshold=-1.0, balance=1.0, text_bonus=2.0, lexical_weight=0.25)
    base.update(kw)
    return SearchOptions(**base)


class ProximityOrderingTests(unittest.TestCase):
    """Closer images must rank higher; scores must decrease monotonically."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        # Embeddings at increasing angles from the query axis [1,0,0,0].
        self.engine = build_quality_engine(tmp, [
            {"arquivo": "exact.jpg", "image": _vec(1.0, 0.0, 0.0, 0.0)},      # cos 1.00
            {"arquivo": "near.jpg", "image": _vec(0.8, 0.6, 0.0, 0.0)},       # cos 0.80
            {"arquivo": "mid.jpg", "image": _vec(0.5, 0.866, 0.0, 0.0)},      # cos 0.50
            {"arquivo": "far.jpg", "image": _vec(0.0, 1.0, 0.0, 0.0)},        # cos 0.00
        ])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, **kw):
        return self.engine.search_by_embedding(_vec(1.0, 0.0, 0.0, 0.0), _opts(**kw))

    def test_results_ordered_by_visual_proximity(self) -> None:
        order = [r.arquivo for r in self._run()]
        self.assertEqual(order, ["exact.jpg", "near.jpg", "mid.jpg", "far.jpg"])

    def test_scores_decrease_monotonically(self) -> None:
        scores = [r.score for r in self._run()]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # Pure-visual score should equal cosine similarity (balance=1, no text query).
        self.assertAlmostEqual(scores[0], 1.0, places=4)
        self.assertAlmostEqual(scores[-1], 0.0, places=4)

    def test_nearest_neighbor_is_rank_one(self) -> None:
        self.assertEqual(self._run()[0].arquivo, "exact.jpg")

    def test_threshold_excludes_distant_results(self) -> None:
        kept = {r.arquivo for r in self._run(threshold=0.5)}
        self.assertEqual(kept, {"exact.jpg", "near.jpg", "mid.jpg"})  # >= 0.5 only
        self.assertNotIn("far.jpg", kept)

    def test_top_k_caps_result_count(self) -> None:
        self.assertEqual(len(self._run(top_k=2)), 2)


class TextRelevanceTests(unittest.TestCase):
    """When the user's words appear in OCR/tags, that image should win ties."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        # Identical visual proximity — only the text metadata differs.
        self.engine = build_quality_engine(tmp, [
            {"arquivo": "plain.jpg", "image": _vec(1.0, 0.0, 0.0, 0.0)},
            {"arquivo": "match.jpg", "image": _vec(1.0, 0.0, 0.0, 0.0),
             "ocr": "gato bravo", "tags": "gato, bravo"},
        ])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_lexical_match_outranks_visually_equal_result(self) -> None:
        results = self.engine.search_by_embedding(
            _vec(1.0, 0.0, 0.0, 0.0), _opts(), text_query="gato bravo",
        )
        self.assertEqual(results[0].arquivo, "match.jpg")
        self.assertGreater(results[0].score, results[1].score)
        self.assertGreater(float(results[0].score_details["lexical"]), 0.0)

    def test_no_text_query_keeps_visual_tie_stable(self) -> None:
        # Without a text query the two are visually identical; neither gets a boost.
        results = self.engine.search_by_embedding(_vec(1.0, 0.0, 0.0, 0.0), _opts())
        self.assertAlmostEqual(results[0].score, results[1].score, places=5)


class BalanceBlendTests(unittest.TestCase):
    """The balance knob must move ranking between the visual and description channels."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        # "visual" wins on the image channel; "textual" wins on the description channel.
        self.engine = build_quality_engine(tmp, [
            {"arquivo": "visual.jpg", "image": _vec(1.0, 0.0, 0.0, 0.0), "desc": _vec(0.0, 1.0, 0.0, 0.0)},
            {"arquivo": "textual.jpg", "image": _vec(0.0, 1.0, 0.0, 0.0), "desc": _vec(1.0, 0.0, 0.0, 0.0)},
        ])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_visual_bias_prefers_image_channel(self) -> None:
        order = [r.arquivo for r in self.engine.search_by_embedding(_vec(1.0, 0.0, 0.0, 0.0), _opts(balance=1.0))]
        self.assertEqual(order[0], "visual.jpg")

    def test_description_bias_prefers_desc_channel(self) -> None:
        order = [r.arquivo for r in self.engine.search_by_embedding(_vec(1.0, 0.0, 0.0, 0.0), _opts(balance=0.0))]
        self.assertEqual(order[0], "textual.jpg")


class RecallHarnessTests(unittest.TestCase):
    """Tie the engine output to the evaluation metrics: a query whose expected image is
    the nearest neighbour must score recall@1 = 1.0 and MRR = 1.0."""

    def test_engine_output_feeds_recall_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            engine = build_quality_engine(Path(name), [
                {"arquivo": "apple.jpg", "image": _vec(1.0, 0.0, 0.0, 0.0)},
                {"arquivo": "banana.jpg", "image": _vec(0.0, 1.0, 0.0, 0.0)},
                {"arquivo": "cherry.jpg", "image": _vec(0.0, 0.0, 1.0, 0.0)},
            ])
            cases = [
                (_vec(1.0, 0.0, 0.0, 0.0), "apple.jpg"),
                (_vec(0.0, 1.0, 0.0, 0.0), "banana.jpg"),
                (_vec(0.0, 0.0, 1.0, 0.0), "cherry.jpg"),
            ]
            rows: list[SearchEvalResult] = []
            for query_vec, expected in cases:
                found = engine.search_by_embedding(query_vec, _opts())
                top_files = [r.arquivo for r in found]
                rank = top_files.index(expected) + 1 if expected in top_files else -1
                rows.append(SearchEvalResult(
                    query=expected, expected=[expected], kind="synthetic",
                    category="proximity", image_id=expected, found_rank=rank,
                    top_files=top_files, latency_ms=0.0,
                ))
            metrics = aggregate_metrics(rows, (1, 5))
            self.assertEqual(metrics["recall_at_1"], 1.0)
            self.assertEqual(metrics["mrr"], 1.0)


@unittest.skipUnless(
    os.environ.get("IRIS_EVAL_DB") and os.environ.get("IRIS_EVAL_QUERIES"),
    "real eval index not configured (set IRIS_EVAL_DB and IRIS_EVAL_QUERIES)",
)
class GoldenSetAcceptanceTests(unittest.TestCase):
    """Real quality gate against a GPU-built index + a human golden set."""

    def test_golden_set_meets_recall_thresholds(self) -> None:
        from core.evaluation import evaluate_search, load_eval_queries

        engine = IrisEngine(
            db_path=os.environ["IRIS_EVAL_DB"],
            media_root=os.environ.get("IRIS_EVAL_MEDIA_ROOT", "media"),
        )
        queries = load_eval_queries(Path(os.environ["IRIS_EVAL_QUERIES"]))
        report = evaluate_search(
            engine, queries,
            SearchOptions(top_k=20, threshold=-1.0, balance=0.5, text_bonus=2.0, lexical_weight=0.25),
            recall_ks=(1, 5, 10, 20),
        )
        metrics = report["metrics"]
        min10 = float(os.environ.get("IRIS_EVAL_MIN_RECALL10", "0.90"))
        min20 = float(os.environ.get("IRIS_EVAL_MIN_RECALL20", "0.95"))
        self.assertGreaterEqual(
            metrics["recall_at_10"], min10,
            msg=f"recall@10={metrics['recall_at_10']:.3f} < {min10}; falhas: {report['failures']}",
        )
        self.assertGreaterEqual(metrics["recall_at_20"], min20)


if __name__ == "__main__":
    unittest.main()
