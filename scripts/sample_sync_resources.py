#!/usr/bin/env python3
"""Sample server-process and host/cgroup resources during sync benchmarks."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import psutil


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return int(value) if value.isdigit() else None


def _read_key_values(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (FileNotFoundError, PermissionError, OSError):
        return result
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            result[parts[0]] = int(parts[1])
    return result


def _cgroup_io_bytes(path: Path = Path("/sys/fs/cgroup/io.stat")) -> dict[str, int]:
    totals = {"read_bytes": 0, "write_bytes": 0}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (FileNotFoundError, PermissionError, OSError):
        return {}
    found = False
    for line in lines:
        for field in line.split()[1:]:
            name, separator, value = field.partition("=")
            if separator and name in {"rbytes", "wbytes"} and value.isdigit():
                totals["read_bytes" if name == "rbytes" else "write_bytes"] += int(value)
                found = True
    return totals if found else {}


def sample_resources(process: psutil.Process, *, host_cpu_percent: float, timestamp: float | None = None) -> dict[str, Any]:
    """Return one JSON-safe sample; counters are cumulative, percentages per interval."""
    memory = process.memory_info()
    try:
        io = process.io_counters()
        process_io = {"read_bytes": io.read_bytes, "write_bytes": io.write_bytes}
    except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
        process_io = {}
    try:
        disk = psutil.disk_io_counters()
        host_disk = {"read_bytes": disk.read_bytes, "write_bytes": disk.write_bytes} if disk else {}
    except (OSError, AttributeError):
        host_disk = {}
    try:
        host_memory_percent = psutil.virtual_memory().percent
        host_memory_available_bytes = psutil.virtual_memory().available
    except (OSError, AttributeError):
        host_memory_percent = None
        host_memory_available_bytes = None
    cpu = _read_key_values(Path("/sys/fs/cgroup/cpu.stat"))
    cgroup_io = _cgroup_io_bytes()
    return {
        "timestamp": time.time() if timestamp is None else timestamp,
        "process": {
            "pid": process.pid,
            "cpu_percent": process.cpu_percent(interval=None),
            "rss_bytes": memory.rss,
            "read_bytes": process_io.get("read_bytes"),
            "write_bytes": process_io.get("write_bytes"),
        },
        "host": {
            "cpu_percent": host_cpu_percent,
            "memory_percent": host_memory_percent,
            "memory_available_bytes": host_memory_available_bytes,
            "disk_read_bytes": host_disk.get("read_bytes"),
            "disk_write_bytes": host_disk.get("write_bytes"),
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        },
        "cgroup": {
            "cpu_usage_usec": cpu.get("usage_usec"),
            "cpu_nr_throttled": cpu.get("nr_throttled"),
            "cpu_throttled_usec": cpu.get("throttled_usec"),
            "memory_current_bytes": _read_int(Path("/sys/fs/cgroup/memory.current")),
            "memory_peak_bytes": _read_int(Path("/sys/fs/cgroup/memory.peak")),
            "io_read_bytes": cgroup_io.get("read_bytes"),
            "io_write_bytes": cgroup_io.get("write_bytes"),
        },
    }


def sample_for(pid: int, *, interval: float, duration: float, output: Path) -> int:
    try:
        process = psutil.Process(pid)
        process.cpu_percent(interval=None)
    except psutil.Error as exc:
        print(f"Cannot sample server PID {pid}: {exc}", file=sys.stderr)
        return 2
    psutil.cpu_percent(interval=None)
    deadline = time.monotonic() + duration
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("w", encoding="utf-8") as stream:
            while True:
                try:
                    row = sample_resources(process, host_cpu_percent=psutil.cpu_percent(interval=None))
                except psutil.NoSuchProcess:
                    print(f"Server PID {pid} exited during sampling", file=sys.stderr)
                    return 2
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                stream.flush()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(interval, remaining))
    except OSError as exc:
        print(f"Cannot write samples to {output}: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote resource samples to {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int, help="PID of the disposable Iris server process")
    parser.add_argument("--interval", type=float, default=0.5, help="sample interval in seconds (0.1..10)")
    parser.add_argument("--duration", type=float, required=True, help="sampling duration in seconds")
    parser.add_argument("--output", type=Path, required=True, help="JSONL path for cumulative counters and gauges")
    args = parser.parse_args()
    if args.pid <= 0 or not 0.1 <= args.interval <= 10 or args.duration <= 0:
        parser.error("pid and duration must be positive; interval must be between 0.1 and 10 seconds")
    return sample_for(args.pid, interval=args.interval, duration=args.duration, output=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
