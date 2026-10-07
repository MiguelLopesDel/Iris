"""Event-loop and thread-pool pressure, for the performance lab (scripts/perf_lab).

Enabled only with IRIS_PERF_PROBE=1. A server whose event loop is blocked, or
whose thread pool is full, cannot feed the disk or the network however fast
they are; this exposes both so a benchmark can tell "the software is the
bottleneck" from "the hardware is".
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import anyio.to_thread


def enabled() -> bool:
    return os.environ.get("IRIS_PERF_PROBE") == "1"


class LoopLagProbe:
    """Wakes every ``interval`` seconds and records how late each wake-up was."""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self._max = 0.0
        self._total = 0.0
        self._count = 0
        self._auth: list[float] = []

    def record_auth(self, seconds: float) -> None:
        """Time one request's identity read, kept on the event loop on purpose."""
        self._auth.append(seconds)

    def record(self, lag: float) -> None:
        lag = max(0.0, lag)
        self._max = max(self._max, lag)
        self._total += lag
        self._count += 1

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            await asyncio.sleep(self.interval)
            self.record(loop.time() - started - self.interval)

    def snapshot(self) -> dict[str, Any]:
        """Lag since the previous snapshot, then starts a new window."""
        result = {
            "loop_lag_ms_max": round(self._max * 1000, 2),
            "loop_lag_ms_mean": round(self._total / self._count * 1000, 2) if self._count else 0.0,
            "loop_lag_samples": self._count,
        }
        if self._auth:
            # Work kept on the loop must stay small in its worst case: watch
            # the tail, not only the average.
            ordered = sorted(self._auth)
            result["auth_ms_p99"] = round(ordered[int(0.99 * (len(ordered) - 1))] * 1000, 3)
            result["auth_ms_max"] = round(ordered[-1] * 1000, 3)
            result["auth_samples"] = len(ordered)
        self._max = self._total = 0.0
        self._count = 0
        self._auth = []
        return result


def thread_pool_stats() -> dict[str, int]:
    """The default anyio thread pool (run_in_threadpool); call from the event loop."""
    stats = anyio.to_thread.current_default_thread_limiter().statistics()
    return {
        "threads_busy": stats.borrowed_tokens,
        "threads_total": int(stats.total_tokens),
        "threads_waiting": stats.tasks_waiting,
    }
