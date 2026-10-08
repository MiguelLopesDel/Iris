"""Validated resource limits for the media-ingestion pipeline.

The policy contains tuning values only. Ingestion stages remain responsible
for enforcing the limits they use; the policy does not define durability
ordering or acceptance semantics.
"""
from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class IngestPolicy:
    """Bounded batch and I/O settings shared by ingestion stages."""

    # These limits describe the new data-plane pipeline. The legacy
    # reservation API keeps its independent 16-item protocol cap.
    max_items: int = 64
    max_bytes: int = 32 << 20
    block_bytes: int = 1 << 20
    fsync_concurrency: int = 64
    # Each batch request already brings its own files; waiting long for other
    # requests only delays it. Measured on the reference HDD with 64 uploads in
    # flight: 0.01 s took 69 photos/s, 0.05 s 67, 0 63 and 0.25 s 53.
    durability_window_s: float = 0.01
    db_group_max_items: int = 256
    db_window_s: float = 0.01
    packages_in_flight_per_device: int = 4
    max_in_flight_bytes: int = 128 << 20

    def __post_init__(self) -> None:
        bounded_ints = (
            # Bytes stream to disk in blocks, so a batch's size bounds disk
            # and request time, not memory; the lab sweeps far above defaults.
            ("max_items", self.max_items, 1024),
            ("max_bytes", self.max_bytes, 512 << 20),
            ("block_bytes", self.block_bytes, 1 << 20),
            ("fsync_concurrency", self.fsync_concurrency, 128),
            ("db_group_max_items", self.db_group_max_items, 256),
            ("packages_in_flight_per_device", self.packages_in_flight_per_device, 8),
            ("max_in_flight_bytes", self.max_in_flight_bytes, 2 << 30),
        )
        for name, value, maximum in bounded_ints:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            if value > maximum:
                raise ValueError(f"{name} cannot exceed {maximum}")

        for name in ("durability_window_s", "db_window_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite non-negative number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")

        if self.durability_window_s > 5 or self.db_window_s > 1:
            raise ValueError("batch windows exceed their safe maximum")

        if self.block_bytes > self.max_bytes:
            raise ValueError("block_bytes cannot exceed max_bytes")
        if self.max_bytes > self.max_in_flight_bytes:
            raise ValueError("max_bytes cannot exceed max_in_flight_bytes")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> IngestPolicy:
        """Defaults overridden by ``IRIS_INGEST_<FIELD>`` variables, validated as usual.

        Lets the performance lab sweep the policy without code changes.
        """
        environ = os.environ if environ is None else environ
        overrides: dict[str, int | float] = {}
        for item in fields(cls):
            raw = environ.get(f"IRIS_INGEST_{item.name.upper()}")
            if raw is None or raw == "":
                continue
            try:
                overrides[item.name] = float(raw) if item.name.endswith("_s") else int(raw)
            except ValueError as exc:
                raise ValueError(f"IRIS_INGEST_{item.name.upper()} is not a number") from exc
        return cls(**overrides)
