from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO


def _files_size(paths: list[Path]) -> int:
    return sum(path.stat().st_size for path in paths if path.is_file())


def backup_inventory(data_dir: Path) -> dict[str, int]:
    databases = sorted(data_dir.glob("*.db"))
    indexes = sorted(data_dir.glob("*.faiss"))
    manifests = sorted(data_dir.glob("*_manifest.json"))
    library_root = data_dir / "library"
    library = [path for path in library_root.rglob("*") if path.is_file()] if library_root.exists() else []
    return {
        "databases": len(databases),
        "indexes": len(indexes),
        "manifests": len(manifests),
        "library_files": len(library),
        "database_bytes": _files_size(databases),
        "index_bytes": _files_size(indexes + manifests),
        "library_bytes": _files_size(library),
    }


def _write_backup(archive_target: BinaryIO | Path, data_dir: Path, include_library: bool) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for pattern in ("*.db", "*.faiss", "*_manifest.json"):
            for path in sorted(data_dir.glob(pattern)):
                archive.write(path, f"databases/{path.name}")

        weights = data_dir / "best_weights.json"
        if weights.exists():
            archive.write(weights, "config/best_weights.json")

        library_included = False
        library_root = data_dir / "library"
        if include_library and library_root.exists():
            for path in sorted(library_root.rglob("*")):
                if path.is_file():
                    archive.write(path, f"library/{path.relative_to(library_root).as_posix()}")
                    library_included = True

        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": "1.0",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "software": "iris",
                    "library_included": library_included,
                },
                indent=2,
            ),
        )


def create_backup_zip(data_dir: Path, include_library: bool = True) -> bytes:
    output = io.BytesIO()
    _write_backup(output, data_dir, include_library)
    return output.getvalue()


def create_backup_file(data_dir: Path, output_path: Path, include_library: bool = True) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_backup(output_path, data_dir, include_library)


def _zip_input(source: bytes | Path) -> io.BytesIO | Path:
    return io.BytesIO(source) if isinstance(source, bytes) else source


def inspect_backup_zip(source: bytes | Path) -> dict[str, object]:
    with zipfile.ZipFile(_zip_input(source)) as archive:
        names = archive.namelist()
        manifest: dict[str, object] = {}
        if "manifest.json" in names:
            manifest = json.loads(archive.read("manifest.json"))
        return {
            "manifest": manifest,
            "databases": sum(name.startswith("databases/") and not name.endswith("/") for name in names),
            "config": sum(name.startswith("config/") and not name.endswith("/") for name in names),
            "library": sum(name.startswith("library/") and not name.endswith("/") for name in names),
        }


def _safe_relative_member(name: str, prefix: str) -> Path | None:
    if not name.startswith(prefix):
        return None
    relative = PurePosixPath(name[len(prefix) :])
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        return None
    return Path(*relative.parts)


def restore_backup_zip(source: bytes | Path, data_dir: Path) -> dict[str, int]:
    data_dir.mkdir(parents=True, exist_ok=True)
    counts = {"databases": 0, "config": 0, "library": 0}
    with zipfile.ZipFile(_zip_input(source)) as archive:
        manifest: dict[str, object] = {}
        if "manifest.json" in archive.namelist():
            manifest = json.loads(archive.read("manifest.json"))

        for member in archive.infolist():
            if member.is_dir() or member.filename == "manifest.json":
                continue

            relative = _safe_relative_member(member.filename, "databases/")
            category = "databases"
            target_root = data_dir
            if relative is None:
                relative = _safe_relative_member(member.filename, "config/")
                category = "config"
            if relative is None and manifest.get("library_included"):
                relative = _safe_relative_member(member.filename, "library/")
                category = "library"
                target_root = data_dir / "library"
            if relative is None:
                continue

            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".restore-tmp")
            with archive.open(member) as source, temporary.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            temporary.replace(target)
            counts[category] += 1
    return counts
