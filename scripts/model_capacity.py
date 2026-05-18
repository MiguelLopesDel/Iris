from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import torch
from _path import ensure_project_root
from PIL import Image
from sentence_transformers import SentenceTransformer

ensure_project_root()

from core.media_inventory import read_manifest  # noqa: E402


def load_images(manifest: Path, limit: int) -> list[Image.Image]:
    media_dir, items = read_manifest(manifest)
    images: list[Image.Image] = []
    for item in items[:limit]:
        path = Path(item.path)
        if not path.exists():
            path = media_dir / item.relative_path
        try:
            images.append(Image.open(path).convert("RGB"))
        except Exception:
            continue
    if not images:
        raise SystemExit("Nenhuma imagem valida encontrada no manifest.")
    return images


def run_batch(
    model: SentenceTransformer,
    images: list[Image.Image],
    batch_size: int,
) -> dict[str, Any]:
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        before = torch.cuda.memory_allocated()
    else:
        before = 0

    started = time.perf_counter()
    try:
        model.encode(images, batch_size=batch_size, show_progress_bar=False)
        elapsed = time.perf_counter() - started
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            peak = torch.cuda.max_memory_allocated()
            after = torch.cuda.memory_allocated()
        else:
            peak = after = 0
        return {
            "batch_size": batch_size,
            "ok": True,
            "elapsed_sec": round(elapsed, 3),
            "images_per_sec": round(len(images) / elapsed, 3) if elapsed else 0,
            "memory_before_gib": round(before / 1024**3, 3),
            "memory_after_gib": round(after / 1024**3, 3),
            "memory_peak_gib": round(peak / 1024**3, 3),
            "error": "",
        }
    except RuntimeError as exc:
        message = str(exc)
        if "out of memory" in message.lower() and torch.cuda.is_available():
            torch.cuda.empty_cache()
        return {
            "batch_size": batch_size,
            "ok": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "images_per_sec": 0,
            "memory_before_gib": round(before / 1024**3, 3),
            "memory_after_gib": 0,
            "memory_peak_gib": 0,
            "error": message[:500],
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mede limite de batch/modelo na GPU antes de indexar tudo."
    )
    parser.add_argument("--manifest", default="data/eval/samples/sample_100.json")
    parser.add_argument("--model", default="sentence-transformers/clip-ViT-L-14")
    parser.add_argument("--batch-sizes", default="1,2,4,8,16")
    parser.add_argument("--limit", type=int, default=32)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--output", default="data/reports/model_capacity.json")
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()

    if args.require_cuda and not torch.cuda.is_available():
        raise SystemExit("CUDA obrigatoria, mas PyTorch nao enxerga CUDA.")

    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA solicitada, mas indisponivel para o PyTorch.")

    images = load_images(Path(args.manifest), args.limit)
    model = SentenceTransformer(args.model, device=device)
    if device == "cuda":
        model.half()

    results = [
        run_batch(model, images, int(batch.strip()))
        for batch in args.batch_sizes.split(",")
        if batch.strip()
    ]
    safe = [row for row in results if row["ok"]]
    recommended = max(safe, key=lambda row: row["images_per_sec"])["batch_size"] if safe else None
    report = {
        "model": args.model,
        "device": device,
        "image_count": len(images),
        "recommended_batch_size": recommended,
        "results": results,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
