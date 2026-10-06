import json
from pathlib import Path

import pytest

from scripts.perf_lab import report, scenario
from scripts.perf_lab.runner import phase_timings, prepare_output, prepare_root, run
from scripts.perf_lab.sampler import _parent_device, disk_rates, parse_diskstats
from scripts.perf_lab.statistics import percentile
from scripts.perf_lab.sync_actor import Item, summarize

SCENARIOS = Path(__file__).parents[1] / "scripts" / "perf_lab" / "scenarios"


@pytest.mark.parametrize(
    "filename", ["smoke.yaml", "sync-small-files.yaml", "sync-mixed-library.yaml"]
)
def test_checked_in_scenarios_parse(filename):
    loaded = scenario.load(SCENARIOS / filename)

    assert loaded.name
    assert loaded.duration > 0
    assert loaded.actors


@pytest.mark.parametrize(
    "change",
    [
        {"duration": 0},
        {"duration": "NaNs"},
        {"duration": True},
        {"seed": 1.5},
        {"library_items": True},
        {
            "actors": [
                {"kind": "syncing", "items": 1, "sizes": {"999999999999999999999999999GB": "100%"}}
            ]
        },
        {"actors": [{"kind": "syncing", "items": 1, "sizes": {"1KB": "NaN"}}]},
        {"actors": [{"kind": "syncing", "items": 1, "sizes": {"1KB": "1%"}, "target": []}]},
    ],
)
def test_invalid_scenario_values_are_rejected(change):
    raw = {
        "name": "invalid",
        "duration": 2,
        "actors": [{"kind": "syncing", "items": 1, "sizes": {"1KB": "100%"}}],
    }
    raw.update(change)

    with pytest.raises(scenario.ScenarioError):
        scenario.parse(raw)


def test_diskstats_parses_flush_counter_and_rates():
    before = parse_diskstats("8 0 sda 10 0 20 0 30 0 40 0 0 50 60 0 0 0 0 80 0")["sda"]
    after = parse_diskstats("8 0 sda 12 0 24 0 34 0 48 0 0 70 90 0 0 0 0 86 0")["sda"]

    assert before.flushes == 80
    rates = disk_rates(before, after, 2)
    assert rates["flushes_s"] == 3
    assert rates["write_mb_s"] == pytest.approx(0.002048)


def test_partition_resolves_parent_disk_for_flush_measurement(tmp_path):
    real_parent = tmp_path / "nvme0n1"
    partition = real_parent / "nvme0n1p3"
    partition.mkdir(parents=True)
    (partition / "partition").write_text("3")
    class_block = tmp_path / "class-block"
    class_block.mkdir()
    (class_block / "nvme0n1p3").symlink_to(partition)

    assert _parent_device("nvme0n1p3", class_block) == "nvme0n1"


def test_report_flags_sustained_event_loop_lag_and_correlated_rate():
    samples = [
        {"t": 0.5, "probe": {"loop_lag_ms_max": 60}},
        {"t": 1.0, "probe": {"loop_lag_ms_max": 70}},
        {"t": 1.5, "probe": {"loop_lag_ms_max": 0}},
    ]

    flags = report.candidates(samples, {"sync": [0.6]}, total=2)

    assert flags[0]["resource"] == "event_loop"
    assert flags[0]["actors"]["sync"]["items_per_s_inside"] == 2.0


def test_phase_timings_keep_only_allowlisted_aggregate_data(tmp_path):
    log = tmp_path / "server.log"
    log.write_text(
        "not-json\n"
        + json.dumps(
            {
                "event": "sync_phase_completed",
                "phase": "chunk_durable",
                "phase_ms": 10,
                "user_id": 999,
                "request_id": "private-id",
            }
        )
        + "\n"
        + json.dumps({"event": "sync_phase_completed", "phase": "chunk_durable", "phase_ms": 30})
        + "\n",
        encoding="utf-8",
    )

    result = phase_timings(log)

    assert result == {"chunk_durable": {"count": 2, "p50_ms": 20, "p95_ms": 29, "p99_ms": 29.8}}
    assert "private-id" not in json.dumps(result)


def test_lab_refuses_nonempty_root_and_report_output(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep.txt").write_text("do not overwrite")
    with pytest.raises(SystemExit):
        prepare_root(root)

    output = tmp_path / "reports"
    output.mkdir()
    (output / "previous.json").write_text("preserve")
    with pytest.raises(SystemExit):
        prepare_output(root, output)

    empty_output = tmp_path / "empty-reports"
    empty_output.mkdir()
    with pytest.raises(SystemExit):
        prepare_output(root, empty_output)

    assert (root / "keep.txt").read_text() == "do not overwrite"
    assert (output / "previous.json").read_text() == "preserve"


def test_sync_actor_summary_counts_failures_and_omits_unfinished_items():
    items = [
        Item(
            index=1,
            size=1000,
            seed=1,
            started=1,
            reserved=1.1,
            sent=1.2,
            finished=1.3,
            state="ready",
        ),
        Item(index=2, size=1000, seed=2, started=1, finished=1.1, state="error", error="offline"),
        Item(index=3, size=1000, seed=3),
    ]

    summary = summarize(items, elapsed=2)

    assert summary["items_done"] == 1
    assert summary["items_failed"] == 1
    assert summary["items_not_started"] == 1
    assert summary["error_rate"] == 0.5


def test_percentile_interpolates_between_samples():
    assert percentile([10, 30], 0.5) == 20


def test_perf_lab_smoke_end_to_end(tmp_path):
    pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    pytest.importorskip("PIL")
    root, output = tmp_path / "lab", tmp_path / "report"
    case = scenario.parse(
        {
            "name": "test-smoke",
            "library_items": 1,
            "duration": "30s",
            "actors": [
                {
                    "kind": "syncing",
                    "devices": 1,
                    "items": 2,
                    "sizes": {"2KB": "100%"},
                    "concurrency": 2,
                }
            ],
        }
    )

    result = run(case, root, output)

    assert result["actors"]["0:syncing×1"]["summary"]["items_failed"] == 0
    assert result["actors"]["0:syncing×1"]["summary"]["items_done"] == 2
    assert (output / "report.md").is_file()
    assert (output / "report.json").is_file()


def test_a_server_held_to_one_core_is_flagged_although_no_host_core_is_full():
    # Measured on the lab: the server process at ~105% (one core) while the
    # busiest host core stayed under 60%, because the scheduler moves it around.
    from scripts.perf_lab.report import RULES

    rule = next(rule for rule in RULES if rule.name == "server_one_core")
    serial = {"server_cpu": 105.0, "cpu_total": 16.0, "cpu_max_core": 47.0}
    assert rule.holds(serial)
    assert not rule.holds({**serial, "cpu_total": 95.0})  # the whole machine is busy instead
    assert not rule.holds({**serial, "server_cpu": 40.0})
    one_core = next(rule for rule in RULES if rule.name == "one_core")
    assert not one_core.holds(serial)  # the host-core rule alone missed it
