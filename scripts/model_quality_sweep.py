from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()


def slugify(value: str) -> str:
    return value.replace("/", "__").replace(":", "_").replace(" ", "_")


def run(command: list[str]) -> tuple[int, float]:
    started = time.perf_counter()
    result = subprocess.run(command, check=False)
    return result.returncode, round(time.perf_counter() - started, 3)


def load_metrics(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("metrics", {})
    except Exception:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Indexa uma amostra com varios modelos e avalia recall no golden set."
    )
    parser.add_argument("--manifest", default="data/eval/samples/sample_100.json")
    parser.add_argument("--queries", default="")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["sentence-transformers/clip-ViT-L-14"],
    )
    parser.add_argument("--caption-model", default="none")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--media-root", default="media")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--output", default="data/reports/model_quality_sweep.json")
    args = parser.parse_args()

    runs = []
    for model in args.models:
        slug = slugify(model)
        db = Path("data/indexes") / f"sweep_{slug}.db"
        eval_report = Path("data/reports") / f"sweep_{slug}_eval.json"
        db.parent.mkdir(parents=True, exist_ok=True)
        index_cmd = [
            sys.executable,
            "scripts/build_sample_index.py",
            "--manifest",
            args.manifest,
            "--db",
            str(db),
            "--model",
            model,
            "--caption-model",
            args.caption_model,
            "--device",
            args.device,
            "--batch-size",
            str(args.batch_size),
        ]
        index_code, index_sec = run(index_cmd)

        eval_code = None
        eval_sec = 0.0
        metrics = {}
        if args.queries and index_code == 0:
            eval_cmd = [
                sys.executable,
                "scripts/evaluate_golden_set.py",
                "--db",
                str(db),
                "--queries",
                args.queries,
                "--media-root",
                args.media_root,
                "--output",
                str(eval_report),
                "--top-k",
                str(args.top_k),
                "--no-translate",
            ]
            eval_code, eval_sec = run(eval_cmd)
            metrics = load_metrics(eval_report)

        runs.append(
            {
                "model": model,
                "db": str(db),
                "index_returncode": index_code,
                "index_elapsed_sec": index_sec,
                "eval_returncode": eval_code,
                "eval_elapsed_sec": eval_sec,
                "eval_report": str(eval_report) if args.queries else "",
                "metrics": metrics,
            }
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")
    print(json.dumps({"runs": runs}, indent=2))


if __name__ == "__main__":
    main()
