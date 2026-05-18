from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.evaluation import evaluate_search, load_eval_queries  # noqa: E402
from core.search_engine import MemeSearchEngine, SearchOptions  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia busca usando consultas com resposta esperada.")
    parser.add_argument("--db", default="data/eval/indexes/sample_100.db")
    parser.add_argument("--queries", default="data/eval/packs/sample_100/queries.json")
    parser.add_argument("--media-root", default="media")
    parser.add_argument("--output", default="data/eval/reports/search_eval.json")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--recall-ks", default="1,5,10,20")
    parser.add_argument("--threshold", type=float, default=-1.0)
    parser.add_argument("--balance", type=float, default=0.5)
    parser.add_argument("--text-bonus", type=float, default=2.0)
    parser.add_argument("--lexical-weight", type=float, default=0.25)
    parser.add_argument("--no-translate", action="store_true")
    args = parser.parse_args()

    engine = MemeSearchEngine(db_path=args.db, media_root=args.media_root)
    queries = load_eval_queries(Path(args.queries))
    options = SearchOptions(
        top_k=args.top_k,
        threshold=args.threshold,
        balance=args.balance,
        text_bonus=args.text_bonus,
        lexical_weight=args.lexical_weight,
        translate=not args.no_translate,
    )
    recall_ks = tuple(
        sorted({int(part.strip()) for part in args.recall_ks.split(",") if part.strip()})
    )
    report = evaluate_search(engine, queries, options, recall_ks=recall_ks)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2, ensure_ascii=False))
    print(f"Relatorio salvo em: {output}")


if __name__ == "__main__":
    main()
