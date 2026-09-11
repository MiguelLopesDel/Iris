#!/usr/bin/env python3
"""Preenche ``perceptual_hash`` das imagens já indexadas (sem recomputar CLIP).

``perceptual_hash`` é coluna aditiva, e nunca houve backfill para ela. Num
catálogo real medido aqui, **79% das linhas estavam nulas** — 13.716 de 17.352 —
porque foram indexadas antes de a coluna existir. Toda a detecção de duplicatas
por hash perceptual (MIH, colapso de idênticos, quarentena na importação) enxerga
apenas as linhas preenchidas, então o detector estava operando sobre um quinto do
acervo sem nada indicar isso.

Distingue "ainda não tentei" de "tentei e não há hash": NULL é o primeiro, string
vazia é o segundo. Sem essa diferença, toda execução tentaria de novo as imagens
sem estrutura suficiente para um hash de frequência (cores sólidas, telas em
branco), que são justamente as que nunca vão produzir um.

Idempotente e retomável: processa apenas linhas NULL, então pode ser
interrompido à vontade.

Uso:
    python scripts/backfill_phash.py                      # descobre os bancos
    python scripts/backfill_phash.py --db data/iris_v1.db
    python scripts/backfill_phash.py --dry-run
    python scripts/backfill_phash.py --limit 500
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from core.indexer import _compute_phash  # noqa: E402
from scripts.backfill_thumb_hash import discover_databases  # noqa: E402

# Uma transação por linha faria de um catálogo de 17k 17 mil transações, e a
# escrita dominaria um trabalho que é quase todo decode.
_COMMIT_EVERY = 200

# Só imagens. Vídeo e áudio têm os seus próprios sinais (CLIP multi-frame,
# Chromaprint), e um hash de frequência sobre um frame não representa o arquivo.
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif"})


def _pending(conn: sqlite3.Connection, limit: int | None) -> list[tuple[int, str, str]]:
    """Linhas que nunca foram tentadas: NULL, não string vazia."""
    sql = (
        "SELECT id, arquivo, COALESCE(caminho, '') FROM memes"
        " WHERE perceptual_hash IS NULL ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(int(r[0]), r[1] or "", r[2] or "") for r in conn.execute(sql)]


def _resolve(arquivo: str, caminho: str, media_root: Path | None) -> Path | None:
    """O arquivo, procurado onde o catálogo diz e depois pelo nome na mídia.

    Catálogos movidos entre máquinas guardam caminhos absolutos que já não
    existem; o nome do arquivo sobrevive à mudança.
    """
    candidates = [Path(caminho)] if caminho else []
    if media_root and arquivo:
        candidates.append(media_root / Path(arquivo).name)
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


def backfill(
    db_path: Path, media_root: Path | None, limit: int | None, dry_run: bool
) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        rows = _pending(conn, limit)
        counts = {"hashed": 0, "no_structure": 0, "missing": 0, "unsupported": 0, "failed": 0}
        print(f"{db_path}: {len(rows)} linhas sem perceptual_hash")

        pending_writes: list[tuple[str, int]] = []
        for index, (meme_id, arquivo, caminho) in enumerate(rows, start=1):
            suffix = Path(arquivo or caminho).suffix.lower()
            if suffix not in _IMAGE_SUFFIXES:
                counts["unsupported"] += 1
                continue
            path = _resolve(arquivo, caminho, media_root)
            if path is None:
                counts["missing"] += 1
                continue
            try:
                with Image.open(path) as image:
                    digest = _compute_phash(image.convert("RGB"))
            except Exception:
                counts["failed"] += 1
                continue
            if digest:
                counts["hashed"] += 1
            else:
                # Sem estrutura para um hash de frequência. Gravar vazio impede
                # que a próxima execução tente de novo para sempre.
                counts["no_structure"] += 1
            pending_writes.append((digest or "", meme_id))

            if not dry_run and len(pending_writes) >= _COMMIT_EVERY:
                conn.executemany(
                    "UPDATE memes SET perceptual_hash = ? WHERE id = ?", pending_writes
                )
                conn.commit()
                pending_writes.clear()
            if index % 500 == 0:
                print(f"  {index}/{len(rows)}…", flush=True)

        if pending_writes and not dry_run:
            conn.executemany(
                "UPDATE memes SET perceptual_hash = ? WHERE id = ?", pending_writes
            )
            conn.commit()
        return counts
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", "-b", default=None, help="Banco alvo; omitido, descobre sob data/.")
    parser.add_argument("--media-root", default="media", help="Onde procurar por nome de arquivo.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Mede sem gravar nada.")
    args = parser.parse_args()

    databases = [Path(args.db)] if args.db else discover_databases(Path("data"))
    if not databases:
        print("Nenhum catálogo encontrado.", file=sys.stderr)
        raise SystemExit(1)

    media_root = Path(args.media_root) if args.media_root else None
    if media_root and not media_root.is_dir():
        media_root = None

    for db_path in databases:
        counts = backfill(db_path, media_root, args.limit, args.dry_run)
        prefix = "[simulação] " if args.dry_run else ""
        print(
            f"{prefix}{db_path}: {counts['hashed']} com hash, "
            f"{counts['no_structure']} sem estrutura, {counts['missing']} arquivo ausente, "
            f"{counts['unsupported']} não-imagem, {counts['failed']} falha de leitura"
        )


if __name__ == "__main__":
    main()
