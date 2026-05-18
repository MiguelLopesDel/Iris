from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.evaluation import EvalQuery, SearchEvalResult, aggregate_metrics, load_eval_queries


class EvaluationTests(unittest.TestCase):
    def test_load_eval_queries_accepts_expected_files_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queries.json"
            path.write_text(
                json.dumps(
                    {
                        "queries": [
                            {
                                "query": "gato bravo",
                                "expected_files": "cat.jpg",
                                "kind": "manual",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            queries = load_eval_queries(path)
            self.assertEqual(queries[0].expected, ["cat.jpg"])
            self.assertEqual(queries[0].kind, "manual")

    def test_load_eval_queries_reads_category_and_image_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queries.json"
            path.write_text(
                json.dumps(
                    {
                        "queries": [
                            {
                                "query": "codigo c fork",
                                "expected": ["code.jpg"],
                                "kind": "manual",
                                "category": "literal_ocr",
                                "image_id": "code.jpg",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            query = load_eval_queries(path)[0]
            self.assertIsInstance(query, EvalQuery)
            self.assertEqual(query.category, "literal_ocr")
            self.assertEqual(query.image_id, "code.jpg")

    def test_aggregate_metrics_reports_recall_at_20(self) -> None:
        rows = [
            SearchEvalResult(
                query="a",
                expected=["a.jpg"],
                kind="manual",
                category="memory",
                image_id="a.jpg",
                found_rank=20,
                top_files=["x.jpg"],
                latency_ms=10.0,
            ),
            SearchEvalResult(
                query="b",
                expected=["b.jpg"],
                kind="manual",
                category="memory",
                image_id="b.jpg",
                found_rank=-1,
                top_files=[],
                latency_ms=20.0,
            ),
        ]
        metrics = aggregate_metrics(rows, (10, 20))
        self.assertEqual(metrics["recall_at_10"], 0.0)
        self.assertEqual(metrics["recall_at_20"], 0.5)


if __name__ == "__main__":
    unittest.main()
