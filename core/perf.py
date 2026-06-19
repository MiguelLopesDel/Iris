"""Minimal performance tracer — writes timing events to data/perf.log.

Disabled by default: ``trace()`` is a near-zero-cost no-op unless ``IRIS_PERF=1``,
so the always-on ``with trace(...)`` wrapping every endpoint costs nothing in normal
runs. When enabled, events go into a bounded ring buffer (thread-safe), so a
long-running server never accumulates unbounded memory.

Usage:
    from core.perf import trace, dump

    with trace("gallery.render_cards"):
        ...  # code being measured

    dump()  # writes all timings to data/perf.log
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from pathlib import Path

_LOG_PATH = Path("data/perf.log")
# Ring-buffer cap: bounds memory even if the process runs for days.
_MAX_EVENTS = 5000

# Enabled only when IRIS_PERF=1 (read once at import). When off, trace() returns a
# shared no-op span and records nothing.
ENABLED = os.environ.get("IRIS_PERF") == "1"

_events: deque[tuple[str, float]] = deque(maxlen=_MAX_EVENTS)
_lock = threading.Lock()
# Per-thread nesting depth so indentation stays correct under the ASGI threadpool.
_local = threading.local()


class _NullSpan:
    """Zero-cost context manager used when tracing is disabled."""

    __slots__ = ()

    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


_NULL_SPAN = _NullSpan()


def trace(label: str) -> _NullSpan | Span:
    if not ENABLED:
        return _NULL_SPAN
    return Span(label)


def _depth() -> int:
    return getattr(_local, "depth", 0)


class Span:
    """Context manager that records elapsed time on exit."""

    __slots__ = ("label", "start")

    def __init__(self, label: str):
        self.label = label
        self.start = 0.0

    def __enter__(self) -> Span:
        self.start = time.perf_counter()
        _local.depth = _depth() + 1
        return self

    def __exit__(self, *args: object) -> bool:
        elapsed = time.perf_counter() - self.start
        _local.depth = max(_depth() - 1, 0)
        prefix = "  " * _depth()
        with _lock:
            _events.append((f"{prefix}{self.label}", elapsed))
        return False


def dump() -> None:
    """Write all recorded events to data/perf.log."""
    with _lock:
        events = list(_events)
    if not events:
        return
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "w") as f:
        total = 0.0
        for label, elapsed in events:
            f.write(f"[{elapsed*1000:7.1f}ms] {label}\n")
            total += elapsed
        f.write(f"{'─' * 50}\n")
        f.write(f"[{total*1000:7.1f}ms] TOTAL\n")


def reset() -> None:
    with _lock:
        _events.clear()
