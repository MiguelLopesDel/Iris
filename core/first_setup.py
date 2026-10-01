"""First-run setup: the one-time installation code and the first administrator.

A private server starts without accounts. Whoever completes setup becomes
its administrator, so setup is gated by a code the installer prints and the
server logs: reaching the page is not enough, the person must also be able
to read the host's console or data folder. The code is deleted once the
first account exists and setup closes for good.
"""

from __future__ import annotations

import errno
import hmac
import logging
import os
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from core.indexer_db import init_db
from core.users_db import IrisUser, create_user, delete_user, has_users

logger = logging.getLogger("iris")

CODE_FILE = "setup_code"
# No 0/O or 1/I/L: the code is read off a terminal and typed by hand.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8


class SetupError(Exception):
    """A setup request that cannot proceed; the message is safe to show."""


class MigrationFailed(SetupError):
    """Moving the legacy library failed after the administrator was created.

    ``rolled_back`` says whether everything was put back (no account, the
    library whole in its original place) so setup can simply be retried.
    """

    def __init__(self, message: str, *, rolled_back: bool, out_of_space: bool) -> None:
        super().__init__(message)
        self.rolled_back = rolled_back
        self.out_of_space = out_of_space


def ensure_setup_code(data_dir: Path) -> str:
    """The pending code, created once (owner-only file) and reused across restarts."""
    path = data_dir / CODE_FILE
    if path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
    code = f"{raw[:4]}-{raw[4:]}"
    data_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{CODE_FILE}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(code + "\n")
    os.replace(temporary, path)
    return code


def clear_setup_code(data_dir: Path) -> None:
    (data_dir / CODE_FILE).unlink(missing_ok=True)


def _normalize(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def code_matches(data_dir: Path, submitted: str) -> bool:
    path = data_dir / CODE_FILE
    if not path.is_file():
        return False
    expected = _normalize(path.read_text(encoding="utf-8"))
    return bool(expected) and hmac.compare_digest(expected, _normalize(submitted))


class AttemptLimiter:
    """Bounds code guesses: a 31^8 code is safe only if it cannot be brute-forced."""

    def __init__(self, max_failures: int = 10, window_seconds: float = 600.0, clock=time.monotonic):
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: list[float] = []
        self._lock = threading.Lock()

    def blocked(self) -> bool:
        with self._lock:
            self._prune()
            return len(self._failures) >= self._max

    def record_failure(self) -> None:
        with self._lock:
            self._prune()
            self._failures.append(self._clock())

    def _prune(self) -> None:
        cutoff = self._clock() - self._window
        self._failures = [moment for moment in self._failures if moment > cutoff]


# -- the first administrator ----------------------------------------------------


def default_legacy_db(data_dir: Path) -> Path:
    """Match the legacy-catalog fallback used by the application server."""
    iris_db = data_dir / "iris_v1.db"
    meme_compass_db = data_dir / "meme_compass_full_v1.db"
    if not iris_db.exists() and meme_compass_db.exists():
        return meme_compass_db
    return iris_db


@dataclass(frozen=True)
class LegacyLibrary:
    """What setup finds from a pre-accounts single library."""

    db: Path
    media_root: Path
    has_db: bool
    has_media: bool


def find_legacy_library(data_dir: Path, media_root: Path, db: Path | None = None) -> LegacyLibrary:
    source_db = (db or default_legacy_db(data_dir)).resolve()
    source_media = media_root.resolve()
    has_media = source_media.is_dir() and any(source_media.iterdir())
    return LegacyLibrary(source_db, source_media, source_db.exists(), has_media)


def check_legacy_library(legacy: LegacyLibrary) -> None:
    # An existing but empty media/ is not a legacy library: the Docker image
    # creates that directory in every fresh install. What deserves a refusal
    # is a genuine half-migration -- files on one side and nothing on the other.
    if legacy.has_db and not legacy.media_root.is_dir():
        raise SetupError("Migração incompleta: o banco antigo existe mas a pasta de mídia não")
    if legacy.has_media and not legacy.has_db:
        raise SetupError("Migração incompleta: há mídia antiga mas nenhum banco para indexá-la")


def _faiss_files(db_path: Path) -> list[Path]:
    prefix = db_path.with_suffix("")
    return [
        prefix.with_name(f"{prefix.name}_image.faiss"),
        prefix.with_name(f"{prefix.name}_desc.faiss"),
    ]


# Room left free on the data disk beyond what the migration copies.
_SPACE_HEADROOM_BYTES = 256 * 1024 * 1024


def _tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _migration_sources(legacy: LegacyLibrary, data_dir: Path) -> list[Path]:
    sources = [p for p in [legacy.db, *_faiss_files(legacy.db)] if p.exists()]
    if legacy.media_root.is_dir():
        sources.extend(sorted(legacy.media_root.iterdir()))
    thumbnails = data_dir / "thumbnails"
    if thumbnails.exists():
        sources.append(thumbnails)
    return sources


def _device_of(path: Path) -> int:
    return path.stat().st_dev


def check_migration_space(legacy: LegacyLibrary, data_dir: Path) -> None:
    """Refuse before anything is created when the moved library will not fit.

    A move within one filesystem is a rename and needs no room; only what
    crosses filesystems (a separate media mount, say) is copied first.
    """
    if not legacy.has_db:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    data_device = _device_of(data_dir)
    to_copy = sum(
        _tree_bytes(source) for source in _migration_sources(legacy, data_dir)
        if _device_of(source) != data_device
    )
    if not to_copy:
        return
    free = shutil.disk_usage(data_dir).free
    if to_copy + _SPACE_HEADROOM_BYTES > free:
        raise SetupError(
            f"Espaço insuficiente para migrar a biblioteca: é preciso copiar {to_copy / 1e9:.1f} GB "
            f"para {data_dir} e há {free / 1e9:.1f} GB livres. Libere espaço e tente de novo."
        )


def _move(source: Path, destination: Path) -> None:
    """Move without ever being the cause of the only complete copy disappearing.

    Within one filesystem this is a rename. Across filesystems it copies, and
    only when the copy is complete removes the original. A failed copy leaves
    the original untouched and its partial copy is discarded; a failure while
    removing the original leaves both, and the complete copy is kept.
    """
    try:
        os.rename(source, destination)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    try:
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copy2(source, destination)
    except BaseException:
        if destination.is_dir():
            shutil.rmtree(destination, ignore_errors=True)
        else:
            destination.unlink(missing_ok=True)
        raise
    if source.is_dir():
        shutil.rmtree(source)
    else:
        source.unlink()


class _MoveJournal:
    """Moves files and remembers how, so a failed migration can be put back."""

    def __init__(self) -> None:
        self._done: list[tuple[Path, Path]] = []

    def move(self, source: Path, destination: Path) -> None:
        if destination.exists():
            raise FileExistsError(f"destino já existe: {destination}")
        _move(source, destination)
        self._done.append((source, destination))

    def undo(self) -> list[str]:
        """Move everything back, newest first; returns what could not be restored."""
        stuck = []
        for source, destination in reversed(self._done):
            try:
                if source.exists():
                    raise FileExistsError(f"a origem já existe: {source}")
                source.parent.mkdir(parents=True, exist_ok=True)
                _move(destination, source)
            except Exception as exc:  # keep restoring the rest
                stuck.append(f"{destination} -> {source} ({exc})")
        self._done.clear()
        return stuck


def _move_media_contents(source_root: Path, destination_root: Path, journal: _MoveJournal | None = None) -> None:
    """Move children without attempting to remove a Docker bind-mount root.

    ``/app/media`` is commonly a bind mount while private libraries live under
    the separate ``/app/data`` bind mount. Moving the root directory makes
    ``shutil.move`` copy the library and then fail while trying to remove the
    mount point. Moving children permits the normal copy-and-delete fallback
    across filesystems and leaves an empty legacy mount behind.
    """
    journal = journal or _MoveJournal()
    destination_root.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_root.iterdir()):
        journal.move(source, destination_root / source.name)


