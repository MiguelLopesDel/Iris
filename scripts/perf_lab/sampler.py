"""Server-side resource samples: CPU, threads, disk and the server's own probe.

Every sample holds rates over the interval since the previous one, so a
report can line them up with what the actors saw at the same moment.

- CPU: total and per core. One core near 100% while the total is low points
  at a serial step (the GIL, one busy thread), not at a lack of CPU.
- Server threads by state: R (running), D (waiting on I/O, uninterruptible),
  S (sleeping). Many in D with a busy disk means "waiting for the disk"; many
  in R or one core pegged means "executing".
- Disk, for the block device holding the data and every device under it
  (a dm/LUKS device and its physical disk): utilization, average queue,
  MB/s and flushes per second (forced writes: fsync and friends).
- Probe (IRIS_PERF_PROBE=1): event-loop lag and thread-pool load.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from scripts.sample_sync_resources import sample_resources

_SECTOR = 512


@dataclass(frozen=True)
class DiskCounters:
    reads: int
    sectors_read: int
    writes: int
    sectors_written: int
    io_ticks_ms: int
    weighted_ms: int
    flushes: int


def parse_diskstats(text: str) -> dict[str, DiskCounters]:
    """Counters per device from /proc/diskstats (flush fields since Linux 5.5)."""
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        values = [int(value) for value in fields[3:]]
        result[fields[2]] = DiskCounters(
            reads=values[0],
            sectors_read=values[2],
            writes=values[4],
            sectors_written=values[6],
            io_ticks_ms=values[9],
            weighted_ms=values[10],
            flushes=values[15] if len(values) > 15 else 0,
        )
    return result


def disk_rates(before: DiskCounters, after: DiskCounters, seconds: float) -> dict[str, float]:
    elapsed_ms = max(seconds * 1000.0, 1e-9)
    return {
        "util_pct": min(100.0, 100.0 * (after.io_ticks_ms - before.io_ticks_ms) / elapsed_ms),
        "queue": (after.weighted_ms - before.weighted_ms) / elapsed_ms,
        "read_mb_s": (after.sectors_read - before.sectors_read) * _SECTOR / 1e6 / seconds,
        "write_mb_s": (after.sectors_written - before.sectors_written) * _SECTOR / 1e6 / seconds,
        "writes_s": (after.writes - before.writes) / seconds,
        "flushes_s": (after.flushes - before.flushes) / seconds,
    }


def devices_for(path: Path) -> list[str]:
    """The block device holding ``path`` and every device it sits on."""
    stat = os.stat(path)
    major, minor = os.major(stat.st_dev), os.minor(stat.st_dev)
    link = Path(f"/sys/dev/block/{major}:{minor}")
    if not link.exists():
        # btrfs reports an anonymous device; find it through the mount table.
        return _devices_from_mounts(path)
    return _with_slaves(link.resolve().name)


def _devices_from_mounts(path: Path) -> list[str]:
    best, device = "", None
    target = str(path.resolve())
    for line in Path("/proc/mounts").read_text().splitlines():
        source, mount_point = line.split()[:2]
        if (
            source.startswith("/dev/")
            and target.startswith(mount_point)
            and len(mount_point) > len(best)
        ):
            best, device = mount_point, Path(source).resolve().name
    return _with_slaves(device) if device else []


def _kernel_name(name: str) -> str:
    """A device-mapper name (cryptroot) as the kernel lists it (dm-0)."""
    if Path(f"/sys/class/block/{name}").exists():
        return name
    for mapped in Path("/sys/block").glob("dm-*/dm/name"):
        if mapped.read_text().strip() == name:
            return mapped.parent.parent.name
    return name


def _parent_device(name: str, sys_class_block: Path = Path("/sys/class/block")) -> str | None:
    device = sys_class_block / name
    if (device / "partition").exists():
        return device.resolve().parent.name
    return None


def _with_slaves(name: str) -> list[str]:
    name = _kernel_name(name)
    names = [name]
    slaves = Path(f"/sys/class/block/{name}/slaves")
    if slaves.is_dir():
        for slave in sorted(slaves.iterdir()):
            names.extend(_with_slaves(slave.name))
    # Include the parent disk too: Linux records flush counters on the whole
    # device, not necessarily on the mounted partition.
    parent = _parent_device(name)
    if parent:
        names.extend(_with_slaves(parent))
    return list(dict.fromkeys(names))


def thread_states(pid: int) -> dict[str, int]:
    counts = {"R": 0, "D": 0, "S": 0, "other": 0}
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            state = (task / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue
        counts[state if state in counts else "other"] += 1
    return counts


class Sampler:
    def __init__(self, pid: int, data_path: Path, probe=None) -> None:
        self.pid = pid
        self.process = psutil.Process(pid)
        self.devices = devices_for(data_path)
        self.probe = probe  # callable returning the server probe's dict, or None
        self.started = time.monotonic()
        self._last_time = self.started
        self._last_disk = self._read_disk()
        psutil.cpu_percent(percpu=True)
        self.process.cpu_percent(None)

    def _read_disk(self) -> dict[str, DiskCounters]:
        stats = parse_diskstats(Path("/proc/diskstats").read_text())
        return {name: stats[name] for name in self.devices if name in stats}

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        seconds = max(now - self._last_time, 1e-3)
        disk = self._read_disk()
        per_core = psutil.cpu_percent(percpu=True)
        baseline = sample_resources(self.process, host_cpu_percent=sum(per_core) / len(per_core))
        result: dict[str, Any] = {
            "t": round(now - self.started, 3),
            "cpu_total": round(sum(per_core) / len(per_core), 1),
            "cpu_max_core": round(max(per_core), 1),
            "cpu_cores": [round(value, 1) for value in per_core],
            "server_cpu": round(baseline["process"]["cpu_percent"], 1),
            "server_threads": thread_states(self.pid),
            # Reuse the established process/host/cgroup collector instead of
            # maintaining a second implementation of those measurements.
            "resource_sample": baseline,
            "disk": {
                name: {
                    key: round(value, 2)
                    for key, value in disk_rates(self._last_disk[name], disk[name], seconds).items()
                }
                for name in disk
                if name in self._last_disk
            },
        }
        if self.probe is not None:
            try:
                result["probe"] = self.probe()
            except Exception as exc:  # the server may be busy or restarting
                result["probe_error"] = type(exc).__name__
        self._last_time, self._last_disk = now, disk
        return result
