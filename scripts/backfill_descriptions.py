#!/usr/bin/env python3
"""Repara descrições e tags contaminadas pela saída crua do Florence-2.

Catálogos indexados antes do conserto guardam, no lugar das tags, o eco do
prompt ``<VQA>`` e tokens ``<loc_*>`` — e a descrição embute esse mesmo lixo,
que foi para o índice de busca junto.

**Nada é reprocessado na GPU por imagem.** A legenda produzida pelo
``<MORE_DETAILED_CAPTION>`` está limpa e já gravada dentro da própria
descrição, e as tags são reclassificadas a partir do embedding de imagem que
já está no banco. O único cálculo é o embedding de texto da descrição
reconstruída, que é barato.

Idempotente: só toca em registros contaminados, então pode ser interrompido e
retomado. Use ``--dry-run`` primeiro para ver o tamanho do estrago.

Uso:
    python scripts/backfill_descriptions.py --dry-run
    python scripts/backfill_descriptions.py
    python scripts/backfill_descriptions.py --db data/users/1/iris.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from tqdm import tqdm  # noqa: E402

from core.indexer import build_embedding_text, compose_description  # noqa: E402
from core.indexer_db import init_db  # noqa: E402
from core.taxonomy import (  # noqa: E402
    build_taxonomy_prompt_rows,
    classify_embedding,
    values_for_field,
)
from scripts.backfill_thumb_hash import discover_databases  # noqa: E402

# Marcas do bug: eco do prompt e tokens de coordenada do grounding. Inclui
# também "N/A", que é o texto devolvido quando o Florence não estava carregado
# e que foi gravado como se fosse conteúdo — 4.525 registros exibiam
# "Tags: N/A. Visual: N/A" mesmo tendo OCR e tags aproveitáveis.
_CONTAMINADO = (
    "(descricao_ia LIKE '%<loc_%' OR descricao_ia LIKE '%VQA>%' "
    "OR tags LIKE '%<loc_%' OR tags LIKE '%VQA>%' "
    "OR descricao_ia LIKE '%N/A%')"
)

_COMMIT_EVERY = 200


# Pedaços do prompt que sobrevivem a uma limpeza ingênua por vírgula:
# "VQA>What is this meme (reaction) photo, art)? List 5 keywords" parte em duas
# e a segunda metade não tem marcador nenhum.
_MARCAS_DO_PROMPT = (
    "<loc_",
    "vqa>",
    "what is this meme",
    "list 5 keywords",
    "separated by comma",
    "reaction, comic, photo, art",
)


def _limpar_tags(tags: str | None) -> str:
    """Descarta o valor inteiro se ele for resíduo do prompt.

    Todo-ou-nada de propósito: a saída quebrada é uma frase, não uma lista, e
    tentar recortá-la por vírgula deixa passar fragmentos como
    "art)? List 5 keywords" — que iriam para a busca como se fossem tags.
    """
    if not tags:
        return ""
    baixo = tags.lower()
    if any(marca in baixo for marca in _MARCAS_DO_PROMPT):
        return ""
    return ", ".join(p.strip() for p in tags.split(",") if p.strip())


def _separar_descricao(descricao: str | None) -> tuple[str, str]:
    """Devolve (tags_embutidas, legenda) do formato ``Tags: X. Visual: Y``."""
    if not descricao:
        return "", ""
    if "Visual:" not in descricao:
        return "", descricao.strip()
    cabeca, _, legenda = descricao.partition("Visual:")
    tags = cabeca.partition("Tags:")[2] if "Tags:" in cabeca else ""
    return tags.strip().rstrip("."), legenda.strip()


def reparar(db_path: Path, *, dry_run: bool, limit: int | None) -> None:
    if not db_path.exists():
        raise SystemExit(f"Banco não encontrado: {db_path}")
    init_db(db_path).close()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0]
        afetados = conn.execute(
            f"SELECT COUNT(*) FROM memes WHERE {_CONTAMINADO}"
        ).fetchone()[0]
        print(f"{db_path}: {total} registros · {afetados} contaminados")
        if afetados == 0:
            print("  nada a fazer")
            return

        if dry_run:
            print("\n  amostra do que seria reescrito:\n")
            for row in conn.execute(
                f"SELECT tags, descricao_ia FROM memes WHERE {_CONTAMINADO} LIMIT 3"
            ):
                _, legenda = _separar_descricao(row["descricao_ia"])
                print(f"    antes:  {(row['descricao_ia'] or '')[:100]}")
                limpas = _limpar_tags(row["tags"])
                destino = limpas or "(reclassificadas pela taxonomia)"
                print(f"    depois: Tags: {destino}. Visual: {legenda[:60]}…\n")
            print("  (--dry-run: nada foi gravado)")
            return

        # CLIP só para o texto reconstruído; nenhuma imagem é lida.
        from sentence_transformers import SentenceTransformer

        from core.search_engine import DEFAULT_MODEL, IrisEngine

        engine = IrisEngine(db_path=str(db_path), load_model=False)
        modelo = SentenceTransformer(DEFAULT_MODEL, device=engine.device)
        prompt_rows = build_taxonomy_prompt_rows()
        prompt_embeddings = modelo.encode(
            [row["prompt"] for row in prompt_rows], show_progress_bar=False
        )

        pendentes = conn.execute(
            f"SELECT id, tags, descricao_ia, texto_extraido, embedding FROM memes "
            f"WHERE {_CONTAMINADO}" + (f" LIMIT {limit}" if limit else "")
        ).fetchall()

        gravados = 0
        for posicao, row in enumerate(tqdm(pendentes, desc="Reparando"), start=1):
            _, legenda = _separar_descricao(row["descricao_ia"])
            tags = _limpar_tags(row["tags"])

            # Tags perdidas são recuperáveis do embedding de imagem já gravado —
            # é a mesma classificação que a indexação faria, sem reler o arquivo.
            if not tags and row["embedding"]:
                try:
                    emb = np.frombuffer(row["embedding"], dtype=np.float32).reshape(1, -1)
                    matches = classify_embedding(
                        emb, prompt_embeddings, prompt_rows,
                        text_content=row["texto_extraido"] or "",
                    )
                    tags = values_for_field(matches, "style", "")
                    tags = values_for_field(matches, "source_work", tags)
                    tags = values_for_field(matches, "context", tags)
                except Exception:
                    tags = ""

            descricao = compose_description(tags, legenda)
            texto = build_embedding_text(
                visual=legenda, ocr=row["texto_extraido"] or "", tags=tags, model=modelo
            )
            desc_emb = modelo.encode([texto], show_progress_bar=False)[0].astype(np.float32)

            conn.execute(
                "UPDATE memes SET tags = ?, descricao_ia = ?, desc_embedding = ? WHERE id = ?",
                (tags, descricao, desc_emb.tobytes(), row["id"]),
            )
            gravados += 1
            if posicao % _COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()

        print(f"\n  reparados: {gravados}")
        print("  O índice FAISS de descrição precisa ser recriado:")
        print(f"    python -m core.indexer --db {db_path} --rebuild-faiss-only")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repara descrições contaminadas pela saída crua do Florence-2."
    )
    parser.add_argument("--db", "-b", default=None, help="Banco alvo; omitido, descobre.")
    parser.add_argument("--data-dir", default="data", help="Raiz para descoberta.")
    parser.add_argument("--limit", type=int, default=None, help="Processa no máximo N.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Só mostra o que seria alterado."
    )
    args = parser.parse_args()

    bancos = [Path(args.db)] if args.db else discover_databases(Path(args.data_dir))
    if not bancos:
        raise SystemExit(f"Nenhum catálogo sob {args.data_dir}/. Aponte com --db.")

    for db_path in bancos:
        reparar(db_path, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
