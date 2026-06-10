from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.evaluation import evaluate_search, load_eval_queries  # noqa: E402
from core.search_engine import IrisEngine, SearchOptions  # noqa: E402


def parse_ks(value: str) -> tuple[int, ...]:
    return tuple(sorted({int(part.strip()) for part in value.split(",") if part.strip()}))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Avalia golden set com multiplas consultas por imagem e categoria."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--media-root", default="media")
    parser.add_argument("--output", default="data/eval/reports/golden_eval.json")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--recall-ks", default="1,5,10,20")
    parser.add_argument("--threshold", type=float, default=-1.0)
    parser.add_argument("--balance", type=float, default=0.5)
    parser.add_argument("--text-bonus", type=float, default=2.0)
    parser.add_argument("--lexical-weight", type=float, default=0.25)
    parser.add_argument("--no-translate", action="store_true")
    parser.add_argument("--min-recall10", type=float, default=0.90)
    parser.add_argument("--min-recall20", type=float, default=0.95)
    args = parser.parse_args()

    recall_ks = parse_ks(args.recall_ks)
    engine = IrisEngine(db_path=args.db, media_root=args.media_root)
    options = SearchOptions(
        top_k=args.top_k,
        threshold=args.threshold,
        balance=args.balance,
        text_bonus=args.text_bonus,
        lexical_weight=args.lexical_weight,
        translate=not args.no_translate,
    )
    report = evaluate_search(
        engine,
        load_eval_queries(Path(args.queries)),
        options,
        recall_ks=recall_ks,
    )
    metrics = report["metrics"]
    report["acceptance"] = {
        "min_recall_at_10": args.min_recall10,
        "min_recall_at_20": args.min_recall20,
        "passed": (
            metrics.get("recall_at_10", 0.0) >= args.min_recall10
            and metrics.get("recall_at_20", 0.0) >= args.min_recall20
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"metrics": metrics, "acceptance": report["acceptance"]}, indent=2))
    print(f"Relatorio salvo em: {output}")


if __name__ == "__main__":
    main()
