#!/usr/bin/env python3
"""Extrai rostos das mídias já indexadas (sem recomputar CLIP) e agrupa em pessoas.

Necessário uma vez para catálogos indexados antes da feature de busca por pessoa existir.
Idempotente: processa apenas mídias que ainda não têm linhas em ``faces``.

Uso:
    python scripts/backfill_faces.py --db data/iris_v1.db
    python scripts/backfill_faces.py --db data/iris_v1.db --limit 500
    python scripts/backfill_faces.py --db data/iris_v1.db --recluster   # reagrupa do zero
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402
from tqdm import tqdm  # noqa: E402

from core import faces as faces_mod  # noqa: E402
from core.indexer import _sample_video_frames  # noqa: E402
from core.indexer_db import init_db  # noqa: E402
from core.search_engine import VIDEO_EXTENSIONS  # noqa: E402


def _load_images(record) -> tuple[list[Image.Image], list[float | None]]:
    path = record.resolved_path
    if not path or not Path(path).exists():
        return [], []
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        sampled = _sample_video_frames(Path(path), 6)
        return [img for img, _t in sampled], [t for _img, t in sampled]
    try:
        return [Image.open(path).convert("RGB")], [None]
    except Exception:
        return [], []


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill de rostos para mídias já indexadas.")
    parser.add_argument("--db", "-b", required=True, help="Banco SQLite alvo.")
    parser.add_argument("--limit", type=int, default=None, help="Processa no máximo N mídias.")
    parser.add_argument("--device", default=None, help="cuda | cpu (auto se omitido).")
    parser.add_argument(
        "--recluster",
        action="store_true",
        help="Refaz o agrupamento de pessoas do zero ao final.",
    )
    parser.add_argument(
        "--no-cluster",
        action="store_true",
        help="Não agrupa em pessoas ao final (só extrai rostos).",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Banco não encontrado: {db_path}")

    # Garante o schema (tabelas faces/persons) sem tocar nos embeddings CLIP.
    init_db(db_path).close()

    # Carrega registros sem o modelo CLIP (apenas metadados + caminhos resolvidos).
    from core.search_engine import IrisEngine

    engine = IrisEngine(db_path=str(db_path), load_model=False)
    device = args.device or engine.device

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        already = {
            r[0] for r in conn.execute("SELECT DISTINCT meme_id FROM faces").fetchall()
        }
        pending = [r for r in engine.records if r.db_id and r.db_id not in already]
        if args.limit:
            pending = pending[: args.limit]

        print(f"Mídias no catálogo: {len(engine.records)} · já com rostos: {len(already)}")
        print(f"A processar: {len(pending)}")

        total_faces = 0
        for record in tqdm(pending, desc="Extraindo rostos"):
            images, times = _load_images(record)
            if not images:
                continue
            try:
                total_faces += faces_mod.extract_faces_for_record(
                    conn, record.db_id, images, frame_times=times, device=device
                )
            except Exception as exc:  # noqa: BLE001
                print(f"\n! Falha em {record.arquivo}: {exc}")

        print(f"\nRostos extraídos: {total_faces}")

        if not args.no_cluster:
            print("Agrupando em pessoas...")
            stats = faces_mod.cluster_faces(conn, recluster=args.recluster)
            print(f"Pessoas: {stats['persons']} · rostos atribuídos: {stats['assigned']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
