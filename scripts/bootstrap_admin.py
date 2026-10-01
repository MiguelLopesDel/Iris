#!/usr/bin/env python3
"""Create the first private administrator, optionally migrating a legacy library.

The usual path is the web setup at /setup; this is the terminal alternative
for headless installs. Both use core.first_setup.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.auth import hash_password
from core.first_setup import (
    SetupError,
    check_legacy_library,
    clear_setup_code,
    create_first_admin,
    find_legacy_library,
    summarize_legacy_library,
)
from core.users_db import has_users

_PASSWORD_ATTEMPTS = 3


def _read_password() -> str:
    """Prompt until the password is valid and confirmed, instead of failing with a traceback."""
    for _ in range(_PASSWORD_ATTEMPTS):
        password = getpass.getpass("Senha do administrador (mínimo 12 caracteres): ")
        if len(password) < 12:
            print("A senha precisa ter pelo menos 12 caracteres. Tente de novo.", file=sys.stderr)
            continue
        if getpass.getpass("Repita a senha: ") != password:
            print("As senhas não conferem. Tente de novo.", file=sys.stderr)
            continue
        return password
    raise SystemExit("Nenhuma senha válida informada; nada foi criado.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--db", type=Path)
    parser.add_argument("--media-root", type=Path, default=Path("media"))
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    users_db = data_dir / "users.db"
    if has_users(users_db):
        parser.error("data/users.db já possui contas; o bootstrap só roda uma vez")
    legacy = find_legacy_library(data_dir, args.media_root, args.db)
    try:
        check_legacy_library(legacy, data_dir)
    except SetupError as exc:
        parser.error(str(exc))
    if legacy.has_db:
        summary = summarize_legacy_library(legacy)
        print(
            f"Biblioteca antiga: {summary.items} itens — {summary.with_file} serão movidos para a conta, "
            f"{summary.missing} já não têm arquivo, {summary.outside} estão fora de data/ e media/ "
            "e não serão movidos."
        )

    password = _read_password()
    if args.dry_run:
        action = (
            f"moveria {legacy.db} e {legacy.media_root}" if legacy.has_db else "criaria uma biblioteca vazia"
        )
        print(f"Criaria a conta {args.username!r} e {action}.")
        return 0

    try:
        user = create_first_admin(
            users_db, data_dir, legacy,
            username=args.username, password_hash=hash_password(password),
            display_name=args.display_name,
        )
    except SetupError as exc:
        parser.error(str(exc))
    except Exception as exc:
        print(f"Migração interrompida: {exc}. Não inicie o Iris até verificar os arquivos.", file=sys.stderr)
        return 1
    clear_setup_code(data_dir)
    print(f"Conta administradora criada: {user.username}")
    if legacy.has_db:
        print("Biblioteca antiga migrada para a conta.")
    print("Reinicie o Iris para abrir a tela de login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
