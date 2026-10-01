"""Attach a legacy single-library catalog to an existing, still empty account.

First-run setup migrates a legacy library into the first administrator.
This covers the other case: the server was installed fresh, the account
exists, and the old catalog (with its library folders and media) was copied
over afterwards. The account's empty catalog is set aside, never deleted,
and the same journaled move as setup brings the legacy library in; a
failure puts everything back, leaving the account as it was.
"""
from __future__ import annotations

import errno
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from core.embedding_models import resolve_embedding_model
from core.first_setup import (
    LegacyLibrary,
    MigrationFailed,
    SetupError,
    _catalog_companions,
    _MoveJournal,
    _remove_empty_tree,
    check_legacy_library,
    check_migration_space,
    move_legacy_into,
)
from core.instance_lock import ServerRunning, exclusive_maintenance
from core.users_db import IrisUser

logger = logging.getLogger("iris")

# Uploads in these states hold bytes the account would lose track of.
_ACTIVE_UPLOAD_STATES = ("uploading", "finalizing", "pending_processing", "processing")


def _read_only(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def catalog_models(db: Path) -> set[str]:
    conn = _read_only(db)
    try:
        if "memes" not in _tables(conn):
            return set()
        return {
            str(row[0]) for row in conn.execute("SELECT DISTINCT model_name FROM memes")
            if row[0]
        }
    finally:
        conn.close()


def check_account_is_empty(user: IrisUser) -> None:
    """Refuse an account that has anything of its own."""
    if user.db_path.exists():
        conn = _read_only(user.db_path)
        try:
            tables = _tables(conn)
            items = conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0] if "memes" in tables else 0
            uploads = 0
            if "sync_uploads" in tables:
                marks = ", ".join("?" for _ in _ACTIVE_UPLOAD_STATES)
                uploads = conn.execute(
                    f"SELECT COUNT(*) FROM sync_uploads WHERE state IN ({marks})", _ACTIVE_UPLOAD_STATES
                ).fetchone()[0]
        finally:
            conn.close()
        if items:
            raise SetupError(
                f"A conta '{user.username}' já tem {items} itens; anexar substituiria a biblioteca dela. "
                "Só é possível anexar a uma conta vazia."
            )
        if uploads:
            raise SetupError(
                f"A conta '{user.username}' tem {uploads} envios em andamento; conclua-os antes de anexar."
            )
    if user.media_root.exists() and any(path.is_file() for path in user.media_root.rglob("*")):
        raise SetupError(
            f"A pasta de mídia da conta '{user.username}' não está vazia ({user.media_root})."
        )


def check_attachable(user: IrisUser, legacy: LegacyLibrary, source_dir: Path) -> None:
    if not legacy.has_db:
        raise SetupError(f"Nenhum catálogo encontrado: {legacy.db}")
    check_legacy_library(legacy, source_dir)
    models = catalog_models(legacy.db)
    expected = resolve_embedding_model(user.model_name)
    if models - {expected}:
        raise SetupError(
            f"O catálogo foi indexado com {', '.join(sorted(models))}, e a conta usa {expected}; "
            "a busca não funcionaria. Use uma conta com o mesmo modelo."
        )
    check_account_is_empty(user)
    check_migration_space(legacy, source_dir, user.db_path.parent)


def _set_aside_paths(user: IrisUser) -> list[Path]:
    root = user.db_path.parent
    paths = [user.db_path] if user.db_path.exists() else []
    paths.extend(path for path, _ in _catalog_companions(user.db_path))
    paths.extend(path for path in (root / "thumbnails", root / "sync_uploads") if path.exists())
    return paths


def attach_legacy_library(
    data_dir: Path, user: IrisUser, legacy: LegacyLibrary, source_dir: Path
) -> Path | None:
    """Make ``legacy`` the library of the empty account ``user``.

    Runs only while no server uses ``data_dir`` (see :mod:`core.instance_lock`),
    and keeps any from starting until it is done. Returns the folder holding
    the account's previous (empty) catalog, or ``None`` when there was
    nothing to set aside.
    """
    try:
        with exclusive_maintenance(data_dir):
            return _attach(user, legacy, source_dir)
    except ServerRunning as exc:
        raise SetupError(str(exc)) from exc


def _attach(user: IrisUser, legacy: LegacyLibrary, source_dir: Path) -> Path | None:
    check_attachable(user, legacy, source_dir)
    root = user.db_path.parent
    journal = _MoveJournal()
    aside_paths = _set_aside_paths(user)
    aside = root / f"replaced-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}" if aside_paths else None
    try:
        if aside is not None:
            aside.mkdir()
            for path in aside_paths:
                journal.move(path, aside / path.name)
        move_legacy_into(user, legacy, source_dir, journal)
    except Exception as exc:
        out_of_space = isinstance(exc, OSError) and exc.errno == errno.ENOSPC
        stuck = journal.undo()
        if not stuck and aside is not None:
            stuck = [f"{path} (ficou na pasta da conta)" for path in _remove_empty_tree(aside)]
        if not stuck:
            logger.warning("attach_rolled_back error_type=%s", type(exc).__name__)
            reason = "falta espaço em disco" if out_of_space else str(exc)
            raise MigrationFailed(
                f"Anexar a biblioteca falhou ({reason}). Nada foi alterado: a conta continua vazia "
                "e a biblioteca continua onde estava.",
                rolled_back=True, out_of_space=out_of_space,
            ) from exc
        logger.error("attach_stuck error_type=%s files=%d", type(exc).__name__, len(stuck))
        raise MigrationFailed(
            f"Anexar a biblioteca falhou ({exc}) e não pôde ser desfeito por completo; "
            "confira estes arquivos antes de usar a conta: " + "; ".join(stuck),
            rolled_back=False, out_of_space=out_of_space,
        ) from exc
    logger.info("library_attached user_id=%s", user.id)
    return aside
