#!/usr/bin/env python3
"""Measure what the duplicate detector actually catches, transformation by transformation.

A single recall number hides the failures that matter. Resizes and recompressions
dominate any real library, so a detector that handles them and nothing else still
reports a spectacular average while missing every crop. This reports each
transformation separately, and reports the false-positive side too, because the
two errors are not equally bad: missing a duplicate leaves a second copy on disk,
while a wrong match puts a unique photo in front of a delete button.

Runs the production hash path (``core.indexer._compute_phash`` at the production
threshold), so the numbers describe the shipped detector, not a reimplementation.

    python scripts/evaluate_dedup.py --dir media --sample-size 200
"""

from __future__ import annotations

import argparse
import io
import random
import sys
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.indexer import _DEDUP_PHASH_THRESHOLD, _compute_phash  # noqa: E402

Transform = Callable[[Image.Image], Image.Image]


def _reencode(image: Image.Image, image_format: str, **options) -> Image.Image:
    """Round-trip through a real encoder, so compression artefacts are real."""
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **options)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def _scaled(factor: float) -> Transform:
    def apply(image: Image.Image) -> Image.Image:
        width = max(int(image.width * factor), 1)
        height = max(int(image.height * factor), 1)
        return image.resize((width, height), Image.LANCZOS)

    return apply


def _cropped(fraction: float) -> Transform:
    def apply(image: Image.Image) -> Image.Image:
        dx = int(image.width * fraction)
        dy = int(image.height * fraction)
        return image.crop((dx, dy, image.width - dx, image.height - dy))

    return apply


def _bordered(fraction: float) -> Transform:
    def apply(image: Image.Image) -> Image.Image:
        dx = int(image.width * fraction)
        dy = int(image.height * fraction)
        canvas = Image.new("RGB", (image.width + 2 * dx, image.height + 2 * dy), (0, 0, 0))
        canvas.paste(image, (dx, dy))
        return canvas

    return apply


