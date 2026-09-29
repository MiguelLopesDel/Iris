from __future__ import annotations

import pytest

from core.sync_upload_metadata import UploadMetadataError, parse_upload_metadata


def test_upload_metadata_is_normalized_and_source_fields_are_bounded() -> None:
    parsed = parse_upload_metadata({
        "filename": "camera/../photo.jpg",
        "size": 12,
        "sha256": "A" * 64,
        "captured_at": "x" * 80,
        "source": {
            "id": "i" * 600,
            "media_store_id": "m" * 140,
            "generation": "7",
            "media_kind": "image",
        },
    })

    assert parsed.filename == "photo.jpg"
    assert parsed.size == 12
    assert parsed.sha256 == "a" * 64
    assert len(parsed.captured_at) == 64
    assert len(parsed.source["id"]) == 512
    assert len(parsed.source["media_store_id"]) == 128
    assert parsed.source["generation"] == 7


@pytest.mark.parametrize("size", [True, False, -1, 1.5, "12", None])
def test_upload_metadata_rejects_invalid_size(size) -> None:
    with pytest.raises(UploadMetadataError, match="Metadados"):
        parse_upload_metadata({"filename": "photo.jpg", "size": size, "sha256": "a" * 64})


def test_upload_metadata_rejects_non_object_payload() -> None:
    with pytest.raises(UploadMetadataError, match="Metadados"):
        parse_upload_metadata([])


@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64, "a" * 63 + " "])
def test_upload_metadata_rejects_invalid_digest(digest: str) -> None:
    with pytest.raises(UploadMetadataError, match="Metadados"):
        parse_upload_metadata({"filename": "photo.jpg", "size": 0, "sha256": digest})


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({"media_kind": "audio"}, "media kind"),
        ({"generation": "not-a-number"}, "generation"),
    ],
)
def test_upload_metadata_rejects_invalid_source(source: dict, message: str) -> None:
    with pytest.raises(UploadMetadataError, match=message):
        parse_upload_metadata({
            "filename": "photo.jpg", "size": 0, "sha256": "a" * 64,
            "source": source,
        })


def test_upload_metadata_defaults_missing_source() -> None:
    parsed = parse_upload_metadata({"filename": "photo.jpg", "size": 0, "sha256": "a" * 64})
    assert parsed.source == {
        "id": "", "name": "", "relative_path": "", "volume": "",
        "media_store_id": "", "generation": 0, "media_kind": "",
    }
