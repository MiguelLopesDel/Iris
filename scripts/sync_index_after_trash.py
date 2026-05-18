from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

try:
    from scripts._path import ensure_project_root
except ImportError:
    from _path import ensure_project_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Limpa registros faltantes no banco e recria os indices FAISS."
    )
    parser.add_argument(
        "--db",
        default="meme_compass_full_v1.db",
        help="Banco SQLite. Nome relativo simples sera resolvido dentro de data/.",
    )
    parser.add_argument(
        "--media-root",
        default="media",
        help="Diretorio base das midias para validar existencia dos arquivos.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas mostra quantos registros seriam removidos.",
    )
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="Nao recria os indices FAISS apos a limpeza.",
    )
    return parser.parse_args()


def resolve_db_path(raw_db: str) -> Path:
    db_path = Path(raw_db)
    if not db_path.is_absolute() and db_path.parent == Path("."):
        db_path = Path("data") / db_path
    return db_path


def resolve_media_path(
    libraries: dict[int, Path],
    media_root: Path,
    arquivo: str,
    caminho: str | None,
    relative_path: str | None,
    storage_path: str | None,
    library_id: int | None,
) -> Path:
    candidates: list[Path] = []
    if storage_path and library_id is not None and int(library_id) in libraries:
        candidates.append(libraries[int(library_id)] / storage_path)
    if relative_path:
        candidates.append(media_root / relative_path)
    if caminho:
        raw_path = Path(caminho)
        candidates.append(raw_path if raw_path.is_absolute() else Path.cwd() / raw_path)
        candidates.append(media_root / raw_path.name)
    if arquivo:
        candidates.append(media_root / arquivo)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else media_root / arquivo


def collect_missing_ids(db_path: Path, media_root: Path) -> list[int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memes)")}
        select_cols = ["id", "arquivo", "caminho"]
        if "relative_path" in columns:
            select_cols.append("relative_path")
        if "storage_path" in columns:
            select_cols.append("storage_path")
        if "library_id" in columns:
            select_cols.append("library_id")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        libraries: dict[int, Path] = {}
        if "media_libraries" in tables:
            for row in conn.execute("SELECT id, root_path FROM media_libraries").fetchall():
                try:
                    libraries[int(row[0])] = Path(str(row[1])).resolve()
                except Exception:
                    continue
        rows = conn.execute(f"SELECT {', '.join(select_cols)} FROM memes ORDER BY id").fetchall()
    finally:
        conn.close()

    missing_ids: list[int] = []
    for row in rows:
        path = resolve_media_path(
            libraries=libraries,
            media_root=media_root,
            arquivo=row["arquivo"] or "",
            caminho=row["caminho"] if "caminho" in row.keys() else None,
            relative_path=row["relative_path"] if "relative_path" in row.keys() else None,
            storage_path=row["storage_path"] if "storage_path" in row.keys() else None,
            library_id=row["library_id"] if "library_id" in row.keys() else None,
        )
        if not path.exists():
            missing_ids.append(int(row["id"]))
    return missing_ids


def delete_ids(db_path: Path, ids: list[int]) -> None:
    if not ids:
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany("DELETE FROM memes WHERE id = ?", [(id_value,) for id_value in ids])
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    ensure_project_root()

    args = parse_args()
    db_path = resolve_db_path(args.db)
    media_root = Path(args.media_root).resolve()

    if not db_path.exists():
        raise SystemExit(f"Banco nao encontrado: {db_path}")

    missing_ids = collect_missing_ids(db_path, media_root)
    total = len(missing_ids)
    print(f"Registros faltantes detectados: {total}")
    if args.dry_run:
        return

    if total:
        delete_ids(db_path, missing_ids)
        print(f"Registros removidos: {total}")
    else:
        print("Nenhum registro removido.")

    if not args.no_rebuild:
        from core.indexer import create_faiss_indices

        create_faiss_indices(db_path)


if __name__ == "__main__":
    main()
