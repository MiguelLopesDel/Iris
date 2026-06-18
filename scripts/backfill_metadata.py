"""Backfill capture metadata for media indexed before the metadata feature existed.

Existing rows have ``metadata_json = ''`` because they were imported before Iris read
EXIF/ffprobe tags. This re-reads each file on disk and fills the column, so the
post-import collection suggestions (date / app / location / device) also work for the
library you already have.

Dry-run by default (reports what it would do); pass --apply to write.

    python scripts/backfill_metadata.py --db data/iris_v1.db
    python scripts/backfill_metadata.py --db data/iris_v1.db --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections.abc import Callable

from _path import ensure_project_root

ensure_project_root()

from core.media_metadata import extract_metadata  # noqa: E402


def _has_metadata(meta: dict) -> bool:
    return bool(
        meta.get("captured_at")
        or meta.get("source_app")
        or meta.get("device")
        or meta.get("gps")
        or meta.get("location_label")
    )


def run_backfill(
    conn: sqlite3.Connection,
    resolve_path: Callable[[int], str | None],
    *,
    apply: bool,
    only_empty: bool = True,
    limit: int = 0,
) -> dict[str, int]:
    """Fill ``metadata_json`` for memes whose file resolves on disk.

    ``resolve_path`` maps a meme id to its on-disk path (or None). Returns counters.
    Only rows where extraction yields at least one field are touched, so files with no
    usable metadata stay empty and can be retried by a later run.
    """
    # The column only appears after the app migrates the schema. Add it on --apply;
    # in dry-run, treat its absence as "every row is empty" without touching the DB.
    has_col = "metadata_json" in {r[1] for r in conn.execute("PRAGMA table_info(memes)")}
    if not has_col and apply:
        conn.execute("ALTER TABLE memes ADD COLUMN metadata_json TEXT DEFAULT ''")
        has_col = True

    where = "WHERE metadata_json IS NULL OR metadata_json = ''" if (has_col and only_empty) else ""
    rows = conn.execute(f"SELECT id FROM memes {where} ORDER BY id").fetchall()

    stats = {"scanned": 0, "found": 0, "updated": 0, "missing": 0, "no_metadata": 0}
    updates: list[tuple[str, int]] = []
    for (meme_id,) in rows:
        if limit and stats["scanned"] >= limit:
            break
        stats["scanned"] += 1
        path = resolve_path(meme_id)
        if not path or not os.path.exists(path):
            stats["missing"] += 1
            continue
        meta = extract_metadata(path)
        if not _has_metadata(meta):
            stats["no_metadata"] += 1
            continue
        stats["found"] += 1
        updates.append((json.dumps(meta, ensure_ascii=False), meme_id))

    if apply and updates:
        conn.executemany("UPDATE memes SET metadata_json = ? WHERE id = ?", updates)
        conn.commit()
        stats["updated"] = len(updates)
    return stats


def _default_db() -> str:
    iris, legacy = "data/iris_v1.db", "data/meme_compass_full_v1.db"
    if not os.path.exists(iris) and os.path.exists(legacy):
        return legacy
    return iris


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill metadata_json from EXIF/ffprobe.")
    parser.add_argument("--db", default=_default_db())
    parser.add_argument("--media-root", default="media")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--all", action="store_true", help="re-scan every row, not just empty ones")
    parser.add_argument("--limit", type=int, default=0, help="cap rows scanned (0 = no cap)")
    args = parser.parse_args()

    from core.search_engine import IrisEngine

    engine = IrisEngine(db_path=args.db, media_root=args.media_root, load_model=False)
    path_map = {r.db_id: r.resolved_path for r in engine.records if r.db_id}
    conn = engine.db.get_connection()

    stats = run_backfill(
        conn,
        lambda meme_id: path_map.get(meme_id),
        apply=args.apply,
        only_empty=not args.all,
        limit=args.limit,
    )

    mode = "APLICADO" if args.apply else "SIMULAÇÃO (use --apply para gravar)"
    print(f"[{mode}] {args.db}")
    print(f"  escaneados:    {stats['scanned']}")
    print(f"  com metadados: {stats['found']}")
    print(f"  gravados:      {stats['updated']}")
    print(f"  arquivo ausente: {stats['missing']}")
    print(f"  sem metadados:   {stats['no_metadata']}")


if __name__ == "__main__":
    main()
