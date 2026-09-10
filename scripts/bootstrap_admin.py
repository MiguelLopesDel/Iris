#!/usr/bin/env python3
"""Create the first private administrator, optionally migrating a legacy library."""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user, has_users


def _default_legacy_db(data_dir: Path) -> Path:
    """Match the legacy-catalog fallback used by the application server."""
    iris_db = data_dir / "iris_v1.db"
    meme_compass_db = data_dir / "meme_compass_full_v1.db"
    if not iris_db.exists() and meme_compass_db.exists():
        return meme_compass_db
    return iris_db


def _faiss_files(db_path: Path) -> list[Path]:
    prefix = db_path.with_suffix("")
    return [
        prefix.with_name(f"{prefix.name}_image.faiss"),
        prefix.with_name(f"{prefix.name}_desc.faiss"),
    ]


def _move_media_contents(source_root: Path, destination_root: Path) -> None:
    """Move children without attempting to remove a Docker bind-mount root.

    ``/app/media`` is commonly a bind mount while private libraries live under
    the separate ``/app/data`` bind mount. Moving the root directory makes
    ``shutil.move`` copy the library and then fail while trying to remove the
    mount point. Moving children permits the normal copy-and-delete fallback
    across filesystems and leaves an empty legacy mount behind.
    """
    destination_root.mkdir(parents=True, exist_ok=True)
    for source in source_root.iterdir():
        destination = destination_root / source.name
        if destination.exists():
            raise FileExistsError(f"destino de mídia já existe: {destination}")
        shutil.move(str(source), str(destination))


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
    source_db = (args.db or _default_legacy_db(data_dir)).resolve()
    source_media = args.media_root.resolve()
    if has_users(users_db):
        parser.error("data/users.db já possui contas; o bootstrap só roda uma vez")
    has_legacy_db = source_db.exists()
    has_legacy_media = source_media.is_dir()
    if has_legacy_db != has_legacy_media:
        parser.error("Migração incompleta: banco e pasta de mídia antigos devem existir juntos")

    password = getpass.getpass("Senha do administrador (mínimo 12 caracteres): ")
    confirmation = getpass.getpass("Repita a senha: ")
    if password != confirmation:
        parser.error("As senhas não conferem")
    if args.dry_run:
        action = f"moveria {source_db} e {source_media}" if has_legacy_db else "criaria uma biblioteca vazia"
        print(f"Criaria a conta {args.username!r} e {action}.")
        return 0

    user = create_user(
        users_db, data_dir, username=args.username, password_hash=hash_password(password),
        display_name=args.display_name, is_admin=True,
    )
    destination_root = user.db_path.parent
    if not has_legacy_db:
        init_db(user.db_path).close()
        print(f"Conta administradora criada: {user.username}")
        print("Biblioteca privada vazia criada. Reinicie o Iris para abrir a tela de login.")
        return 0
    assets = [source_db, *_faiss_files(source_db)]
    try:
        for source in assets:
            if source.exists():
                target = user.db_path if source == source_db else destination_root / source.name.replace(source_db.stem, user.db_path.stem, 1)
                shutil.move(str(source), str(target))
        _move_media_contents(source_media, user.media_root)
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
