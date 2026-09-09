#!/usr/bin/env python3
"""Move one existing Iris library into the first private administrator account."""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.auth import hash_password
from core.users_db import create_user, has_users


def _faiss_files(db_path: Path) -> list[Path]:
    prefix = db_path.with_suffix("")
    return [
        prefix.with_name(f"{prefix.name}_image.faiss"),
        prefix.with_name(f"{prefix.name}_desc.faiss"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--db", type=Path, default=Path("data/iris_v1.db"))
    parser.add_argument("--media-root", type=Path, default=Path("media"))
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    users_db = data_dir / "users.db"
    source_db = args.db.resolve()
    source_media = args.media_root.resolve()
    if has_users(users_db):
        parser.error("data/users.db já possui contas; o bootstrap só roda uma vez")
    if not source_db.exists():
        parser.error(f"Banco não encontrado: {source_db}")
    if not source_media.is_dir():
        parser.error(f"Biblioteca de mídia não encontrada: {source_media}")

    password = getpass.getpass("Senha do administrador (mínimo 12 caracteres): ")
    confirmation = getpass.getpass("Repita a senha: ")
    if password != confirmation:
        parser.error("As senhas não conferem")
    if args.dry_run:
        print(f"Criaria a conta {args.username!r} e moveria {source_db} e {source_media}.")
        return 0

    user = create_user(
        users_db, data_dir, username=args.username, password_hash=hash_password(password),
        display_name=args.display_name, is_admin=True,
    )
    destination_root = user.db_path.parent
    assets = [source_db, *_faiss_files(source_db)]
    try:
        # create_user creates this private directory; remove the known-empty
        # placeholder before moving the existing library into its final name.
        user.media_root.rmdir()
        for source in assets:
            if source.exists():
                target = user.db_path if source == source_db else destination_root / source.name.replace(source_db.stem, user.db_path.stem, 1)
                shutil.move(str(source), str(target))
        shutil.move(str(source_media), str(user.media_root))
        old_thumbnails = data_dir / "thumbnails"
        if old_thumbnails.exists():
            shutil.move(str(old_thumbnails), str(destination_root / "thumbnails"))
        for path in (destination_root, user.media_root, destination_root / "thumbnails"):
            if path.exists():
                os.chmod(path, 0o700)
        print(f"Conta administradora criada: {user.username}")
        print("Reinicie o Iris. A tela de login será ativada.")
        return 0
    except Exception as exc:
        print(f"Migração interrompida: {exc}. Não inicie o Iris até verificar os arquivos.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
