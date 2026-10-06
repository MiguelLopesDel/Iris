"""Small shared statistical helpers for client and server measurements."""

from __future__ import annotations

from math import floor


def percentile(values: list[float], quantile: float) -> float:
    """Return a linearly interpolated percentile on the inclusive [0, 1] scale."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