def _watermarked(image: Image.Image) -> Image.Image:
    marked = image.copy()
    draw = ImageDraw.Draw(marked)
    step = max(image.width // 4, 1)
    for x in range(0, image.width, step):
        draw.text((x, image.height // 2), "SAMPLE", fill=(255, 255, 255))
    return marked


def _rotated_pixels(image: Image.Image) -> Image.Image:
    """Rotation baked into the pixels, as an editor would write it."""
    return image.rotate(90, expand=True)


def _exif_orientation(image: Image.Image) -> Image.Image:
    """The same photo as a camera writes it: upright pixels, orientation tag.

    Separate from rotated pixels on purpose. If the importer ignored the tag,
    this file and its already-upright twin would hash differently for no reason
    other than a metadata byte, which is a fixable miss; rotated pixels are a
    genuinely different image to a frequency hash.
    """
    exif = image.getexif()
    exif[274] = 6  # Orientation: rotate 90 CW on display
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95, exif=exif)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


TRANSFORMS: dict[str, Transform] = {
    "resize 50%": _scaled(0.5),
    "resize 25%": _scaled(0.25),
    "resize 200%": _scaled(2.0),
    "jpeg q90": lambda image: _reencode(image, "JPEG", quality=90),
    "jpeg q70": lambda image: _reencode(image, "JPEG", quality=70),
    "jpeg q40": lambda image: _reencode(image, "JPEG", quality=40),
    "png round-trip": lambda image: _reencode(image, "PNG"),
    "webp q80": lambda image: _reencode(image, "WEBP", quality=80),
    "brightness +15%": lambda image: ImageEnhance.Brightness(image).enhance(1.15),
    "brightness -15%": lambda image: ImageEnhance.Brightness(image).enhance(0.85),
    "contrast +20%": lambda image: ImageEnhance.Contrast(image).enhance(1.2),
    "grayscale": lambda image: image.convert("L").convert("RGB"),
    "border 5%": _bordered(0.05),
    "crop 5%": _cropped(0.05),
    "crop 10%": _cropped(0.10),
    "crop 20%": _cropped(0.20),
    "watermark": _watermarked,
    "rotate 90 (pixels)": _rotated_pixels,
    "exif orientation tag": _exif_orientation,
}


def _distance(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def _sample(directory: Path, size: int, seed: int) -> list[Path]:
    suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    files = [path for path in directory.rglob("*") if path.suffix.lower() in suffixes]
    random.Random(seed).shuffle(files)
    return files[:size]


def _measure_recall(images: list[Image.Image], threshold: int) -> dict[str, list[int]]:
    results: dict[str, list[int]] = {name: [] for name in TRANSFORMS}
    for image in images:
        original = _compute_phash(image)
        for name, transform in TRANSFORMS.items():
            try:
                distance = _distance(original, _compute_phash(transform(image)))
            except Exception:
                distance = None
            if distance is not None:
                results[name].append(distance)
    return results


def _measure_false_positives(images: list[Image.Image], threshold: int, seed: int) -> tuple[int, int]:
    """Unrelated images that land within the threshold anyway."""
    hashes = [h for h in (_compute_phash(image) for image in images) if h]
    collisions = 0
    compared = 0
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            compared += 1
            if _distance(hashes[i], hashes[j]) <= threshold:
                collisions += 1
    return collisions, compared


def _uniform_images() -> list[tuple[str, Image.Image]]:
    """Solid and near-solid images, the degenerate case for a frequency hash."""
    return [
        ("black", Image.new("RGB", (512, 512), (0, 0, 0))),
        ("near-black", Image.new("RGB", (512, 512), (2, 2, 2))),
        ("white", Image.new("RGB", (512, 512), (255, 255, 255))),
        ("near-white", Image.new("RGB", (512, 512), (253, 253, 253))),
        ("mid-grey", Image.new("RGB", (512, 512), (128, 128, 128))),
        ("solid red", Image.new("RGB", (512, 512), (200, 30, 30))),
        ("solid blue", Image.new("RGB", (512, 512), (30, 30, 200))),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("media"))
    parser.add_argument("--sample-size", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold", type=int, default=_DEDUP_PHASH_THRESHOLD)
    args = parser.parse_args()

    paths = _sample(args.dir, args.sample_size, args.seed)
    if not paths:
        print(f"Nenhuma imagem encontrada em {args.dir}", file=sys.stderr)
        return 1

    images: list[Image.Image] = []
    for path in paths:
        try:
            images.append(Image.open(path).convert("RGB"))
        except Exception:
            continue
    print(f"{len(images)} imagens de {args.dir}, limiar {args.threshold}\n")

    results = _measure_recall(images, args.threshold)
    print(f"{'transformação':<18} {'recall':>8} {'mediana':>9} {'p95':>6} {'máx':>5}")
    print("-" * 50)
    for name, distances in results.items():
        if not distances:
            continue
        ordered = sorted(distances)
        caught = sum(1 for d in ordered if d <= args.threshold)
        median = ordered[len(ordered) // 2]
        p95 = ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]
        print(
            f"{name:<18} {caught / len(ordered):>7.1%} {median:>9} {p95:>6} {ordered[-1]:>5}"
        )

    collisions, compared = _measure_false_positives(images, args.threshold, args.seed)
    print(
        f"\nfalsos positivos: {collisions} de {compared} pares não relacionados "
        f"({collisions / max(compared, 1):.2%})"
    )

    print("\nimagens uniformes (distância entre si):")
    uniform = _uniform_images()
    hashes = [(name, _compute_phash(image)) for name, image in uniform]
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            distance = _distance(hashes[i][1], hashes[j][1])
            marker = "  <-- agrupadas" if distance is not None and distance <= args.threshold else ""
            print(f"  {hashes[i][0]:<12} x {hashes[j][0]:<12} {distance}{marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
