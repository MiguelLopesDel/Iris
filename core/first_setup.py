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
import re
import secrets
import shutil
import sqlite3
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
class LegacyStore:
    """A library registered in the legacy catalog (table ``media_libraries``).

    Files imported with copy-to-library live in its folder, referenced by a
    path relative to it; the folder's absolute path was recorded at import
    time and goes stale when the project moves, so ``directory`` is where the
    folder actually is now, or ``None`` when it is gone.
    """

    library_id: int
    name: str
    recorded_root: str
    directory: Path | None


@dataclass(frozen=True)
class LegacySummary:
    items: int
    with_file: int
    missing: int  # items whose file exists nowhere any more
    outside: int  # files that exist only outside data/ and media/, which setup cannot bring


@dataclass(frozen=True)
class LegacyLibrary:
    """What setup finds from a pre-accounts single library."""

    db: Path
    media_root: Path
    has_db: bool
    has_media: bool
    stores: tuple[LegacyStore, ...] = ()


def _read_stores(db: Path, data_dir: Path) -> tuple[LegacyStore, ...]:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return ()
    try:
        rows = conn.execute("SELECT id, name, root_path FROM media_libraries ORDER BY id").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    stores = []
    for library_id, name, root in rows:
        recorded = Path(root) if root else None
        if recorded is not None and not recorded.is_absolute():
            recorded = (data_dir.parent / recorded).resolve()
        if recorded is not None and recorded.is_dir():
            directory: Path | None = recorded.resolve()
        elif (data_dir / "library" / str(name)).is_dir():
            # The project moved since import: the folder is where import puts it.
            directory = (data_dir / "library" / str(name)).resolve()
        else:
            directory = None
        stores.append(LegacyStore(int(library_id), str(name), str(root or ""), directory))
    return tuple(stores)


def find_legacy_library(data_dir: Path, media_root: Path, db: Path | None = None) -> LegacyLibrary:
    source_db = (db or default_legacy_db(data_dir)).resolve()
    source_media = media_root.resolve()
    has_media = source_media.is_dir() and any(source_media.iterdir())
    has_db = source_db.exists()
    stores = _read_stores(source_db, data_dir.resolve()) if has_db else ()
    return LegacyLibrary(source_db, source_media, has_db, has_media, stores)


