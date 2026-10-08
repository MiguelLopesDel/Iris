from __future__ import annotations

import hashlib

import pytest

from core.sync_file_ops import VerifiedUploadSource, durable_move_upload


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


def test_verified_source_skips_second_hash_on_same_filesystem(tmp_path, monkeypatch) -> None:
    source = tmp_path / "incoming" / "upload.part"
    destination = tmp_path / "library" / "photo.jpg"
    source.parent.mkdir()
    payload = b"verified media"
    source.write_bytes(payload)
    verified = VerifiedUploadSource.calculate(source)

    def unexpected_hash(*args, **kwargs):
        raise AssertionError("same-filesystem move should not hash the source again")

    monkeypatch.setattr("core.sync_file_ops.FileDigest.sha256", unexpected_hash)
    durable_move_upload(
        source,
        destination,
        upload_id="upload-verified",
        expected_size=len(payload),
        expected_hash=hashlib.sha256(payload).hexdigest(),
        verified_source=verified,
    )

    assert destination.read_bytes() == payload
    assert not source.exists()


def test_verified_source_rechecks_if_identity_changed(tmp_path) -> None:
    source = tmp_path / "incoming.part"
    destination = tmp_path / "library" / "photo.jpg"
    payload = b"verified media"
    source.write_bytes(payload)
    verified = VerifiedUploadSource.calculate(source)
    source.write_bytes(b"tampered media")

    with pytest.raises(ValueError, match="does not match"):
        durable_move_upload(
            source,
            destination,
            upload_id="upload-changed",
            expected_size=len(payload),
            expected_hash=hashlib.sha256(payload).hexdigest(),
            verified_source=verified,
        )

    assert source.read_bytes() == b"tampered media"
    assert not destination.exists()


def test_cross_filesystem_copy_hashes_while_copying_verified_source(tmp_path, monkeypatch) -> None:
    source = tmp_path / "incoming.part"
    destination = tmp_path / "library" / "photo.jpg"
    payload = b"verified media" * 100
    source.write_bytes(payload)
    verified = VerifiedUploadSource.calculate(source)
    original_replace = type(source).replace

    def exdev_for_source(path, target):
        if path == source:
            raise OSError(18, "cross-device link")
        return original_replace(path, target)

    monkeypatch.setattr(type(source), "replace", exdev_for_source)
    durable_move_upload(
        source,
        destination,
        upload_id="upload-cross-device",
        expected_size=len(payload),
        expected_hash=hashlib.sha256(payload).hexdigest(),
        verified_source=verified,
    )

    assert destination.read_bytes() == payload
    assert not source.exists()
