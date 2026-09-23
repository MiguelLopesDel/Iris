"""Whole-instance backup and restore: accounts, libraries, spaces, originals.

The recovery unit is the instance, not one catalogue (see the migration and
recovery contract): the account registry and session secret, every private
library, every shared space and the original bytes, captured together.

A snapshot is a plain directory tree, readable without Iris::

    <dest>/iris-backup-20260923T140000Z/
        manifest.json          every file: root, path, size, mtime, sha256
        data/...               the data/ root
        media/...              the media/ root, when there is one

SQLite files are copied with the online backup API, so a snapshot of a
running server is consistent per database. Everything Iris can rebuild
(FAISS indexes, vector sidecars, thumbnails, half-received uploads) is left
out: it would double the size and be stale on restore anyway.

Snapshots are incremental the way ``rsync --link-dest`` is: a file whose size
and mtime match the previous snapshot is hard-linked to it instead of copied,
so each snapshot is complete and browsable while an unchanged photo occupies
the destination disk once. Backup files are made read-only because a hard
link shared by several snapshots must never be edited in place.

A snapshot is written under a temporary name and renamed only when complete;
a crash leaves an ``.incomplete-*`` directory that is never used as a base.

Restore never overwrites: it rebuilds each root beside the live one, verifies
every hash, then swaps directories and keeps the previous state as
``<root>.before-restore-<stamp>``. It needs the server stopped.

Every manifest records the Iris version and commit that wrote it, and how it
may be retired: ``policy`` snapshots are subject to :func:`prune`, ``pinned``
ones are kept forever. A manifest without that field predates the retention
policy and is never pruned either.

Standard library only, so restore can run on the host without Iris'
dependencies: ``python3 -m core.instance_backup --help``.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, tzinfo
from pathlib import Path

import tomllib

FORMAT_VERSION = 1
SNAPSHOT_PREFIX = "iris-backup-"
INCOMPLETE_PREFIX = ".incomplete-"
PRUNING_PREFIX = ".pruning-"
LOCK_NAME = ".iris-backup.lock"
POLICY = "policy"  # may be pruned by the retention policy
PINNED = "pinned"  # kept until someone deletes it by hand
_PROJECT = Path(__file__).resolve().parents[1]
_CHUNK = 1024 * 1024
_SQLITE_HEADER = b"SQLite format 3\x00"

# Rebuildable or transient: never part of the recovery unit.
_EXCLUDED_SUFFIXES = (".faiss", ".vec", ".vec.tmp", ".part", "-wal", "-shm", "-journal")
_EXCLUDED_DIRS = {"thumbnails", "incoming", "sync_uploads", "__pycache__"}


class BackupError(RuntimeError):
    """The backup or restore cannot proceed safely."""


@dataclass
class Entry:
    root: str
    path: str
    size: int
    mtime_ns: int
    sha256: str
    kind: str  # "sqlite" | "file"


@dataclass
class Manifest:
    format: int
    created_at: str
    roots: dict[str, str]
    files: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    iris_version: str | None = None
    iris_commit: str | None = None
    # None: written before the retention policy existed, so never pruned.
    retention: str | None = None

    def dump(self, path: Path) -> None:
        payload = asdict(self)
        path.write_text(json.dumps(payload, indent=1, sort_keys=True))

    @classmethod
    def load(cls, path: Path) -> Manifest:
        payload = json.loads(path.read_text())
        if payload.get("format") != FORMAT_VERSION:
            raise BackupError(f"formato de backup desconhecido: {payload.get('format')}")
        payload["files"] = [Entry(**item) for item in payload["files"]]
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in payload.items() if key in known})


@dataclass(frozen=True)
class Summary:
    snapshot: Path
    files: int
    bytes_total: int
    copied: int
    linked: int
    warnings: list[str]


# -- walking the live instance ------------------------------------------------


def _excluded(relative: Path) -> bool:
    if any(part in _EXCLUDED_DIRS for part in relative.parts[:-1]):
        return True
    name = relative.name
    return name.startswith(".probe-") or name.endswith(_EXCLUDED_SUFFIXES)


def _walk(root: Path) -> Iterator[Path]:
    """Regular files under ``root``, sorted, without following symlinks."""
    for directory, subdirs, files in os.walk(root):
        subdirs.sort()
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root)
            if not _excluded(relative):
                yield relative


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_hashing(source: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as reader, destination.open("wb") as writer:
        while chunk := reader.read(_CHUNK):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def _sqlite_copy(source: Path, destination: Path) -> None:
    # Read-only URI: the backup must never create or migrate anything.
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()


def _referenced_originals(data_root: Path) -> Iterable[tuple[Path, str]]:
    """Absolute media paths the private catalogues point to, per library."""
    for database in sorted(data_root.glob("users/*/iris.db")):
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                rows = connection.execute("SELECT caminho FROM memes").fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            continue
        for (caminho,) in rows:
            if caminho and os.path.isabs(caminho):
                yield Path(caminho), str(database.relative_to(data_root))


# -- version --------------------------------------------------------------------


def iris_version() -> str | None:
    try:
        with (_PROJECT / "pyproject.toml").open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None


def iris_commit() -> str | None:
    """The commit this code came from: from the environment inside a container
    (the image carries no .git), from git on a checkout."""
    commit = os.environ.get("IRIS_COMMIT", "").strip()
    if commit:
        return commit
    try:
        result = subprocess.run(
            ["git", "-C", str(_PROJECT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


@contextmanager
def _exclusive(dest: Path) -> Iterator[None]:
    """One backup or prune at a time per destination, across processes."""
    with (dest / LOCK_NAME).open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("já existe um backup em andamento neste destino") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# -- create ---------------------------------------------------------------------


def _link(existing: Path, target: Path) -> bool:
    """Reuse the previous snapshot's copy; False means copy instead.

    exFAT/FAT external disks have no hard links, and a previous snapshot may
    have been pruned by hand: both are reasons to copy, not to fail.
    """
    try:
        os.link(existing, target)
        return True
    except OSError:
        return False


def _latest(dest: Path) -> tuple[Path, Manifest] | None:
    snapshots = sorted(p for p in dest.glob(f"{SNAPSHOT_PREFIX}*") if p.is_dir())
    for snapshot in reversed(snapshots):
        try:
            return snapshot, Manifest.load(snapshot / "manifest.json")
        except (OSError, ValueError, KeyError, BackupError):
            continue
    return None


def _check_roots(roots: dict[str, Path], dest: Path) -> None:
    if "data" not in roots:
        raise BackupError("a raiz 'data' é obrigatória")
    if not (roots["data"] / "users.db").is_file():
        raise BackupError(f"{roots['data']} não parece uma instalação Iris (falta users.db)")
    resolved_dest = dest.resolve()
    for name, root in roots.items():
        resolved = root.resolve()
        if resolved_dest == resolved or resolved_dest.is_relative_to(resolved):
            raise BackupError(f"o destino não pode ficar dentro da raiz '{name}' ({root})")


def create(
    roots: dict[str, Path],
    dest: Path,
    *,
    now: datetime | None = None,
    retention: str = POLICY,
) -> Summary:
    """Write one complete snapshot of ``roots`` under ``dest``."""
    if retention not in (POLICY, PINNED):
        raise BackupError(f"retenção desconhecida: {retention}")
    dest.mkdir(parents=True, exist_ok=True)
    _check_roots(roots, dest)
    with _exclusive(dest):
        return _create(roots, dest, now, retention)


def _create(
    roots: dict[str, Path], dest: Path, now: datetime | None, retention: str
) -> Summary:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    # Two backups in the same second (a manual one right after the scheduled
    # one) must not collide.
    suffix = 0
    while (dest / f"{SNAPSHOT_PREFIX}{stamp}{f'-{suffix}' if suffix else ''}").exists():
        suffix += 1
    if suffix:
        stamp = f"{stamp}-{suffix}"
    final = dest / f"{SNAPSHOT_PREFIX}{stamp}"
    work = dest / f"{INCOMPLETE_PREFIX}{stamp}"
    work.mkdir()
    previous = _latest(dest)
    base: dict[tuple[str, str], Entry] = (
        {(e.root, e.path): e for e in previous[1].files} if previous else {}
    )
    manifest = Manifest(
        format=FORMAT_VERSION,
        created_at=(now or datetime.now(timezone.utc)).isoformat(),
        roots={name: str(root.resolve()) for name, root in roots.items()},
        iris_version=iris_version(),
        iris_commit=iris_commit(),
        retention=retention,
    )
    copied = linked = 0
    for name, root in sorted(roots.items()):
        if not root.is_dir():
            manifest.warnings.append(f"raiz '{name}' ausente: {root}")
            continue
        for relative in _walk(root):
            source = root / relative
            target = work / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                stat = source.stat()
                if _is_sqlite(source):
                    _sqlite_copy(source, target)
                    entry = Entry(name, relative.as_posix(), target.stat().st_size,
                                  stat.st_mtime_ns, _sha256(target), "sqlite")
                    copied += 1
                else:
                    known = base.get((name, relative.as_posix()))
                    unchanged = (
                        previous is not None and known is not None and known.kind == "file"
                        and known.size == stat.st_size and known.mtime_ns == stat.st_mtime_ns
                    )
                    if unchanged and _link(previous[0] / name / relative, target):
                        entry = known
                        linked += 1
                    else:
                        digest = _copy_hashing(source, target)
                        entry = Entry(name, relative.as_posix(), target.stat().st_size,
                                      stat.st_mtime_ns, digest, "file")
                        copied += 1
            except FileNotFoundError:
                # Moved to the trash or renamed while the backup ran.
                manifest.warnings.append(f"sumiu durante o backup: {name}/{relative}")
                target.unlink(missing_ok=True)
                continue
            os.chmod(target, 0o444)
            manifest.files.append(entry)

    covered = [root.resolve() for root in roots.values()]
    for original, library in _referenced_originals(roots["data"]):
        resolved = Path(os.path.normpath(original))
        if not any(resolved.is_relative_to(root) for root in covered):
            manifest.warnings.append(
                f"{library} usa um original fora das raízes do backup: {resolved}"
            )

    manifest.dump(work / "manifest.json")
    os.chmod(work / "manifest.json", 0o444)
    os.rename(work, final)
    return Summary(
        snapshot=final,
        files=len(manifest.files),
        bytes_total=sum(entry.size for entry in manifest.files),
        copied=copied,
        linked=linked,
        warnings=manifest.warnings,
    )


# -- verify -----------------------------------------------------------------------


def verify(snapshot: Path) -> list[str]:
    """Every problem found in ``snapshot``; an empty list means it is sound."""
    if snapshot.name.startswith(INCOMPLETE_PREFIX):
        return ["backup incompleto: foi interrompido antes de terminar"]
    try:
        manifest = Manifest.load(snapshot / "manifest.json")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"manifesto ilegível: {exc}"]
    except BackupError as exc:
        return [str(exc)]
    problems: list[str] = []
    if not any(e.root == "data" and e.path == "users.db" for e in manifest.files):
        problems.append("o backup não contém data/users.db")
    for entry in manifest.files:
        path = snapshot / entry.root / entry.path
        label = f"{entry.root}/{entry.path}"
        if not path.is_file():
            problems.append(f"faltando: {label}")
        elif path.stat().st_size != entry.size or _sha256(path) != entry.sha256:
            problems.append(f"conteúdo alterado: {label}")
        elif entry.kind == "sqlite":
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
                try:
                    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    connection.close()
            except sqlite3.Error as exc:
                result = str(exc)
            if result != "ok":
                problems.append(f"banco corrompido: {label}: {result}")
    return problems


# -- listing and retention -------------------------------------------------------


@dataclass(frozen=True)
class SnapshotInfo:
    path: Path
    created_at: datetime
    iris_version: str | None
    iris_commit: str | None
    retention: str | None  # None: from before the retention policy
    files: int
    bytes_total: int

    @property
    def prunable(self) -> bool:
        return self.retention == POLICY


def snapshots(dest: Path) -> list[SnapshotInfo]:
    """Complete snapshots in ``dest``, oldest first; unreadable ones are skipped."""
    found: list[SnapshotInfo] = []
    for path in sorted(dest.glob(f"{SNAPSHOT_PREFIX}*")):
        try:
            manifest = Manifest.load(path / "manifest.json")
            created = datetime.fromisoformat(manifest.created_at)
        except (OSError, ValueError, KeyError, TypeError, BackupError):
            continue
        found.append(SnapshotInfo(
            path=path,
            created_at=created,
            iris_version=manifest.iris_version,
            iris_commit=manifest.iris_commit,
            retention=manifest.retention,
            files=len(manifest.files),
            bytes_total=sum(entry.size for entry in manifest.files),
        ))
    found.sort(key=lambda info: info.created_at)
    return found


@dataclass(frozen=True)
class RetentionPolicy:
    """How many of the most recent days, weeks and months keep a snapshot.

    Each tier keeps the newest policy snapshot of each of its last N periods;
    a snapshot kept by any tier survives. All three at zero disables pruning.
    """

    daily: int = 7
    weekly: int = 4
    monthly: int = 6

    @property
    def enabled(self) -> bool:
        return self.daily > 0 or self.weekly > 0 or self.monthly > 0


def _keep(managed: list[SnapshotInfo], policy: RetentionPolicy, zone: tzinfo) -> set[Path]:
    newest_first = sorted(managed, key=lambda info: info.created_at, reverse=True)
    kept: set[Path] = {newest_first[0].path} if newest_first else set()
    tiers = (
        (policy.daily, lambda moment: moment.date()),
        (policy.weekly, lambda moment: moment.isocalendar()[:2]),
        (policy.monthly, lambda moment: (moment.year, moment.month)),
    )
    for count, period in tiers:
        seen: set[object] = set()
        for info in newest_first:
            if len(seen) >= count:
                break
            key = period(info.created_at.astimezone(zone))
            if key not in seen:
                seen.add(key)
                kept.add(info.path)
    return kept


def _discard(path: Path) -> None:
    """Rename first, so a half-deleted snapshot never looks like a valid one."""
    doomed = path.with_name(PRUNING_PREFIX + path.name.removeprefix(PRUNING_PREFIX))
    if path != doomed:
        os.rename(path, doomed)
    shutil.rmtree(doomed)


def prune(
    dest: Path,
    policy: RetentionPolicy,
    *,
    zone: tzinfo = timezone.utc,
    dry_run: bool = False,
) -> list[Path]:
    """Delete the policy snapshots the retention policy no longer keeps.

    Pinned snapshots and those written before the policy existed are never
    candidates, and neither is the newest policy snapshot. Deleting a snapshot
    only drops its hard links: bytes still used by a kept snapshot stay.
    """
    if not policy.enabled or not dest.is_dir():
        return []
    with _exclusive(dest):
        managed = [info for info in snapshots(dest) if info.prunable]
        kept = _keep(managed, policy, zone)
        doomed = [info.path for info in managed if info.path not in kept]
        if dry_run:
            return doomed
        for leftover in sorted(dest.glob(f"{PRUNING_PREFIX}*")):
            _discard(leftover)  # finish a deletion a crash interrupted
        for path in doomed:
            _discard(path)
        return doomed


# -- restore ----------------------------------------------------------------------


def restore(snapshot: Path, targets: dict[str, Path], *, now: datetime | None = None) -> dict[str, Path]:
    """Replace each target root with the snapshot's copy; return where the old one went.

    The live roots are renamed, never deleted, and only after the rebuilt
    copy has been verified byte for byte.
    """
    problems = verify(snapshot)
    if problems:
        raise BackupError("backup com problemas, nada foi alterado:\n" + "\n".join(problems[:20]))
    manifest = Manifest.load(snapshot / "manifest.json")
    missing = set(manifest.roots) - set(targets)
    if missing:
        raise BackupError(f"informe o destino para as raízes: {', '.join(sorted(missing))}")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")

    staged: dict[str, Path] = {}
    for name in sorted(manifest.roots):
        target = targets[name]
        staging = target.with_name(f"{target.name}.restoring-{stamp}")
        if staging.exists():
            raise BackupError(f"{staging} já existe")
        staging.mkdir(parents=True)
        os.chmod(staging, 0o700)
        staged[name] = staging

    for entry in manifest.files:
        source = snapshot / entry.root / entry.path
        target = staged[entry.root] / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        # A fresh, writable copy: backup files are shared read-only links.
        if _copy_hashing(source, target) != entry.sha256:
            raise BackupError(f"falha ao copiar {entry.root}/{entry.path}; nada foi trocado")
        os.chmod(target, 0o600)
        if entry.kind == "file":
            os.utime(target, ns=(entry.mtime_ns, entry.mtime_ns))
    for staging in staged.values():
        for directory, _, _ in os.walk(staging):
            os.chmod(directory, 0o700)

    previous: dict[str, Path] = {}
    for name, staging in staged.items():
        target = targets[name]
        if target.exists():
            kept = target.with_name(f"{target.name}.before-restore-{stamp}")
            os.rename(target, kept)
            previous[name] = kept
        os.rename(staging, target)
    return previous


# -- command line -----------------------------------------------------------------


def _roots(pairs: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for pair in pairs:
        name, _, path = pair.partition("=")
        if not name or not path:
            raise BackupError(f"use nome=caminho, recebido: {pair!r}")
        roots[name] = Path(path)
    return roots


_RETENTION_LABELS = {
    POLICY: "segue a política",
    PINNED: "guardado para sempre",
    None: "anterior à política, nunca apagado",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.instance_backup")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("create", help="grava um backup completo e consistente")
    make.add_argument("dest", type=Path)
    make.add_argument("--root", action="append", default=[],
                      help="nome=caminho; padrão: data=data e media=media, se existir")
    make.add_argument("--pin", action="store_true",
                      help="guardar para sempre: a política de retenção não apaga")
    check = commands.add_parser("verify", help="confere hashes e bancos de um backup")
    check.add_argument("snapshot", type=Path)
    back = commands.add_parser("restore", help="restaura com o servidor parado")
    back.add_argument("snapshot", type=Path)
    back.add_argument("--target", action="append", default=[],
                      help="nome=caminho; padrão: data=data e media=media")
    listing = commands.add_parser("list", help="lista os backups de um destino")
    listing.add_argument("dest", type=Path)
    trim = commands.add_parser("prune", help="aplica a política de retenção")
    trim.add_argument("dest", type=Path)
    trim.add_argument("--daily", type=int, default=RetentionPolicy.daily)
    trim.add_argument("--weekly", type=int, default=RetentionPolicy.weekly)
    trim.add_argument("--monthly", type=int, default=RetentionPolicy.monthly)
    trim.add_argument("--timezone", default="UTC", help="fuso dos dias/semanas/meses")
    trim.add_argument("--dry-run", action="store_true", help="só mostra o que sairia")
    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            roots = _roots(args.root) or {"data": Path("data")}
            if not args.root and Path("media").is_dir():
                roots["media"] = Path("media")
            summary = create(roots, args.dest, retention=PINNED if args.pin else POLICY)
            print(f"backup: {summary.snapshot}")
            print(f"{summary.files} arquivos, {summary.bytes_total} bytes "
                  f"({summary.copied} copiados, {summary.linked} reaproveitados)")
            for warning in summary.warnings:
                print(f"aviso: {warning}")
        elif args.command == "verify":
            problems = verify(args.snapshot)
            for problem in problems:
                print(problem)
            print("ok" if not problems else f"{len(problems)} problema(s)")
            return 1 if problems else 0
        elif args.command == "restore":
            targets = _roots(args.target) or {"data": Path("data"), "media": Path("media")}
            kept = restore(args.snapshot, targets)
            print("restaurado.")
            for name, path in kept.items():
                print(f"estado anterior de '{name}' guardado em {path}")
        elif args.command == "list":
            for info in snapshots(args.dest):
                print(f"{info.path.name}  Iris {info.iris_version or '?'}"
                      f" ({info.iris_commit or 'commit ?'})  {info.files} arquivos"
                      f"  {_RETENTION_LABELS[info.retention]}")
            for partial in sorted(args.dest.glob(f"{INCOMPLETE_PREFIX}*")):
                print(f"{partial.name} (incompleto)")
        elif args.command == "prune":
            from zoneinfo import ZoneInfo

            policy = RetentionPolicy(args.daily, args.weekly, args.monthly)
            removed = prune(args.dest, policy, zone=ZoneInfo(args.timezone),
                            dry_run=args.dry_run)
            verb = "sairia" if args.dry_run else "apagado"
            for path in removed:
                print(f"{verb}: {path.name}")
            if not removed:
                print("nada a apagar")
    except BackupError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
