"""Background work yields to the machine: low priority, and waits while it is busy."""
from __future__ import annotations

import os
import threading

from core.polite import (
    Pressure,
    PressureGate,
    make_current_thread_polite,
    parse_some_avg10,
    read_pressure,
)

PSI = "some avg10=12.50 avg60=3.00 avg300=1.00 total=1\nfull avg10=9.00 avg60=0 avg300=0 total=0\n"


def test_reads_the_share_of_time_stalled():
    assert parse_some_avg10(PSI) == 12.5
    assert parse_some_avg10("") == 0.0


def test_read_pressure_from_a_directory(tmp_path):
    (tmp_path / "cpu").write_text(PSI)
    (tmp_path / "io").write_text(PSI.replace("12.50", "30.00"))

    assert read_pressure(tmp_path) == Pressure(cpu=12.5, io=30.0)
    assert read_pressure(tmp_path / "missing") is None


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_waits_while_the_disk_is_busy_and_goes_once_calm():
    clock = _Clock()
    readings = iter([Pressure(0, 50), Pressure(0, 50), Pressure(0, 5)])
    gate = PressureGate(max_io=20, max_cpu=40, max_wait_s=600, poll_s=10,
                        read=lambda: next(readings), sleep=clock.sleep, clock=clock)

    assert gate.wait_until_calm() == 20


def test_never_waits_longer_than_the_limit():
    clock = _Clock()
    gate = PressureGate(max_io=20, max_cpu=40, max_wait_s=30, poll_s=10,
                        read=lambda: Pressure(90, 90), sleep=clock.sleep, clock=clock)

    assert gate.wait_until_calm() == 30


def test_a_kernel_without_pressure_figures_never_blocks():
    gate = PressureGate(read=lambda: None, sleep=lambda s: (_ for _ in ()).throw(AssertionError))

    assert gate.wait_until_calm() < 0.01


def test_stopping_ends_the_wait():
    stopping = threading.Event()
    stopping.set()
    gate = PressureGate(max_wait_s=600, read=lambda: Pressure(90, 90))

    assert gate.wait_until_calm(stopping) < 1


def test_only_the_calling_thread_is_lowered():
    before = os.getpriority(os.PRIO_PROCESS, 0)
    seen = {}

    def worker():
        make_current_thread_polite()
        seen["nice"] = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen["nice"] == 19
    assert os.getpriority(os.PRIO_PROCESS, 0) == before
