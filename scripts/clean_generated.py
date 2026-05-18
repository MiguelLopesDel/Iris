from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_PATTERNS = [
    "data/*.db",
    "data/*.faiss",
    "data/*_manifest.json",
    "data/index_*.json",
    "data/indice_imagens.json",
    "data/logs/*.log",
    "__pycache__",
    "*/__pycache__",
]


def find_targets(patterns: list[str]) -> list[Path]:
    targets: list[Path] = []
    for pattern in patterns:
        targets.extend(Path(".").glob(pattern))
    return sorted(set(targets), key=lambda path: str(path))


def remove_target(path: Path) -> None:
    if path.is_dir():
        for child in sorted(path.iterdir(), reverse=True):
            remove_target(child)
        path.rmdir()
    else:
        path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lista ou remove artefatos gerados pelo Meme Compass."
    )
    parser.add_argument("--apply", action="store_true", help="Remove os arquivos listados.")
    parser.add_argument(
        "--pattern",
        action="append",
        default=[],
        help="Padrao glob adicional. Pode ser usado mais de uma vez.",
    )
    args = parser.parse_args()

    targets = [target for target in find_targets(DEFAULT_PATTERNS + args.pattern) if target.exists()]
    if not targets:
        print("Nenhum artefato gerado encontrado.")
        return

    action = "Removendo" if args.apply else "Dry-run"
    print(f"{action}: {len(targets)} alvo(s)")
    for target in targets:
        print(target)
        if args.apply:
            remove_target(target)

    if not args.apply:
        print("\nUse --apply para remover.")


if __name__ == "__main__":
    main()
