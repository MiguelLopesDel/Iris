"""Map persisted upload source columns to the catalog's origin record shape."""
from __future__ import annotations

from collections.abc import Mapping


def source_from_upload_row(upload: Mapping[str, object]) -> dict[str, str | int]:
    """Return the same origin metadata shape for every upload-processing path."""
    return {
        "id": upload["source_id"],
        "name": upload["source_name"],
        "relative_path": upload["source_relative_path"],
        "volume": upload["source_volume"],
        "media_store_id": upload["source_media_store_id"],
        "generation": upload["source_generation"],
        "media_kind": upload["source_media_kind"],
    }
