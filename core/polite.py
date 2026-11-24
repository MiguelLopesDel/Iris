"""Background work that yields to everything else on the machine.

Iris runs all the time on a server people use for other things, and most of
its work can wait: processing an upload, warming thumbnails, indexing. That
work must never be what makes someone's film stutter or their game lag. Two
tools keep it out of the way, both for the calling thread only, so a search a
person is waiting for keeps its normal priority:

* :func:`make_current_thread_polite` gives the thread the lowest CPU priority
  (nice 19) and the idle I/O class, which the kernel serves only when no one
  else wants the disk.
* :class:`PressureGate` reads the kernel's pressure-stall figures
  (``/proc/pressure``) before each piece of work and waits while the disk or
  the CPU is already struggling. It waits at most ``max_wait_s`` so work is
  delayed, never abandoned.

Docker sets the ceiling (CPU weight, memory limit); this decides what can wait.
Everything degrades to a no-op where the kernel does not offer it.
"""
from __future__ import annotations

import ctypes
import logging
import os
import platform
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger("iris.polite")

_NICE = 19
# ioprio_set(2): class in the top bits, IDLE = 3.
_IOPRIO_CLASS_SHIFT = 13
_IOPRIO_CLASS_IDLE = 3
_IOPRIO_WHO_PROCESS = 1
_IOPRIO_SET = {"x86_64": 251, "aarch64": 30, "armv7l": 314, "i686": 289}


def make_current_thread_polite() -> bool:
    """Lowest CPU priority and idle I/O for this thread; True if both were applied."""
    if not hasattr(os, "setpriority") or not hasattr(threading, "get_native_id"):
        return False
    tid = threading.get_native_id()
    cpu = io = False
    try:
        # On Linux a thread id is a process id for setpriority: only this thread changes.
        os.setpriority(os.PRIO_PROCESS, tid, _NICE)
        cpu = True
    except OSError:
        pass
    number = _IOPRIO_SET.get(platform.machine())
    if number is not None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            value = _IOPRIO_CLASS_IDLE << _IOPRIO_CLASS_SHIFT
            io = libc.syscall(number, _IOPRIO_WHO_PROCESS, tid, value) == 0
        except Exception:
            io = False
    return cpu and io


@dataclass(frozen=True)
class Pressure:
    """Share of the last 10 s some task was stalled waiting for the resource."""

    cpu: float
    io: float


def parse_some_avg10(text: str) -> float:
    for line in text.splitlines():
        if line.startswith("some "):
            for field in line.split()[1:]:
                name, _, value = field.partition("=")
                if name == "avg10":
                    return float(value)
    return 0.0


def read_pressure(root: Path = Path("/proc/pressure")) -> Pressure | None:
    """The machine's current pressure, or None where the kernel has no PSI."""
    try:
        return Pressure(
            cpu=parse_some_avg10((root / "cpu").read_text()),
            io=parse_some_avg10((root / "io").read_text()),
        )
    except (OSError, ValueError):
        return None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


class PressureGate:
    """Waits before background work while the machine is busy.

    Limits are percentages of time stalled (PSI ``some avg10``). The disk one
    is the strict one: on a spinning disk shared with other services, extra
    seeks hurt everyone else far more than extra CPU does.
    """

    def __init__(
        self,
        *,
        max_io: float | None = None,
        max_cpu: float | None = None,
        max_wait_s: float | None = None,
        poll_s: float = 10.0,
        read: Callable[[], Pressure | None] = read_pressure,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_io = _env_float("IRIS_BACKGROUND_MAX_IO_PRESSURE", 20.0) if max_io is None else max_io
        self.max_cpu = _env_float("IRIS_BACKGROUND_MAX_CPU_PRESSURE", 40.0) if max_cpu is None else max_cpu
        self.max_wait_s = _env_float("IRIS_BACKGROUND_MAX_WAIT_S", 900.0) if max_wait_s is None else max_wait_s
        self.poll_s = poll_s
        self._read = read
        self._sleep = sleep
        self._clock = clock

    def busy(self) -> bool:
        pressure = self._read()
        if pressure is None:
            return False
        return pressure.io > self.max_io or pressure.cpu > self.max_cpu

    def wait_until_calm(self, stopping: threading.Event | None = None) -> float:
        """Block while the machine is busy, up to ``max_wait_s``; return seconds waited."""
        started = self._clock()
        waited_notice = False
        while self.busy():
            waited = self._clock() - started
            if waited >= self.max_wait_s or (stopping is not None and stopping.is_set()):
                break
            if not waited_notice:
                _logger.info("background work waits: the machine is busy")
                waited_notice = True
            if stopping is not None:
                stopping.wait(self.poll_s)
            else:
                self._sleep(self.poll_s)
        return self._clock() - started