def create_first_admin(
    users_db: Path,
    data_dir: Path,
    legacy: LegacyLibrary,
    *,
    username: str,
    password_hash: str,
    display_name: str = "",
) -> IrisUser:
    """Create the first administrator, moving a legacy library into it if one exists.

    Either the account exists with the whole library, or, when moving fails
    (a full disk, say), everything is put back and no account remains, so
    setup can be retried. Only if putting back also fails does the account
    stay, with :class:`MigrationFailed` saying which files are where.
    """
    if has_users(users_db):
        raise SetupError("O Iris já tem contas; a configuração inicial só roda uma vez")
    check_legacy_library(legacy)
    check_migration_space(legacy, data_dir)
    user = create_user(
        users_db, data_dir, username=username, password_hash=password_hash,
        display_name=display_name, is_admin=True,
    )
    destination_root = user.db_path.parent
    if not legacy.has_db:
        init_db(user.db_path).close()
        return user
    journal = _MoveJournal()
    try:
        for source in [legacy.db, *_faiss_files(legacy.db)]:
            if source.exists():
                target = (
                    user.db_path if source == legacy.db
                    else destination_root / source.name.replace(legacy.db.stem, user.db_path.stem, 1)
                )
                journal.move(source, target)
        _move_media_contents(legacy.media_root, user.media_root, journal)
        old_thumbnails = data_dir / "thumbnails"
        if old_thumbnails.exists():
            journal.move(old_thumbnails, destination_root / "thumbnails")
    except Exception as exc:
        out_of_space = isinstance(exc, OSError) and exc.errno == errno.ENOSPC
        stuck = journal.undo()
        if not stuck:
            delete_user(users_db, user.id)
            shutil.rmtree(destination_root, ignore_errors=True)
            logger.warning("setup_migration_rolled_back error_type=%s", type(exc).__name__)
            reason = "falta espaço em disco" if out_of_space else str(exc)
            raise MigrationFailed(
                f"A migração da biblioteca falhou ({reason}). Nada foi alterado: "
                "nenhuma conta foi criada e a biblioteca continua onde estava. "
                "Resolva o problema e tente de novo.",
                rolled_back=True, out_of_space=out_of_space,
            ) from exc
        logger.error("setup_migration_stuck error_type=%s files=%d", type(exc).__name__, len(stuck))
        raise MigrationFailed(
            f"A migração falhou ({exc}) e não pôde ser desfeita por completo. A conta "
            f"'{user.username}' foi criada; confira estes arquivos antes de usar o Iris: "
            + "; ".join(stuck),
            rolled_back=False, out_of_space=out_of_space,
        ) from exc
    for path in (destination_root, user.media_root, destination_root / "thumbnails"):
        if path.exists():
            os.chmod(path, 0o700)
    return user
