"""Canonical validation for metadata used to reserve device uploads."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SOURCE_FIELD_LIMIT = 512


class UploadMetadataError(ValueError):
    """Upload metadata is malformed or outside the supported contract."""


@dataclass(frozen=True)
class UploadMetadata:
    filename: str
    size: int
    sha256: str
    captured_at: str
    source: dict[str, str | int]


def parse_upload_metadata(payload: dict) -> UploadMetadata:
    """Validate and normalize one upload reservation's shared metadata."""
    if not isinstance(payload, dict):
        raise UploadMetadataError("Metadados de envio inválidos")
    filename = Path(str(payload.get("filename", ""))).name
    size = payload.get("size")
    sha256 = str(payload.get("sha256", "")).lower()
    if (
        not filename
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
    ):
        raise UploadMetadataError("Metadados de envio inválidos")

    raw_source = payload.get("source")
    if not isinstance(raw_source, dict):
        source: dict[str, str | int] = {
            "id": "", "name": "", "relative_path": "", "volume": "",
            "media_store_id": "", "generation": 0, "media_kind": "",
        }
    else:
        media_kind = str(raw_source.get("media_kind", ""))[:16]
        if media_kind not in {"", "image", "video"}:
            raise UploadMetadataError("Invalid source media kind")
        try:
            generation = max(0, int(raw_source.get("generation", 0)))
        except (TypeError, ValueError) as exc:
            raise UploadMetadataError("Invalid source generation") from exc
        source = {
            "id": str(raw_source.get("id", ""))[:_SOURCE_FIELD_LIMIT],
            "name": str(raw_source.get("name", ""))[:_SOURCE_FIELD_LIMIT],
            "relative_path": str(raw_source.get("relative_path", ""))[:_SOURCE_FIELD_LIMIT],
            "volume": str(raw_source.get("volume", ""))[:_SOURCE_FIELD_LIMIT],
            "media_store_id": str(raw_source.get("media_store_id", ""))[:128],
            "generation": generation,
            "media_kind": media_kind,
        }

    return UploadMetadata(
        filename=filename,
        size=size,
        sha256=sha256,
        captured_at=str(payload.get("captured_at", ""))[:64],
        source=source,
    )
