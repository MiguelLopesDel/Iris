from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.search_engine import MemeSearchEngine, SearchOptions


@dataclass(frozen=True)
class EvalQuery:
    query: str
    expected: list[str]
    kind: str = "human"
    category: str = "general"
    image_id: str = ""
    note: str = ""


@dataclass(frozen=True)
class SearchEvalResult:
    query: str
    expected: list[str]
    kind: str
    category: str
    image_id: str
    found_rank: int
    top_files: list[str]
    latency_ms: float


def load_eval_queries(path: Path) -> list[EvalQuery]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["queries"] if isinstance(payload, dict) else payload
    queries: list[EvalQuery] = []
    for row in rows:
        expected = row.get("expected", row.get("expected_files", []))
        if isinstance(expected, str):
            expected = [expected]
        queries.append(
            EvalQuery(
                query=row["query"],
                expected=list(expected),
                kind=row.get("kind", "human"),
                category=row.get("category", row.get("kind", "general")),
                image_id=row.get("image_id", ""),
                note=row.get("note", ""),
            )
        )
    return queries


def write_eval_template(path: Path, files: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "queries": [
            {
                "query": "descreva aqui como voce lembraria dessa imagem",
                "expected": [name],
                "kind": "manual",
                "category": "memory",
                "image_id": name,
                "note": "Edite esta consulta antes de rodar evaluate_search.py.",
            }
            for name in files
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def evaluate_search(
    engine: MemeSearchEngine,
    queries: list[EvalQuery],
    options: SearchOptions,
    recall_ks: tuple[int, ...] = (1, 5, 10, 20),
) -> dict[str, Any]:
    results: list[SearchEvalResult] = []
    for query in queries:
        start = time.perf_counter()
        found = engine.search_text(query.query, options)
        latency_ms = (time.perf_counter() - start) * 1000
        top_files = [item.arquivo for item in found]
        found_rank = -1
        expected = set(query.expected)
        for rank, arquivo in enumerate(top_files, start=1):
            if arquivo in expected:
                found_rank = rank
                break
        results.append(
            SearchEvalResult(
                query=query.query,
                expected=query.expected,
                kind=query.kind,
                category=query.category,
                image_id=query.image_id,
                found_rank=found_rank,
                top_files=top_files,
                latency_ms=latency_ms,
            )
        )

    metrics = aggregate_metrics(results, recall_ks)
    by_category = {
        category: aggregate_metrics(category_results, recall_ks)
        for category, category_results in group_results(results, "category").items()
    }
    by_kind = {
        kind: aggregate_metrics(kind_results, recall_ks)
        for kind, kind_results in group_results(results, "kind").items()
    }
    by_image = image_level_metrics(results, recall_ks)
    return {
        "metrics": metrics,
        "by_category": by_category,
        "by_kind": by_kind,
        "by_image": by_image,
        "failures": [asdict(row) for row in results if row.found_rank < 0],
        "results": [asdict(row) for row in results],
    }


def aggregate_metrics(
    results: list[SearchEvalResult],
    recall_ks: tuple[int, ...] = (1, 5, 10, 20),
) -> dict[str, Any]:
    total = max(len(results), 1)
    metrics: dict[str, Any] = {"count": len(results)}
    for k in recall_ks:
        metrics[f"recall_at_{k}"] = sum(
            1 for row in results if 1 <= row.found_rank <= k
        ) / total
    metrics["mrr"] = sum(1 / row.found_rank for row in results if row.found_rank > 0) / total
    metrics["latency_ms_avg"] = (
        statistics.mean(row.latency_ms for row in results) if results else 0
    )
    metrics["latency_ms_p95"] = percentile(
        [row.latency_ms for row in results],
        0.95,
    )
    return metrics


def group_results(
    results: list[SearchEvalResult],
    field_name: str,
) -> dict[str, list[SearchEvalResult]]:
    grouped: dict[str, list[SearchEvalResult]] = {}
    for row in results:
        key = str(getattr(row, field_name) or "unknown")
        grouped.setdefault(key, []).append(row)
    return grouped


def image_level_metrics(
    results: list[SearchEvalResult],
    recall_ks: tuple[int, ...] = (1, 5, 10, 20),
) -> dict[str, Any]:
    grouped = group_results(results, "image_id")
    grouped.pop("", None)
    images: dict[str, Any] = {}
    for image_id, rows in grouped.items():
        images[image_id] = aggregate_metrics(rows, recall_ks)
    return images


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(max(int(round((len(values) - 1) * q)), 0), len(values) - 1)
    return values[idx]


def caption_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = max(len(records), 1)
    has_ocr = sum(1 for row in records if str(row.get("texto_extraido", "")).strip())
    has_description = sum(
        1
        for row in records
        if str(row.get("descricao_ia", "")).strip()
        and "N/A" not in str(row.get("descricao_ia", "")).strip()
    )
    has_tags = sum(
        1 for row in records if str(row.get("tags", "")).strip() and row.get("tags") != "N/A"
    )
    return {
        "count": len(records),
        "ocr_coverage": has_ocr / total,
        "description_coverage": has_description / total,
        "tag_coverage": has_tags / total,
    }
