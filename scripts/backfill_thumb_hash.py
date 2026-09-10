#!/usr/bin/env python3
"""Preenche ``thumb_hash`` das mídias já indexadas (sem recomputar CLIP).

Necessário uma vez para catálogos indexados antes do placeholder inline existir.
Sem ele, a galeria continua abrindo célula vazia até a miniatura chegar.
Idempotente: processa apenas linhas com ``thumb_hash`` vazio, então pode ser
interrompido e retomado à vontade.

Uso:
    python scripts/backfill_thumb_hash.py --db data/iris_v1.db
    python scripts/backfill_thumb_hash.py --db data/iris_v1.db --limit 500
    python scripts/backfill_thumb_hash.py --db data/iris_v1.db --redo   # refaz todos
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402
from tqdm import tqdm  # noqa: E402

from core.indexer_db import init_db  # noqa: E402
from core.search_engine import VIDEO_EXTENSIONS  # noqa: E402
from core.thumb_hash import GRID_SIDE, encode_thumb_hash  # noqa: E402

# Committing every row would make a 17k-record catalog 17k transactions. The
# work per row is small, so the write amplification would dominate the run.
_COMMIT_EVERY = 200


def _load_image(path_str: str | None) -> Image.Image | None:
    if not path_str:
        return None
    path = Path(path_str)
    if not path.exists():
        return None
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        from core.indexer import _sample_video_frames

        sampled = _sample_video_frames(path, 1)
        return sampled[0][0] if sampled else None
    try:
        img = Image.open(path)
        # Lets the JPEG decoder downscale while decoding instead of after —
        # the difference over a whole catalog is large, and the target is 6x6.
        img.draft("RGB", (GRID_SIDE * 8, GRID_SIDE * 8))
        return img.convert("RGB")
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill do placeholder inline (thumb_hash) para mídias já indexadas."
    )
    parser.add_argument("--db", "-b", required=True, help="Banco SQLite alvo.")
    parser.add_argument("--limit", type=int, default=None, help="Processa no máximo N mídias.")
    parser.add_argument(
        "--redo",
        action="store_true",
        help="Recalcula também as que já têm thumb_hash.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Banco não encontrado: {db_path}")

    # Garante a coluna thumb_hash sem tocar nos embeddings CLIP.
    init_db(db_path).close()

    # Carrega registros sem o modelo CLIP (apenas metadados + caminhos resolvidos).
    from core.search_engine import IrisEngine

    engine = IrisEngine(db_path=str(db_path), load_model=False)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if args.redo:
            pending = [r for r in engine.records if r.db_id]
        else:
            pending = [r for r in engine.records if r.db_id and not r.thumb_hash]
        if args.limit:
            pending = pending[: args.limit]

        print(f"Mídias no catálogo: {len(engine.records)} · a processar: {len(pending)}")

        done = skipped = 0
        for position, record in enumerate(tqdm(pending, desc="Gerando placeholders"), start=1):
            image = _load_image(record.resolved_path)
            encoded = encode_thumb_hash(image) if image is not None else ""
            if not encoded:
                skipped += 1
                continue
            conn.execute(
                "UPDATE memes SET thumb_hash = ? WHERE id = ?", (encoded, record.db_id)
            )
            done += 1
            if position % _COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()

        print(f"\nPlaceholders gravados: {done} · sem imagem legível: {skipped}")
        if done:
            print("Reinicie o servidor Iris para servir os novos placeholders.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
