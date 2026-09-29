from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from core.file_digest import FileDigest
from core.timestamps import UtcTimestamp


def test_file_digest_streams_and_can_return_size(tmp_path: Path) -> None:
    path = tmp_path / "payload.bin"
    payload = b"iris" * 31
    path.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()
    assert FileDigest.sha256(path, chunk_size=7) == expected
    assert FileDigest.sha256_with_size(path, chunk_size=7) == (expected, len(payload))


def test_file_digest_handles_empty_files(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")

    assert FileDigest.sha256(path) == hashlib.sha256(b"").hexdigest()
    assert FileDigest.sha256_with_size(path) == (hashlib.sha256(b"").hexdigest(), 0)


def test_file_digest_rejects_nonpositive_chunk_size(tmp_path: Path) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"data")

    with pytest.raises(ValueError, match="chunk_size"):
        FileDigest.sha256(path, chunk_size=0)


def test_utc_metadata_timestamp_uses_second_precision_z_suffix() -> None:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", UtcTimestamp.now_iso_seconds())
