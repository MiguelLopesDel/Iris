from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

try:
    from scripts._path import ensure_project_root
except ImportError:
    from _path import ensure_project_root

ensure_project_root()

from core.indexer import (  # noqa: E402
    DEFAULT_LIBRARY_NAME,
    DEFAULT_LIBRARY_ROOT,
    IndexerConfig,
    create_faiss_indices,
    process_images,
    resolve_device,
)
from core.media_inventory import iter_media_files  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Importa arquivos/pastas para a biblioteca e indexa incrementalmente."
    )
    parser.add_argument("--db", default="data/iris.db")
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--library", default=DEFAULT_LIBRARY_NAME)
    parser.add_argument("--library-root", default=str(DEFAULT_LIBRARY_ROOT))
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    parser.add_argument("--model", default="sentence-transformers/clip-ViT-L-14")
    parser.add_argument("--caption-model", default="microsoft/Florence-2-large")
    parser.add_argument("--whisper-model", default="tiny")
    return parser.parse_args()


def collect_single_files(sources: list[Path]) -> list[Path]:
    return [path for path in sources if path.is_file()]


def collect_directories(sources: list[Path]) -> list[Path]:
    return [path for path in sources if path.is_dir()]


def stage_files(files: list[Path]) -> Path | None:
    if not files:
        return None
    temp_root = Path(tempfile.mkdtemp(prefix="iris_import_"))
    for idx, file_path in enumerate(files, start=1):
        target = temp_root / f"{idx:05d}_{file_path.name}"
        shutil.copy2(file_path, target)
    return temp_root


def build_config(
    *,
    db_path: Path,
    media_dir: Path,
    args: argparse.Namespace,
) -> IndexerConfig:
    return IndexerConfig(
        media_dir=media_dir,
        db_path=db_path,
        model_name=args.model,
        batch_size=args.batch_size,
        device=resolve_device(args.device),
        recursive=args.recursive,
        limit=None,
        rebuild_faiss_only=False,
        caption_model=args.caption_model,
        whisper_model=args.whisper_model,
        sample_manifest=None,
        library_name=args.library,
        library_root=Path(args.library_root),
        copy_to_library=True,
    )


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute() and db_path.parent == Path("."):
        db_path = Path("data") / db_path

    sources = [Path(source).resolve() for source in args.source]
    dirs = collect_directories(sources)
    files = collect_single_files(sources)
    missing = [str(path) for path in sources if not path.exists()]
    if missing:
        raise SystemExit(f"Fontes nao encontradas: {', '.join(missing)}")

    staged_dir = stage_files(files)
    try:
        for source_dir in dirs:
            file_count = len(iter_media_files(source_dir, recursive=args.recursive))
            print(f"Importando diretorio: {source_dir} ({file_count} arquivo(s))")
            process_images(build_config(db_path=db_path, media_dir=source_dir, args=args))

        if staged_dir:
            print(f"Importando arquivo(s) avulso(s): {len(files)}")
            process_images(build_config(db_path=db_path, media_dir=staged_dir, args=args))

        create_faiss_indices(db_path, args.model)
    finally:
        if staged_dir and staged_dir.exists():
            shutil.rmtree(staged_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
