from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
from _path import ensure_project_root
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

ensure_project_root()

from core.search_engine import DEFAULT_MODEL  # noqa: E402
from core.taxonomy import (  # noqa: E402
    build_taxonomy_prompt_rows,
    classify_embedding,
    merge_taxonomy_into_profile,
    values_for_field,
)


def ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memes)")}
    additions = {
        "visual_json": "TEXT",
        "objects": "TEXT",
        "style": "TEXT",
        "source_work": "TEXT",
        "humor": "TEXT",
        "context": "TEXT",
        "tags": "TEXT",
    }
    for column, column_type in additions.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE memes ADD COLUMN {column} {column_type}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enriquece metadados do banco usando taxonomia zero-shot via CLIP."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-output", default="data/reports/metadata_enrichment_sample.json")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Banco nao encontrado: {db_path}")

    device = args.device
    if device == "auto":
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"

    model = SentenceTransformer(args.model, device=device)
    if device == "cuda":
        model.half()
    prompt_rows = build_taxonomy_prompt_rows()
    prompt_embeddings = model.encode(
        [row["prompt"] for row in prompt_rows],
        batch_size=32,
        show_progress_bar=False,
    ).astype(np.float32)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_columns(conn)
    rows = conn.execute(
        """
        SELECT id, arquivo, texto_extraido, descricao_ia, tags, visual_json,
               style, source_work, humor, context, embedding
        FROM memes
        WHERE embedding IS NOT NULL
        ORDER BY id
        """
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]

    samples = []
    updates = 0
    for row in tqdm(rows, desc="Enriquecendo metadados"):
        embedding = np.frombuffer(row["embedding"], dtype=np.float32).copy()
        text_content = " ".join(
            str(row[name] or "")
            for name in ["arquivo", "texto_extraido", "descricao_ia", "tags"]
        )
        matches = classify_embedding(
            embedding,
            prompt_embeddings,
            prompt_rows,
            text_content=text_content,
        )
        if not matches:
            continue

        profile = merge_taxonomy_into_profile(row["visual_json"], matches)
        style = values_for_field(matches, "style", row["style"] or "")
        source_work = values_for_field(matches, "source_work", row["source_work"] or "")
        humor = values_for_field(matches, "humor", row["humor"] or "")
        context = values_for_field(matches, "context", row["context"] or "")
        tags = values_for_field(matches, "style", row["tags"] or "")
        tags = values_for_field(matches, "source_work", tags)
        tags = values_for_field(matches, "context", tags)

        samples.append(
            {
                "arquivo": row["arquivo"],
                "style": style,
                "source_work": source_work,
                "humor": humor,
                "context": context,
                "matches": profile.get("taxonomy_matches", []),
            }
        )
        updates += 1
        if not args.dry_run:
            conn.execute(
                """
                UPDATE memes
                SET visual_json = ?, style = ?, source_work = ?, humor = ?,
                    context = ?, tags = ?
                WHERE id = ?
                """,
                (
                    json.dumps(profile, ensure_ascii=False),
                    style,
                    source_work,
                    humor,
                    context,
                    tags,
                    row["id"],
                ),
            )

    if not args.dry_run:
        conn.commit()
    conn.close()

    sample_path = Path(args.sample_output)
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(
        json.dumps({"updated": updates, "samples": samples[:100]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    action = "Dry-run" if args.dry_run else "Atualizado"
    print(f"{action}: {updates} registro(s) com metadados enriquecidos.")
    print(f"Amostra salva em: {sample_path}")


if __name__ == "__main__":
    main()
