"""Durable filesystem operations for resumable media ingestion."""
from __future__ import annotations

import errno
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from core.file_digest import FileDigest


@dataclass(frozen=True)
class VerifiedUploadSource:
    """Digest and file identity observed together for an upload source."""

    sha256: str
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def calculate(cls, path: Path) -> VerifiedUploadSource:
        before = path.stat()
        digest, size = FileDigest.sha256_with_size(path)
        after = path.stat()
        if _file_identity(before) != _file_identity(after) or size != after.st_size:
            raise ValueError("upload source changed while its hash was calculated")
        return cls(digest, size, after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)

    def still_matches(self, path: Path) -> bool:
        try:
            current = path.stat()
        except FileNotFoundError:
            return False
        return (
            current.st_size == self.size
            and _file_identity(current)
            == (self.device, self.inode, self.mtime_ns, self.ctime_ns)
        )


def durable_move_upload(
    source: Path,
    destination: Path,
    *,
    upload_id: str,
    expected_size: int,
    expected_hash: str,
    verified_source: VerifiedUploadSource | None = None,
    unsynced_directories: set[Path] | None = None,
) -> None:
    """Place an upload at its persisted destination and verify crash-safely.

    A same-filesystem rename is atomic. For separate data/media mounts, copy to
    a deterministic staging file beside the destination, fsync and validate it,
    then atomically rename that staged copy into place. The source remains until
    the destination is verified, so a restart can safely repeat either path.

    With ``unsynced_directories``, a plain rename does not sync its two
    directories: it adds them to the set, and the caller syncs each once for
    a whole batch before recording any of its moves. The copy paths still sync
    the destination before removing the source, as the two may sit on
    different filesystems.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.{upload_id}.staging")

    if destination.is_file():
        _verify(destination, expected_size, expected_hash)
        source.unlink(missing_ok=True)
        return

    if staging.is_file() and _matches(staging, expected_size, expected_hash):
        os.replace(staging, destination)
        fsync_directory(destination.parent)
        source.unlink(missing_ok=True)
        fsync_directory(source.parent)
        return

    staging.unlink(missing_ok=True)
    if not source.is_file():
        raise FileNotFoundError("upload source and finalized destination are both absent")
    source_was_verified = (
        verified_source is not None
        and verified_source.sha256 == expected_hash
        and verified_source.size == expected_size
        and verified_source.still_matches(source)
    )
    if not source_was_verified:
        _verify(source, expected_size, expected_hash)

    try:
        source.replace(destination)
        if unsynced_directories is not None:
            unsynced_directories.update((destination.parent, source.parent))
            return
        fsync_directory(destination.parent)
        fsync_directory(source.parent)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    copied_digest = hashlib.sha256()
    copied_size = 0
    with source.open("rb") as input_file, staging.open("wb") as output_file:
        for chunk in iter(lambda: input_file.read(FileDigest.DEFAULT_CHUNK_SIZE), b""):
            output_file.write(chunk)
            copied_digest.update(chunk)
            copied_size += len(chunk)
        output_file.flush()
        os.fsync(output_file.fileno())
    if copied_size != expected_size or copied_digest.hexdigest() != expected_hash:
        staging.unlink(missing_ok=True)
        raise ValueError("uploaded file does not match its declared size/hash")
    os.replace(staging, destination)
    fsync_directory(destination.parent)
    source.unlink(missing_ok=True)
    fsync_directory(source.parent)


def matches_original(path: Path, expected_size: int, expected_hash: str) -> bool:
    """Whether ``path`` is a file holding exactly the declared original."""
    return path.is_file() and _matches(path, expected_size, expected_hash)


def _verify(path: Path, expected_size: int, expected_hash: str) -> None:
    if not _matches(path, expected_size, expected_hash):
        raise ValueError("uploaded file does not match its declared size/hash")


def _matches(path: Path, expected_size: int, expected_hash: str) -> bool:
    return path.stat().st_size == expected_size and FileDigest.sha256(path) == expected_hash


def _file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (stat_result.st_dev, stat_result.st_ino, stat_result.st_mtime_ns, stat_result.st_ctime_ns)


def fsync_directory(path: Path) -> None:
    """Persist directory-entry changes where directory fsync is supported."""
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise
    finally:
        os.close(descriptor)
