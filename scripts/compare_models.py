from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compara tempo de indexacao de modelos locais na mesma amostra."
    )
    parser.add_argument("--manifest", default="data/eval/samples/sample_100.json")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["sentence-transformers/clip-ViT-L-14"],
        help="Modelos sentence-transformers a comparar.",
    )
    parser.add_argument("--caption-model", default="none")
    parser.add_argument("--output", default="data/eval/reports/model_compare.json")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    reports = []
    for model in args.models:
        slug = model.replace("/", "__").replace(":", "_")
        db = Path("data/eval/indexes") / f"compare_{slug}.db"
        started = time.perf_counter()
        command = [
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
        ]
        result = subprocess.run(command, check=False)
        reports.append(
            {
                "model": model,
                "db": str(db),
                "returncode": result.returncode,
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"runs": reports}, indent=2), encoding="utf-8")
    print(json.dumps({"runs": reports}, indent=2))


if __name__ == "__main__":
    main()
