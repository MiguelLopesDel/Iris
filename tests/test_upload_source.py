from __future__ import annotations

from core.upload_source import source_from_upload_row


def test_source_from_upload_row_preserves_the_complete_origin() -> None:
    row = {
        "source_id": "camera:external",
        "source_name": "Camera",
        "source_relative_path": "DCIM/Camera",
        "source_volume": "external",
        "source_media_store_id": "4821",
        "source_generation": 12,
        "source_media_kind": "image",
    }

    assert source_from_upload_row(row) == {
        "id": "camera:external",
        "name": "Camera",
        "relative_path": "DCIM/Camera",
        "volume": "external",
        "media_store_id": "4821",
        "generation": 12,
        "media_kind": "image",
    }
