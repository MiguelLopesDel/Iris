"""Minimal performance tracer — writes timing events to data/perf.log.

Usage:
    from core.perf import trace, dump

    with trace("gallery.render_cards"):
        ...  # code being measured

    # At the end of main():
    dump()  # writes all timings to data/perf.log
"""

from __future__ import annotations

import time
from pathlib import Path

_LOG_PATH = Path("data/perf.log")
_events: list[tuple[str, float]] = []
_depth = 0


def trace(label: str) -> Span:
    return Span(label)


class Span:
    """Context manager that records elapsed time on exit."""
    def __init__(self, label: str):
        self.label = label
        self.start = 0.0

    def __enter__(self):
        global _depth
        self.start = time.perf_counter()
        _depth += 1
        return self

    def __exit__(self, *args):
        global _depth
        elapsed = time.perf_counter() - self.start
        _depth -= 1
        prefix = "  " * _depth
        _events.append((f"{prefix}{self.label}", elapsed))
        return False


def dump() -> None:
    """Write all recorded events to data/perf.log."""
    if not _events:
        return
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "w") as f:
        total = 0.0
        for label, elapsed in _events:
            f.write(f"[{elapsed*1000:7.1f}ms] {label}\n")
            total += elapsed
        f.write(f"{'─' * 50}\n")
        f.write(f"[{total*1000:7.1f}ms] TOTAL\n")


def reset() -> None:
    _events.clear()
