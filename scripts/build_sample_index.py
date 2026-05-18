from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.media_inventory import read_manifest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexa somente uma amostra manifestada.")
    parser.add_argument("--manifest", default="data/eval/samples/sample_100.json")
    parser.add_argument("--db", default="data/eval/indexes/sample_100.db")
    parser.add_argument("--model", default="sentence-transformers/clip-ViT-L-14")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    parser.add_argument("--caption-model", default="microsoft/Florence-2-large")
    parser.add_argument("--whisper-model", default="none")
    args = parser.parse_args()

    manifest = Path(args.manifest)
    media_dir, _ = read_manifest(manifest)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "core.indexer",
        "--dir",
        str(media_dir),
        "--db",
        str(db_path),
        "--sample-manifest",
        str(manifest),
        "--model",
        args.model,
        "--batch-size",
        str(args.batch_size),
        "--device",
        args.device,
        "--caption-model",
        args.caption_model,
        "--whisper-model",
        args.whisper_model,
    ]
    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
