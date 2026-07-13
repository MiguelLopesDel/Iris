#!/usr/bin/env python3
"""Re-point stale absolute library roots to their current on-disk location.

When the project directory is moved/renamed, ``media_libraries.root_path`` keeps the
old absolute path baked in at import time, so ``resolve_media_path`` can no longer find
files that resolve via ``library_id`` + ``storage_path`` — every such media shows up as
"arquivo indisponível" even though the files are right there under ``data/library/<name>``.

This heals it: for each registered library whose ``root_path`` no longer exists, it looks
for a matching ``data/library/<name>`` directory relative to the project root (or an
explicit ``--library-base``) and rewrites ``root_path`` to that absolute path. Idempotent
and non-destructive — it only ever UPDATEs the path column, never touches media.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def relink(db_path: Path, library_base: Path, *, apply: bool) -> int:
    if not db_path.exists():
        print(f"banco não encontrado: {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db_path)
    rows = list(conn.execute("SELECT id, name, root_path FROM media_libraries"))
    if not rows:
        print("nenhuma biblioteca registrada — nada a fazer.")
        return 0

    changes: list[tuple[int, str, str]] = []
    for lib_id, name, root in rows:
        root_ok = bool(root) and os.path.exists(root)
        if root_ok:
            print(f"lib {lib_id} '{name}': OK (root existe) — {root}")
            continue
        # Candidate current location: <library_base>/<name>, else <library_base> itself.
        candidates = [library_base / name, library_base]
        target = next((c for c in candidates if c.is_dir()), None)
        if target is None:
            print(f"lib {lib_id} '{name}': root ausente e nenhuma pasta encontrada em {candidates}")
            continue
        new_root = str(target.resolve())
        if new_root == root:
            continue
        changes.append((lib_id, root or "", new_root))
        print(f"lib {lib_id} '{name}': {root!r} -> {new_root}")

    if not changes:
        print("\nnada para reapontar.")
        return 0
    if not apply:
        print(f"\n[dry-run] {len(changes)} biblioteca(s) seriam reapontadas. Rode com --apply para gravar.")
        return 0
    for lib_id, _old, new_root in changes:
        conn.execute("UPDATE media_libraries SET root_path = ? WHERE id = ?", (new_root, lib_id))
    conn.commit()
    print(f"\n{len(changes)} biblioteca(s) reapontada(s). Recarregue o backend (ou reinicie o servidor).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/meme_compass_full_v1.db", help="caminho do banco SQLite")
    ap.add_argument(
        "--library-base",
        default="data/library",
        help="pasta onde vivem as bibliotecas (default: data/library, relativa ao CWD)",
    )
    ap.add_argument("--apply", action="store_true", help="grava as mudanças (sem isso é dry-run)")
    args = ap.parse_args()
    return relink(Path(args.db), Path(args.library_base), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
