from __future__ import annotations

import argparse
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.media_inventory import inventory_media, sample_media, write_manifest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cria um manifest deterministico de amostra sem copiar ou alterar midias."
    )
    parser.add_argument("--dir", default="media", help="Pasta de midias.")
    parser.add_argument("--output", default="data/eval/samples/sample_100.json")
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument(
        "--hash",
        action="store_true",
        help="Calcula SHA-256 dos arquivos. Mais confiavel, mas mais lento.",
    )
    args = parser.parse_args()

    media_dir = Path(args.dir)
    items = inventory_media(media_dir, recursive=args.recursive, compute_hash=args.hash)
    selected = sample_media(items, sample_size=args.sample_size, seed=args.seed)
    output = Path(args.output)
    write_manifest(output, media_dir, selected, seed=args.seed, sample_size=args.sample_size)

    print(f"Midias encontradas: {len(items)}")
    print(f"Amostra salva: {output} ({len(selected)} itens)")


if __name__ == "__main__":
    main()