def check_legacy_library(legacy: LegacyLibrary, data_dir: Path | None = None) -> None:
    # An existing but empty media/ is not a legacy library: the Docker image
    # creates that directory in every fresh install. What deserves a refusal
    # is a genuine half-migration -- files on one side and nothing on the other.
    if legacy.has_db and not legacy.media_root.is_dir():
        raise SetupError("Migração incompleta: o banco antigo existe mas a pasta de mídia não")
    if legacy.has_media and not legacy.has_db:
        raise SetupError("Migração incompleta: há mídia antiga mas nenhum banco para indexá-la")
    if data_dir is not None:
        data = data_dir.resolve()
        for store in legacy.stores:
            if store.directory is not None and (store.directory == data or store.directory in data.parents):
                raise SetupError(
                    f"A biblioteca '{store.name}' aponta para {store.directory}, que contém a pasta de dados; "
                    "mover isso para a conta não é seguro. Ajuste o caminho dela antes do setup."
                )


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def summarize_legacy_library(legacy: LegacyLibrary) -> LegacySummary:
    """Where each catalogued item's file is, as the server would look for it."""
    if not legacy.has_db:
        return LegacySummary(0, 0, 0, 0)
    stores = {store.library_id: store for store in legacy.stores}
    movable_roots = [legacy.media_root] + [store.directory for store in legacy.stores if store.directory]
    try:
        conn = sqlite3.connect(f"file:{legacy.db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT caminho, relative_path, storage_path, library_id FROM memes"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        # An empty or older catalog: nothing to count, and nothing to block setup over.
        rows = []
    with_file = missing = outside = 0
    for caminho, relative_path, storage_path, library_id in rows:
        candidates: list[Path] = []
        store = stores.get(int(library_id)) if library_id is not None else None
        if store and store.directory and storage_path:
            candidates.append(store.directory / storage_path)
        if relative_path:
            candidates.append(legacy.media_root / relative_path)
        if caminho:
            candidates.append(Path(caminho))
            candidates.append(legacy.media_root / Path(caminho).name)
        found = next((c for c in candidates if c.is_file()), None)
        if found is None:
            missing += 1
        elif any(_within(found.resolve(), root) for root in movable_roots):
            with_file += 1
        else:
            outside += 1
    return LegacySummary(len(rows), with_file, missing, outside)


# Files that belong to a catalog database and move with it, renamed to the
# account's database name: SQLite's write-ahead log (which may hold commits
# not yet in the .db), the FAISS indexes and the embedding sidecars.
_COMPANION_SUFFIX = re.compile(r"^(\.db-wal|\.db-shm|_image\.faiss|_desc\.faiss|\.[a-z0-9_]+\.vec)$")


def _catalog_companions(db: Path) -> list[tuple[Path, str]]:
    stem = db.stem
    companions = []
    for path in sorted(db.parent.glob(f"{stem}*")):
        rest = path.name[len(stem):]
        if path != db and _COMPANION_SUFFIX.match(rest):
            companions.append((path, rest))
    return companions


# Room left free on the data disk beyond what the migration copies.
_SPACE_HEADROOM_BYTES = 256 * 1024 * 1024


def _tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _separate_stores(legacy: LegacyLibrary) -> list[LegacyStore]:
    """Library folders that move on their own (not already inside media/)."""
    return [
        store for store in legacy.stores
        if store.directory is not None and not _within(store.directory, legacy.media_root)
    ]


def _migration_sources(legacy: LegacyLibrary, data_dir: Path) -> list[Path]:
    sources = [legacy.db] if legacy.db.exists() else []
    sources.extend(path for path, _ in _catalog_companions(legacy.db))
    if legacy.media_root.is_dir():
        sources.extend(sorted(legacy.media_root.iterdir()))
    thumbnails = data_dir / "thumbnails"
    if thumbnails.exists():
        sources.append(thumbnails)
    sources.extend(store.directory for store in _separate_stores(legacy))
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


def _move(source: Path, destination: Path, on_copied=lambda: None) -> None:
    """Move without ever being the cause of the only complete copy disappearing.

    Within one filesystem this is a rename. Across filesystems it copies,
    calls ``on_copied`` once the copy is complete, and only then removes the
    original. A failed copy leaves the original untouched and its partial
    copy is discarded. A failure while removing the original happens after
    ``on_copied``: the complete copy is already accounted for and is kept.
    """
    try:
        os.rename(source, destination)
        on_copied()
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
    on_copied()
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
        # Recorded as soon as the destination holds a complete copy, before the
        # original is removed: if that removal fails partway, undo must know
        # the destination is the copy to keep.
        _move(source, destination, on_copied=lambda: self._done.append((source, destination)))

    def undo(self) -> list[str]:
        """Move everything back, newest first; returns what could not be restored.

        Never overwrites: an original that still (partly) exists means its
        removal failed, so the complete copy stays where it is and is reported.
        """
        stuck = []
        for source, destination in reversed(self._done):
            try:
                if source.exists():
                    raise FileExistsError(f"a origem ainda existe em parte: {source}")
                source.parent.mkdir(parents=True, exist_ok=True)
                _move(destination, source)
            except Exception as exc:  # keep restoring the rest
                stuck.append(f"{destination} -> {source} ({exc})")
        self._done.clear()
        return stuck


def _remove_empty_tree(root: Path) -> list[Path]:
    """Remove ``root`` if only empty directories remain; return any files left.

    Undoing setup removes the folders the new account created and nothing
    else: a file there means something was not put back, and it is kept.
    """
    if not root.exists():
        return []
    files = [path for path in root.rglob("*") if not path.is_dir()]
    if files:
        return files
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        directory.rmdir()
    root.rmdir()
    return []


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


def _move_stores(legacy: LegacyLibrary, user_media: Path, journal: _MoveJournal) -> dict[int, Path]:
    """Bring each library folder into the account; return each library's new root."""
    # The legacy media/ has already been moved into the account's media/ top
    # level, so anything there is the user's own; a "library" folder among it
    # keeps its name and the libraries go next to it instead.
    parent = user_media / "library"
    if parent.exists():
        parent = user_media / "legacy-libraries"
    new_roots: dict[int, Path] = {}
    for store in legacy.stores:
        if store.directory is None:
            continue
        if _within(store.directory, legacy.media_root):
            new_roots[store.library_id] = user_media / store.directory.relative_to(legacy.media_root)
            continue
        target = parent / store.name
        suffix = 2
        while target.exists():
            target = parent / f"{store.name}-{suffix}"
            suffix += 1
        parent.mkdir(parents=True, exist_ok=True)
        journal.move(store.directory, target)
        new_roots[store.library_id] = target
    return new_roots


def _rewrite_library_roots(db_path: Path, new_roots: dict[int, Path]) -> None:
    if not new_roots:
        return
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "UPDATE media_libraries SET root_path = ? WHERE id = ?",
                [(str(root), library_id) for library_id, root in new_roots.items()],
            )
    finally:
        conn.close()


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
    check_legacy_library(legacy, data_dir)
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
        journal.move(legacy.db, user.db_path)
        for source, rest in _catalog_companions(legacy.db):
            journal.move(source, destination_root / f"{user.db_path.stem}{rest}")
        _move_media_contents(legacy.media_root, user.media_root, journal)
        old_thumbnails = data_dir / "thumbnails"
        if old_thumbnails.exists():
            journal.move(old_thumbnails, destination_root / "thumbnails")
        new_roots = _move_stores(legacy, user.media_root, journal)
        for path in (destination_root, user.media_root, destination_root / "thumbnails"):
            if path.exists():
                os.chmod(path, 0o700)
        # Last: until every file is in place, the catalog keeps its old roots,
        # so a rollback puts back a database that still matches its files.
        _rewrite_library_roots(user.db_path, new_roots)
    except Exception as exc:
        out_of_space = isinstance(exc, OSError) and exc.errno == errno.ENOSPC
        stuck = journal.undo()
        if not stuck:
            stuck = [f"{path} (ficou na pasta da conta)" for path in _remove_empty_tree(destination_root)]
        if not stuck:
            delete_user(users_db, user.id)
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
    return user
