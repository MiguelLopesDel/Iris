"""Turn actor results and resource samples into a report.

The report states facts and correlations, not causes: a "possible
bottleneck" is a resource that stayed saturated for a while, with what the
actors did inside that interval compared with the rest of the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from statistics import mean
from typing import Any

from scripts.perf_lab.statistics import percentile

# Saturation rules: (name, test on one sample, what it means). Thresholds are
# deliberately simple; the report shows the numbers behind each flag.
MIN_WINDOW_SAMPLES = 2


@dataclass(frozen=True)
class Rule:
    name: str
    description: str

    def holds(self, sample: dict[str, Any]) -> bool:
        return _RULE_TESTS[self.name](sample)


def _busiest_disk(sample: dict[str, Any]) -> dict[str, float]:
    disks = sample.get("disk") or {}
    return max(disks.values(), key=lambda d: d["util_pct"], default={})


def _io_wait_share(sample: dict[str, Any]) -> float:
    threads = sample.get("server_threads") or {}
    active = threads.get("R", 0) + threads.get("D", 0)
    return threads.get("D", 0) / active if active else 0.0


_RULE_TESTS = {
    "disk": lambda s: _busiest_disk(s).get("util_pct", 0) >= 90,
    "one_core": lambda s: s.get("cpu_max_core", 0) >= 90 and s.get("cpu_total", 0) < 60,
    "cpu": lambda s: s.get("cpu_total", 0) >= 90,
    # The scheduler spreads one busy process over the cores, so no single
    # host core looks saturated; the process's own share shows it.
    "server_one_core": lambda s: 85 <= s.get("server_cpu", 0) <= 130 and s.get("cpu_total", 0) < 60,
    "event_loop": lambda s: (s.get("probe") or {}).get("loop_lag_ms_max", 0) >= 50,
    "thread_pool": lambda s: (s.get("probe") or {}).get("threads_waiting", 0) > 0,
    "io_wait": lambda s: _io_wait_share(s) >= 0.5
    and (s.get("server_threads") or {}).get("D", 0) >= 2,
}
RULES = (
    Rule("disk", "disk ≥ 90% busy"),
    Rule("one_core", "one CPU core ≥ 90% while the total stays < 60% (a serial step)"),
    Rule("cpu", "all CPU ≥ 90%"),
    Rule(
        "server_one_core",
        "the server process uses about one core while the machine has spare CPU "
        "(a serial step: the Python GIL or one busy thread)",
    ),
    Rule("event_loop", "server event loop ≥ 50 ms late (possible synchronous-work pressure)"),
    Rule("thread_pool", "requests waiting for a free server thread"),
    Rule("io_wait", "most active server threads waiting on I/O"),
)


def windows(samples: list[dict[str, Any]], rule: Rule) -> list[tuple[float, float]]:
    """Intervals where the rule held for at least MIN_WINDOW_SAMPLES samples in a row."""
    found, run = [], []
    for sample in samples + [None]:
        if sample is not None and rule.holds(sample):
            run.append(sample["t"])
            continue
        if len(run) >= MIN_WINDOW_SAMPLES:
            found.append((run[0], run[-1]))
        run = []
    return found


def _rate_inside(
    finish_times: list[float], start: float, end: float, total: float
) -> tuple[float, float]:
    span = max(end - start, 1e-6)
    inside = sum(1 for t in finish_times if start <= t <= end)
    outside_span = max(total - span, 1e-6)
    return inside / span, (len(finish_times) - inside) / outside_span


def _peak_disk_metric(samples: list[dict[str, Any]], key: str) -> list[float]:
    """Use the maximum device rate; summing layered devices double-counts I/O."""
    result = []
    for sample in samples:
        values = [disk[key] for disk in (sample.get("disk") or {}).values() if key in disk]
        if values:
            result.append(max(values))
    return result


def resource_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {}

    def series(get) -> list[float]:
        return [value for value in (get(s) for s in samples) if value is not None]

    def stats(values: list[float]) -> dict[str, float]:
        if not values:
            return {}
        return {
            "mean": round(mean(values), 1),
            "p95": round(percentile(values, 0.95), 1),
            "max": round(max(values), 1),
        }

    probe = [s["probe"] for s in samples if "probe" in s]
    core_count = max((len(s.get("cpu_cores", [])) for s in samples), default=0)
    return {
        "cpu_total_pct": stats(series(lambda s: s.get("cpu_total"))),
        "cpu_busiest_core_pct": stats(series(lambda s: s.get("cpu_max_core"))),
        "cpu_per_core_pct": [
            stats([s["cpu_cores"][index] for s in samples if len(s.get("cpu_cores", [])) > index])
            for index in range(core_count)
        ],
        "server_cpu_pct": stats(series(lambda s: s.get("server_cpu"))),
        "server_threads_running": stats(series(lambda s: s["server_threads"]["R"])),
        "server_threads_waiting_io": stats(series(lambda s: s["server_threads"]["D"])),
        "disk_util_pct": stats(series(lambda s: _busiest_disk(s).get("util_pct"))),
        "disk_queue": stats(series(lambda s: _busiest_disk(s).get("queue"))),
        "disk_read_mb_s": stats(_peak_disk_metric(samples, "read_mb_s")),
        "disk_write_mb_s": stats(_peak_disk_metric(samples, "write_mb_s")),
        "disk_flushes_s": stats(_peak_disk_metric(samples, "flushes_s")),
        "loop_lag_ms": stats([p["loop_lag_ms_max"] for p in probe]),
        "threads_busy": stats([p["threads_busy"] for p in probe]),
        "threads_waiting": stats([p["threads_waiting"] for p in probe]),
        # Identity reads kept on the event loop: the tail must stay small.
        "auth_ms_p99": stats([p["auth_ms_p99"] for p in probe if "auth_ms_p99" in p]),
        "auth_ms_max": stats([p["auth_ms_max"] for p in probe if "auth_ms_max" in p]),
        "process_rss_mb": stats(
            series(
                lambda s: (s.get("resource_sample", {}).get("process") or {}).get("rss_bytes", 0)
                / 1e6
            )
        ),
    }


def target_results(summary: dict[str, Any], target: dict[str, float]) -> dict[str, dict[str, Any]]:
    error_rate = summary.get("error_rate")
    # A device that cannot authenticate cannot process its assigned items.
    # Keep this visible in the error target even though those items never
    # reached the per-item request failure accounting in sync_actor.summarize.
    if summary.get("login_errors"):
        error_rate = max(error_rate or 0, 1.0)
    actual = {
        "items_per_s": summary.get("items_per_s"),
        "mb_per_s": summary.get("mb_per_s"),
        "total_p95_ms": (summary.get("total") or {}).get("p95_ms"),
        "error_rate": error_rate,
    }
    lower_is_better = {"total_p95_ms", "error_rate"}
    results = {}
    for key, goal in target.items():
        value = actual.get(key)
        met = value is not None and (value <= goal if key in lower_is_better else value >= goal)
        results[key] = {"goal": goal, "actual": value, "met": met}
    return results


def candidates(
    samples: list[dict[str, Any]], finishes: dict[str, list[float]], total: float
) -> list[dict[str, Any]]:
    flagged = []
    for rule in RULES:
        for start, end in windows(samples, rule):
            evidence = {}
            for actor, times in finishes.items():
                inside, outside = _rate_inside(times, start, end, total)
                evidence[actor] = {
                    "items_per_s_inside": round(inside, 1),
                    "items_per_s_elsewhere": round(outside, 1),
                }
            flagged.append(
                {
                    "resource": rule.name,
                    "meaning": rule.description,
                    "from_s": start,
                    "to_s": end,
                    "actors": evidence,
                }
            )
    return flagged


def build(
    scenario_name: str,
    actors: dict[str, dict[str, Any]],
    samples: list[dict[str, Any]],
    finishes: dict[str, list[float]],
    elapsed: float,
    environment: dict[str, Any],
    phases: dict[str, dict[str, float | int]] | None = None,
) -> dict[str, Any]:
    return {
        "scenario": scenario_name,
        "environment": environment,
        "elapsed_s": round(elapsed, 2),
        "actors": actors,
        "resources": resource_summary(samples),
        "server_phases": phases or {},
        "possible_bottlenecks": candidates(samples, finishes, elapsed),
        "samples": samples,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# Perf lab: {report['scenario']}", ""]
    env = report["environment"]
    lines += [
        f"- Data on: `{env.get('data_devices')}` · CPU cores: {env.get('cpu_count')} · elapsed {report['elapsed_s']} s",
        "",
    ]
    lines += [
        "## Actors",
        "",
        "| actor | items/s | MB/s | done | failed | timed out | total p50 / p95 / p99 (ms) | reserve p95 | send p95 | complete p95 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, actor in report["actors"].items():
        s = actor["summary"]
        total, reserve, send, complete = (
            s.get(k) or {} for k in ("total", "reserve", "send", "complete")
        )
        lines.append(
            f"| {name} | {s['items_per_s']} | {s['mb_per_s']} | {s['items_done']} | {s['items_failed']} | {s['items_timed_out']} "
            f"| {total.get('p50_ms', '-')} / {total.get('p95_ms', '-')} / {total.get('p99_ms', '-')} "
            f"| {reserve.get('p95_ms', '-')} | {send.get('p95_ms', '-')} | {complete.get('p95_ms', '-')} |"
        )
    targets = [
        (name, key, t)
        for name, actor in report["actors"].items()
        for key, t in actor["targets"].items()
    ]
    if targets:
        lines += [
            "",
            "### Targets",
            "",
            "| actor | target | goal | actual | |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {name} | {key} | {t['goal']} | {t['actual']} | {'met' if t['met'] else '**missed**'} |"
            for name, key, t in targets
        ]
    lines += ["", "## Server resources", "", "| measure | mean | p95 | max |", "|---|---|---|---|"]
    for key, value in report["resources"].items():
        if key == "cpu_per_core_pct":
            for index, core in enumerate(value):
                if core:
                    lines.append(
                        f"| cpu_core_{index}_pct | {core['mean']} | {core['p95']} | {core['max']} |"
                    )
            continue
        if value:
            lines.append(f"| {key} | {value['mean']} | {value['p95']} | {value['max']} |")
    lines += [
        "",
        "## Server phase timings",
        "",
        "| phase | count | p50 ms | p95 ms | p99 ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for phase, values in report.get("server_phases", {}).items():
        lines.append(
            f"| {phase} | {values['count']} | {values['p50_ms']} | {values['p95_ms']} | {values['p99_ms']} |"
        )
    lines += ["", "## Possible bottlenecks", ""]
    flagged = report["possible_bottlenecks"]
    if not flagged:
        lines.append("None of the saturation rules held for two samples in a row.")
    for item in flagged:
        effects = "; ".join(
            f"{actor}: {e['items_per_s_inside']} items/s inside vs {e['items_per_s_elsewhere']} elsewhere"
            for actor, e in item["actors"].items()
        )
        lines.append(
            f"- **{item['resource']}** ({item['meaning']}) from {item['from_s']} s to {item['to_s']} s. {effects}."
        )
    lines += [
        "",
        "Correlation, not proof: a flag shows what was saturated and what changed at the same time.",
        "",
    ]
    return "\n".join(lines)


def dumps(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)
