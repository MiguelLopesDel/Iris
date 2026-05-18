from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

try:
    from scripts._path import ensure_project_root
except ImportError:
    from _path import ensure_project_root

ensure_project_root()

from core.indexer import (  # noqa: E402
    DEFAULT_LIBRARY_NAME,
    DEFAULT_LIBRARY_ROOT,
    create_faiss_indices,
    ensure_unique_destination,
    file_sha256,
    get_or_create_library,
    init_db,
    now_iso,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migra arquivos do banco legado para biblioteca gerenciada."
    )
    parser.add_argument("--db", default="data/meme_compass_full_v1.db")
    parser.add_argument("--media-root", default="media")
    parser.add_argument("--library", default=DEFAULT_LIBRARY_NAME)
    parser.add_argument("--library-root", default=str(DEFAULT_LIBRARY_ROOT))
    parser.add_argument("--no-copy", action="store_true")
    parser.add_argument("--rebuild-faiss", action="store_true")
    return parser.parse_args()


def resolve_source(
    *,
    media_root: Path,
    caminho: str | None,
    relative_path: str | None,
    arquivo: str | None,
) -> Path | None:
    candidates: list[Path] = []
    if relative_path:
        candidates.append(media_root / relative_path)
    if caminho:
        raw = Path(caminho)
        candidates.append(raw if raw.is_absolute() else Path.cwd() / raw)
        candidates.append(media_root / raw.name)
    if arquivo:
        candidates.append(media_root / arquivo)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute() and db_path.parent == Path("."):
        db_path = Path("data") / db_path
    media_root = Path(args.media_root).resolve()
    library_root = (Path(args.library_root) / args.library).resolve()

    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
    library_id = get_or_create_library(conn, args.library, library_root)
    rows = conn.execute(
        """
        SELECT id, arquivo, caminho, relative_path, storage_path, source_path, library_id, content_hash
        FROM memes
        ORDER BY id
        """
    ).fetchall()

    migrated = 0
    skipped_missing = 0
    skipped_already = 0

    try:
        for row in rows:
            row_library_id = row["library_id"]
            row_storage = row["storage_path"] or row["relative_path"]
            if row_library_id is not None and row_storage:
                target = library_root / row_storage
                if target.exists():
                    skipped_already += 1
                    continue

            source = resolve_source(
                media_root=media_root,
                caminho=row["caminho"],
                relative_path=row["relative_path"],
                arquivo=row["arquivo"],
            )
            if not source:
                skipped_missing += 1
                continue

            content_hash = row["content_hash"] or file_sha256(source)
            relative_hint = row["storage_path"] or row["relative_path"] or source.name
            destination = ensure_unique_destination(
                library_root=library_root,
                relative_path=relative_hint,
                content_hash=content_hash,
            )
            if not args.no_copy and not destination.exists():
                shutil.copy2(source, destination)
            storage_path = destination.relative_to(library_root).as_posix()

            conn.execute(
                """
                UPDATE memes
                SET library_id = ?,
                    storage_path = ?,
                    source_path = COALESCE(source_path, ?),
                    caminho = ?,
                    content_hash = COALESCE(content_hash, ?),
                    imported_at = COALESCE(imported_at, ?),
                    schema_version = ?
                WHERE id = ?
                """,
                (
                    library_id,
                    storage_path,
                    str(source),
                    str(destination.resolve()),
                    content_hash,
                    now_iso(),
                    4,
                    int(row["id"]),
                ),
            )
            migrated += 1
        conn.commit()
    finally:
        conn.close()

    print(
        f"Migrados: {migrated} | Ja na biblioteca: {skipped_already} | Ausentes: {skipped_missing}"
    )
    if args.rebuild_faiss:
        create_faiss_indices(db_path)


if __name__ == "__main__":
    main()
