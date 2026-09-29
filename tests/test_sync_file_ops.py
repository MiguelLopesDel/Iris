from __future__ import annotations

import hashlib

import pytest

from core.sync_file_ops import durable_move_upload


def test_durable_move_is_idempotent_after_destination_is_committed(tmp_path) -> None:
    source = tmp_path / "incoming" / "upload.part"
    destination = tmp_path / "library" / "photo.jpg"
    source.parent.mkdir()
    payload = b"original media bytes"
    source.write_bytes(payload)
    expected_hash = hashlib.sha256(payload).hexdigest()

    durable_move_upload(
        source,
        destination,
        upload_id="upload-1",
        expected_size=len(payload),
        expected_hash=expected_hash,
    )
    assert destination.read_bytes() == payload
    assert not source.exists()

    # A retry after the DB transaction or response was interrupted is safe.
    durable_move_upload(
        source,
        destination,
        upload_id="upload-1",
        expected_size=len(payload),
        expected_hash=expected_hash,
    )
    assert destination.read_bytes() == payload


def test_durable_move_rejects_corrupt_source_without_losing_it(tmp_path) -> None:
    source = tmp_path / "upload.part"
    destination = tmp_path / "library" / "photo.jpg"
    source.write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="does not match"):
        durable_move_upload(
            source,
            destination,
            upload_id="upload-2",
            expected_size=10,
            expected_hash=hashlib.sha256(b"original").hexdigest(),
        )

    assert source.read_bytes() == b"corrupt"
    assert not destination.exists()
