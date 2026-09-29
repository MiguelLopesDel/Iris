"""Streaming file digests shared by catalog, sync, and backup code."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar


class FileDigest:
    """Compute content digests without loading whole files into memory."""

    DEFAULT_CHUNK_SIZE: ClassVar[int] = 1024 * 1024

    @staticmethod
    def sha256(path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
        return FileDigest.sha256_with_size(path, chunk_size)[0]

    @staticmethod
    def sha256_with_size(
        path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> tuple[str, int]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(chunk_size), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size
