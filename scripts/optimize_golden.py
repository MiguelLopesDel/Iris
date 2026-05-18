from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.evaluation import aggregate_metrics, load_eval_queries  # noqa: E402
from core.search_engine import MemeSearchEngine, SearchOptions  # noqa: E402


def parse_values(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def score_metrics(metrics: dict[str, float]) -> tuple[float, float, float, float]:
    return (
        metrics.get("recall_at_20", 0.0),
        metrics.get("recall_at_10", 0.0),
        metrics.get("mrr", 0.0),
        metrics.get("recall_at_1", 0.0),
    )


def evaluate_cached(engine: MemeSearchEngine, encoded_queries: list[dict], options: SearchOptions) -> dict:
    results = []
    for row in encoded_queries:
        found = engine.search_by_embedding(
            row["embedding"],
            options,
            text_query=row["query"],
            translated_query=row["translated"],
            negative_terms=row["negative"],
        )
        top_files = [item.arquivo for item in found]
        expected = set(row["expected"])
        found_rank = -1
        for rank, arquivo in enumerate(top_files, start=1):
            if arquivo in expected:
                found_rank = rank
                break
        results.append(
            {
                "query": row["query"],
                "expected": row["expected"],
                "kind": row["kind"],
                "category": row["category"],
                "image_id": row["image_id"],
                "found_rank": found_rank,
                "top_files": top_files,
                "latency_ms": 0.0,
            }
        )

    from core.evaluation import SearchEvalResult

    typed_results = [SearchEvalResult(**row) for row in results]
    return {"metrics": aggregate_metrics(typed_results, (1, 5, 10, 20)), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Otimiza pesos de busca usando um golden set com respostas esperadas."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--media-root", default="media")
    parser.add_argument("--output", default="data/reports/golden_weights.json")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--balances", default="0.0,0.25,0.5,0.65,0.8,1.0")
    parser.add_argument("--text-bonuses", default="0.5,1.0,1.5,2.0,2.5,3.0")
    parser.add_argument("--lexical-weights", default="0.0,0.15,0.25,0.4,0.6")
    parser.add_argument("--candidate-pool", type=int, default=3000)
    parser.add_argument("--no-translate", action="store_true")
    args = parser.parse_args()

    engine = MemeSearchEngine(db_path=args.db, media_root=args.media_root)
    queries = load_eval_queries(Path(args.queries))
    encoded_queries = []
    for query in queries:
        embedding, translated = engine.encode_text(query.query, translate=not args.no_translate)
        encoded_queries.append(
            {
                "query": query.query,
                "expected": query.expected,
                "kind": query.kind,
                "category": query.category,
                "image_id": query.image_id,
                "negative": [],
                "embedding": embedding,
                "translated": translated,
            }
        )
    candidates = []
    for balance, text_bonus, lexical_weight in product(
        parse_values(args.balances),
        parse_values(args.text_bonuses),
        parse_values(args.lexical_weights),
    ):
        options = SearchOptions(
            top_k=args.top_k,
            threshold=-1.0,
            balance=balance,
            text_bonus=text_bonus,
            lexical_weight=lexical_weight,
            candidate_pool=args.candidate_pool,
            translate=not args.no_translate,
        )
        report = evaluate_cached(engine, encoded_queries, options)
        metrics = report["metrics"]
        candidates.append(
            {
                "weights": {
                    "balance": balance,
                    "text_bonus": text_bonus,
                    "lexical_weight": lexical_weight,
                },
                "metrics": metrics,
                "score": score_metrics(metrics),
            }
        )

    candidates.sort(key=lambda row: row["score"], reverse=True)
    best = candidates[0] if candidates else {}
    payload = {
        "best": best,
        "top_candidates": candidates[:20],
        "count": len(candidates),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(best, indent=2, ensure_ascii=False))
    print(f"Pesos salvos em: {output}")


if __name__ == "__main__":
    main()
