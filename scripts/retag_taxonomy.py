"""Re-apply the taxonomy to items that already have AI, under the current rules.

The taxonomy is computed from an item's CLIP image vector and its text, both
already stored, so new thresholds can be applied without touching the images:
this reloads only the CLIP text encoder to embed the label prompts. Dry run by
default (prints how many items each label has now and would have); ``--apply``
writes the columns in one transaction per library.

    python scripts/retag_taxonomy.py --db data/users/1/iris.db
    python scripts/retag_taxonomy.py --all-users data --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
from _path import ensure_project_root

ensure_project_root()

from core.embedding_models import DEFAULT_MODEL, load_encoder  # noqa: E402
from core.taxonomy import (  # noqa: E402
    TAXONOMY_FIELDS,
    build_taxonomy_prompt_rows,
    classify_embedding,
    retag_columns,
)

COLUMNS = (*TAXONOMY_FIELDS, "tags", "visual_json")


def _label_counts(rows) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        for field in TAXONOMY_FIELDS:
            for value in (row[field] or "").split(","):
                if value.strip():
                    counts[value.strip()] += 1
    return counts


def retag_library(db_path: Path, prompt_embeddings: np.ndarray, prompt_rows, apply: bool) -> None:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""SELECT id, arquivo, texto_extraido, embedding, {", ".join(COLUMNS)} FROM memes
                WHERE embedding IS NOT NULL AND length(embedding) > 0 AND model_name = ?""",
            (DEFAULT_MODEL,),
        ).fetchall()
        updates = []
        new_rows = []
        for row in rows:
            vector = np.frombuffer(row["embedding"], dtype=np.float32).reshape(1, -1)
            if vector.shape[1] != prompt_embeddings.shape[1]:
                continue
            try:
                translated = json.loads(row["visual_json"] or "{}").get("ocr_translated", "")
            except (json.JSONDecodeError, AttributeError):
                translated = ""
            text = f"{row['arquivo'] or ''} {row['texto_extraido'] or ''} {translated}"
            matches = classify_embedding(vector, prompt_embeddings, prompt_rows, text_content=text)
            columns = retag_columns({name: row[name] or "" for name in COLUMNS}, matches)
            new_rows.append(columns)
            updates.append((*[columns[name] for name in COLUMNS], row["id"]))
        before, after = _label_counts(rows), _label_counts(new_rows)
        print(f"{db_path}: {len(updates)} items with CLIP vectors")
        for value in sorted(set(before) | set(after), key=lambda v: -before.get(v, 0)):
            print(f"  {value:32} {before.get(value, 0):6} -> {after.get(value, 0):6}")
        if apply and updates:
            with conn:
                conn.executemany(
                    f"UPDATE memes SET {', '.join(f'{name} = ?' for name in COLUMNS)} WHERE id = ?",
                    updates,
                )
            print("  written")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--db", type=Path, help="one library database")
    target.add_argument("--all-users", type=Path, metavar="DATA_DIR",
                        help="every library under DATA_DIR/users/*/")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()

    encoder = load_encoder(DEFAULT_MODEL, device="cpu")
    prompt_rows = build_taxonomy_prompt_rows()
    prompt_embeddings = encoder.encode(
        [row["prompt"] for row in prompt_rows], show_progress_bar=False
    ).astype(np.float32)
    databases = [args.db] if args.db else sorted(
        p for p in (args.all_users / "users").glob("*/*.db") if p.name != "users.db"
    )
    for db in databases:
        retag_library(db, prompt_embeddings, prompt_rows, args.apply)


if __name__ == "__main__":
    main()
