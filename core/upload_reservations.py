"""Account-scoped persistence shared by single and batched upload reservations."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from core.library_quota import pending_upload_bytes, used_bytes


class UploadReservationStore:
    """Keep quota accounting and reservation writes consistent across APIs."""

    @staticmethod
    def remaining_bytes(connection: sqlite3.Connection, quota_bytes: int) -> int:
        return quota_bytes - used_bytes(connection) - pending_upload_bytes(connection)

    @staticmethod
    def insert(
        connection: sqlite3.Connection,
        *,
        upload_id: str,
        device_id: str,
        filename: str,
        expected_size: int,
        expected_hash: str,
        captured_at: str,
        temp_path: Path,
        created_at: str,
        updated_at: str,
        source: dict[str, str | int],
        client_upload_id: str | None = None,
    ) -> None:
        columns = [
            "id", "device_id", "filename", "expected_size", "expected_hash",
            "captured_at", "state", "temp_path", "created_at", "updated_at",
            "source_id", "source_name", "source_relative_path", "source_volume",
            "source_media_store_id", "source_generation", "source_media_kind",
        ]
        values: list[Any] = [
            upload_id, device_id, filename, expected_size, expected_hash,
            captured_at, "uploading", str(temp_path), created_at, updated_at,
            source["id"], source["name"], source["relative_path"], source["volume"],
            source["media_store_id"], source["generation"], source["media_kind"],
        ]
        if client_upload_id is not None:
            columns.append("client_upload_id")
            values.append(client_upload_id)

        quoted_columns = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO sync_uploads ({quoted_columns}) VALUES ({placeholders})",
            values,
        )

    @staticmethod
    def record_source(
        connection: sqlite3.Connection,
        *,
        device_id: str,
        source: dict[str, str | int],
        updated_at: str,
    ) -> None:
        source_id = source["id"]
        if not source_id:
            return
        connection.execute(
            """INSERT INTO device_sources
            (device_id, source_id, name, relative_path, volume, media_kind, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id, source_id) DO UPDATE SET
            name=excluded.name, relative_path=excluded.relative_path,
            volume=excluded.volume, media_kind=excluded.media_kind,
            updated_at=excluded.updated_at""",
            (
                device_id, source_id, source["name"], source["relative_path"],
                source["volume"], source["media_kind"], updated_at,
            ),
        )
