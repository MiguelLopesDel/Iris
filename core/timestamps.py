"""Canonical UTC timestamp formatting for catalog metadata."""
from __future__ import annotations

from datetime import UTC, datetime


class UtcTimestamp:
    """Provide the legacy second-precision UTC ``Z`` format used by metadata."""

    @staticmethod
    def now_iso_seconds() -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
