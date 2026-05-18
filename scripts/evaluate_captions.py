from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.evaluation import caption_coverage  # noqa: E402
from core.search_engine import MemeSearchEngine  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Mede cobertura basica de OCR, tags e descricoes.")
    parser.add_argument("--db", default="data/eval/indexes/sample_100.db")
    parser.add_argument("--output", default="data/eval/reports/caption_eval.json")
    args = parser.parse_args()

    engine = MemeSearchEngine(db_path=args.db, load_model=False)
    metrics = caption_coverage(engine.dados)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
