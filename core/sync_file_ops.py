"""Durable filesystem operations for resumable media ingestion."""
from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

from core.file_digest import FileDigest


def durable_move_upload(
    source: Path,
    destination: Path,
    *,
    upload_id: str,
    expected_size: int,
    expected_hash: str,
) -> None:
    """Place an upload at its persisted destination and verify crash-safely.

    A same-filesystem rename is atomic. For separate data/media mounts, copy to
    a deterministic staging file beside the destination, fsync and validate it,
    then atomically rename that staged copy into place. The source remains until
    the destination is verified, so a restart can safely repeat either path.
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
    _verify(source, expected_size, expected_hash)

    try:
        source.replace(destination)
        fsync_directory(destination.parent)
        fsync_directory(source.parent)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    with source.open("rb") as input_file, staging.open("wb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        output_file.flush()
        os.fsync(output_file.fileno())
    _verify(staging, expected_size, expected_hash)
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
