#!/usr/bin/env python3
"""Attach a legacy Iris/Meme Compass catalog to an existing, empty account.

Copy the old catalog (with its -wal/.vec/.faiss companions), its library/
folder and media/ into one folder under the data directory, stop the
server, then run, for example:

    docker compose run --rm iris python scripts/attach_library.py --user ana --from data/import

Nothing is deleted: the account's empty catalog is kept in a replaced-*
folder, and a failure puts every file back where it was.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.first_setup import (
    SetupError,
    default_legacy_db,
    find_legacy_library,
    summarize_legacy_library,
)
from core.instance_lock import server_running
from core.library_attach import attach_legacy_library, check_attachable
from core.users_db import get_user_by_username


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", required=True, help="conta que recebe a biblioteca (precisa estar vazia)")
    parser.add_argument("--from", dest="source", type=Path, required=True,
                        help="pasta com o catálogo, library/ e media/ copiados")
    parser.add_argument("--db", type=Path, help="catálogo dentro da pasta (padrão: iris_v1.db ou meme_compass_full_v1.db)")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="pasta de dados do servidor")
    parser.add_argument("--yes", action="store_true", help="não pedir confirmação")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    source = args.source.resolve()
    if not source.is_dir():
        parser.error(f"pasta não encontrada: {source}")
    user = get_user_by_username(data_dir / "users.db", args.user)
    if user is None:
        parser.error(f"conta não encontrada: {args.user}")
    media = source / "media"
    if not media.exists():
        # Libraries carry the files; media/ is optional when there was none.
        media.mkdir()
    legacy = find_legacy_library(source, media, args.db.resolve() if args.db else default_legacy_db(source))
    try:
        if server_running(data_dir):
            raise SetupError("O servidor do Iris está rodando: pare-o (docker compose stop iris) e rode de novo.")
        check_attachable(user, legacy, source)
    except SetupError as exc:
        parser.error(str(exc))

    summary = summarize_legacy_library(legacy)
    print(f"Catálogo: {legacy.db}")
    for store in legacy.stores:
        print(f"Biblioteca '{store.name}': {store.directory or 'pasta não encontrada'}")
    print(
        f"{summary.items} itens: {summary.with_file} serão movidos para a conta '{user.username}', "
        f"{summary.missing} já não têm arquivo, {summary.outside} estão fora da pasta e não serão movidos."
    )
    if not args.yes and input("Anexar? [s/N] ").strip().lower() not in {"s", "sim", "y", "yes"}:
        print("Nada foi alterado.")
        return 1
    try:
        aside = attach_legacy_library(data_dir, user, legacy, source)
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Biblioteca anexada à conta '{user.username}'.")
    if aside is not None:
        print(f"O catálogo vazio anterior da conta ficou em {aside}; pode ser apagado.")
    print("Inicie o servidor (docker compose start iris). Miniaturas são regeradas conforme a galeria abre.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
