#!/usr/bin/env python3
"""Join Android upload profile timestamps with sampled server/host resources."""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

PROFILE_PREFIX = "IRIS_UPLOAD_REAL_SERVER "
_FIELD = re.compile(r"([A-Za-z_]+)=([^\s]+)")


def parse_profiles(log_text: str) -> list[dict[str, Any]]:
    profiles = []
    for line in log_text.splitlines():
        marker = line.find(PROFILE_PREFIX)
        if marker < 0:
            continue
        fields = dict(_FIELD.findall(line[marker + len(PROFILE_PREFIX):]))
        required = {
            "workers", "photos", "videos", "bytes", "enqueue_hash_ms", "transfer_ms",
            "transfer_start_epoch_ms", "transfer_end_epoch_ms", "MBps", "active_MBps",
        }
        if not required.issubset(fields):
            continue
        try:
            profiles.append({
                "workers": int(fields["workers"]),
                "photos": int(fields["photos"]),
                "videos": int(fields["videos"]),
                "bytes": int(fields["bytes"]),
                "enqueue_hash_ms": float(fields["enqueue_hash_ms"]),
                "transfer_start": int(fields["transfer_start_epoch_ms"]) / 1000,
                "transfer_end": int(fields["transfer_end_epoch_ms"]) / 1000,
                "transfer_seconds": float(fields["transfer_ms"]) / 1000,
                "end_to_end_mbps": float(fields["MBps"]),
                "active_mbps": float(fields["active_MBps"]),
            })
        except (TypeError, ValueError):
            continue
    return profiles


def read_samples(path: Path) -> list[dict[str, Any]]:
    samples = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("timestamp"), (int, float)):
                    raise ValueError("sample must be an object with a numeric timestamp")
                samples.append(row)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid sample at line {line_number}: {exc}") from exc
    return sorted(samples, key=lambda row: row["timestamp"])


def _mean(rows: list[dict[str, Any]], *path: str) -> float | None:
    values = []
    for row in rows:
        value: Any = row
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, (int, float)):
            values.append(float(value))
    return round(statistics.fmean(values), 2) if values else None


def _max(rows: list[dict[str, Any]], *path: str) -> float | None:
    values = []
    for row in rows:
        value: Any = row
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, (int, float)):
            values.append(float(value))
    return max(values) if values else None


def _counter_rate(rows: list[dict[str, Any]], section: str, field: str, seconds: float, divisor: float = 1_000_000) -> float | None:
    values = [row.get(section, {}).get(field) for row in rows]
    values = [value for value in values if isinstance(value, (int, float))]
    if len(values) < 2 or seconds <= 0:
        return None
    return round(max(0, values[-1] - values[0]) / seconds / divisor, 2)


def _counter_delta(rows: list[dict[str, Any]], section: str, field: str) -> int | None:
    values = [row.get(section, {}).get(field) for row in rows]
    values = [value for value in values if isinstance(value, (int, float))]
    return max(0, int(values[-1] - values[0])) if len(values) >= 2 else None


def summarize_profiles(profiles: list[dict[str, Any]], samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        raise ValueError("resource sample file has no samples")
    result_profiles = []
    for profile in profiles:
        start, end = profile["transfer_start"], profile["transfer_end"]
        rows = [row for row in samples if start <= row["timestamp"] <= end]
        if not rows:
            result_profiles.append({**profile, "sample_count": 0, "resources": None})
            continue
        interval_seconds = max(0, rows[-1]["timestamp"] - rows[0]["timestamp"])
        process_rss = _mean(rows, "process", "rss_bytes")
        process_rss_max = _max(rows, "process", "rss_bytes")
        result_profiles.append({
            **profile,
            "sample_count": len(rows),
            "resources": {
                "sampled_interval_seconds": round(interval_seconds, 3),
                "server_process_cpu_percent_mean": _mean(rows, "process", "cpu_percent"),
                "server_process_rss_mib_mean": round(process_rss / 1_048_576, 2) if process_rss is not None else None,
                "server_process_rss_mib_peak": round(process_rss_max / 1_048_576, 2) if process_rss_max is not None else None,
                "server_process_read_mib_per_second": _counter_rate(rows, "process", "read_bytes", interval_seconds),
                "server_process_write_mib_per_second": _counter_rate(rows, "process", "write_bytes", interval_seconds),
                "host_cpu_percent_mean": _mean(rows, "host", "cpu_percent"),
                "host_memory_percent_mean": _mean(rows, "host", "memory_percent"),
                "host_disk_read_mib_per_second": _counter_rate(rows, "host", "disk_read_bytes", interval_seconds),
                "host_disk_write_mib_per_second": _counter_rate(rows, "host", "disk_write_bytes", interval_seconds),
                "cgroup_cpu_cores_mean": _counter_rate(rows, "cgroup", "cpu_usage_usec", interval_seconds, divisor=1_000_000),
                "cgroup_cpu_throttled_usec_delta": _counter_delta(rows, "cgroup", "cpu_throttled_usec"),
                "cgroup_throttle_periods_delta": _counter_delta(rows, "cgroup", "cpu_nr_throttled"),
                "cgroup_memory_mib_mean": (
                    round(_mean(rows, "cgroup", "memory_current_bytes") / 1_048_576, 2)
                    if _mean(rows, "cgroup", "memory_current_bytes") is not None else None
                ),
                "cgroup_read_mib_per_second": _counter_rate(rows, "cgroup", "io_read_bytes", interval_seconds),
                "cgroup_write_mib_per_second": _counter_rate(rows, "cgroup", "io_write_bytes", interval_seconds),
            },
        })
    return {
        "scope": "disposable local Android AVD + Iris lab; host/cgroup counters may include other processes",
        "sample_count": len(samples),
        "profiles": result_profiles,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=Path, help="JSONL from scripts/sample_sync_resources.py")
    parser.add_argument("--android-log", required=True, type=Path, help="adb logcat dump containing IrisUploadBench records")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    try:
        profiles = parse_profiles(args.android_log.read_text(encoding="utf-8"))
        if not profiles:
            parser.error("no timestamped IrisUploadBench profiles found in Android log")
        report = summarize_profiles(profiles, read_samples(args.samples))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
