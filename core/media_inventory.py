from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image

SUPPORTED_MEDIA_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mkv", ".mov")


@dataclass(frozen=True)
class MediaItem:
    path: str
    relative_path: str
    name: str
    extension: str
    size_bytes: int
    mtime: float
    width: int | None
    height: int | None
    aspect_bucket: str
    content_hash: str | None = None


def iter_media_files(media_dir: Path, recursive: bool = False) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(
        path
        for path in media_dir.glob(pattern)
        if path.is_file() and path.suffix.lower() in SUPPORTED_MEDIA_EXTS
    )


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    if path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}:
        return None, None
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def aspect_bucket(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "unknown"
    ratio = width / height
    if ratio < 0.75:
        return "portrait"
    if ratio > 1.35:
        return "landscape"
    return "square"


def inventory_media(
    media_dir: Path,
    recursive: bool = False,
    compute_hash: bool = False,
) -> list[MediaItem]:
    media_dir = media_dir.resolve()
    items: list[MediaItem] = []
    for path in iter_media_files(media_dir, recursive=recursive):
        stat = path.stat()
        width, height = image_dimensions(path)
        items.append(
            MediaItem(
                path=str(path.resolve()),
                relative_path=path.resolve().relative_to(media_dir).as_posix(),
                name=path.name,
                extension=path.suffix.lower().lstrip("."),
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                width=width,
                height=height,
                aspect_bucket=aspect_bucket(width, height),
                content_hash=file_sha256(path) if compute_hash else None,
            )
        )
    return items


def sample_media(
    items: list[MediaItem],
    sample_size: int,
    seed: int = 42,
) -> list[MediaItem]:
    if sample_size >= len(items):
        return sorted(items, key=lambda item: item.relative_path)

    rng = random.Random(seed)
    buckets: dict[tuple[str, str], list[MediaItem]] = {}
    for item in items:
        buckets.setdefault((item.extension, item.aspect_bucket), []).append(item)

    selected: list[MediaItem] = []
    bucket_keys = sorted(buckets)
    per_bucket = max(1, sample_size // max(len(bucket_keys), 1))
    for key in bucket_keys:
        candidates = buckets[key]
        take = min(per_bucket, len(candidates), sample_size - len(selected))
        selected.extend(rng.sample(candidates, take))
        if len(selected) >= sample_size:
            break

    selected_paths = {item.relative_path for item in selected}
    remaining = [item for item in items if item.relative_path not in selected_paths]
    if len(selected) < sample_size and remaining:
        selected.extend(rng.sample(remaining, min(sample_size - len(selected), len(remaining))))

    return sorted(selected, key=lambda item: item.relative_path)


def write_manifest(
    path: Path,
    media_dir: Path,
    items: list[MediaItem],
    *,
    seed: int,
    sample_size: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "media_dir": str(media_dir.resolve()),
        "seed": seed,
        "sample_size": sample_size,
        "count": len(items),
        "items": [asdict(item) for item in items],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_manifest(path: Path) -> tuple[Path, list[MediaItem]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    media_dir = Path(payload["media_dir"])
    items = [MediaItem(**item) for item in payload.get("items", [])]
    return media_dir, items
