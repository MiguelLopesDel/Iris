from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.media_inventory import inventory_media, sample_media, write_manifest  # noqa: E402

QUERY_TYPES = [
    ("literal_ocr", "palavras exatas que aparecem na imagem"),
    ("memory", "lembranca vaga do que a imagem era"),
    ("visual", "descricao visual sem depender do texto exato"),
    ("source_style", "obra, personagem, plataforma, estilo ou formato"),
    ("joke_context", "piada, tema ou situacao que a imagem comunica"),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cria um golden set editavel para validar recall antes do indice completo."
    )
    parser.add_argument("--dir", default="media", help="Pasta de midias.")
    parser.add_argument("--output-dir", default="data/eval/golden/golden_30")
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--hash", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    manifest_path = output_dir / "manifest.json"
    queries_path = output_dir / "queries.json"

    media_dir = Path(args.dir)
    items = sample_media(
        inventory_media(media_dir, recursive=args.recursive, compute_hash=args.hash),
        sample_size=args.sample_size,
        seed=args.seed,
    )
    write_manifest(
        manifest_path,
        media_dir,
        items,
        seed=args.seed,
        sample_size=args.sample_size,
    )

    queries = []
    for item in items:
        for category, prompt in QUERY_TYPES:
            queries.append(
                {
                    "query": f"TODO: {prompt}",
                    "expected": [item.name],
                    "kind": "manual",
                    "category": category,
                    "image_id": item.name,
                    "note": f"Editar olhando a imagem {item.relative_path}.",
                }
            )

    queries_path.parent.mkdir(parents=True, exist_ok=True)
    queries_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "description": "Edite todos os campos query antes de avaliar.",
                "query_types": [{"category": key, "prompt": prompt} for key, prompt in QUERY_TYPES],
                "queries": queries,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Golden set criado: {output_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Consultas editaveis: {queries_path}")


if __name__ == "__main__":
    main()
