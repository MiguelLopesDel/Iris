import json
import os

import psutil

from scripts.sample_sync_resources import _cgroup_io_bytes, sample_resources
from scripts.summarize_sync_resources import parse_profiles, summarize_profiles


def test_cgroup_io_bytes_sums_devices(tmp_path):
    stats = tmp_path / "io.stat"
    stats.write_text("8:0 rbytes=100 wbytes=40 rios=1\n8:16 rbytes=25 wbytes=60 wios=2\n")

    assert _cgroup_io_bytes(stats) == {"read_bytes": 125, "write_bytes": 100}


def test_resource_sample_is_json_safe_and_scoped_to_server_process():
    process = psutil.Process(os.getpid())
    process.cpu_percent(interval=None)

    sample = sample_resources(process, host_cpu_percent=22.5, timestamp=100.0)

    assert sample["timestamp"] == 100.0
    assert sample["process"]["pid"] == os.getpid()
    assert sample["process"]["rss_bytes"] > 0
    assert sample["host"]["cpu_percent"] == 22.5
    assert set(sample["cgroup"]) >= {"cpu_usage_usec", "memory_current_bytes", "io_write_bytes"}
    json.dumps(sample)


def test_profile_resources_are_joined_by_transfer_window():
    log = (
        "I IrisUploadBench: IRIS_UPLOAD_REAL_SERVER workers=4 photos=12 videos=4 "
        "bytes=163577856 enqueue_hash_ms=500 transfer_start_epoch_ms=1000 "
        "transfer_end_epoch_ms=3000 transfer_ms=2000 MBps=11.50 active_MBps=11.82\n"
        "I IrisUploadBench: IRIS_UPLOAD_REAL_SERVER_SUMMARY 4-worker=11.50MBps"
    )
    profiles = parse_profiles(log)
    assert len(profiles) == 1

    samples = []
    for timestamp, cpu, rss, written, cgroup_cpu, throttled in [
        (1.0, 10, 2_097_152, 1_000_000, 1_000_000, 100),
        (2.0, 30, 3_145_728, 1_500_000, 1_500_000, 150),
        (3.0, 50, 4_194_304, 2_000_000, 2_000_000, 190),
    ]:
        samples.append({
            "timestamp": timestamp,
            "process": {"cpu_percent": cpu, "rss_bytes": rss, "read_bytes": 0, "write_bytes": written},
            "host": {"cpu_percent": cpu + 10, "memory_percent": 60, "disk_read_bytes": 0, "disk_write_bytes": written},
            "cgroup": {
                "cpu_usage_usec": cgroup_cpu,
                "cpu_throttled_usec": throttled,
                "cpu_nr_throttled": int(throttled / 10),
                "memory_current_bytes": 8_388_608,
                "io_read_bytes": 0,
                "io_write_bytes": written,
            },
        })

    report = summarize_profiles(profiles, samples)
    profile = report["profiles"][0]

    assert profile["sample_count"] == 3
    assert profile["resources"]["server_process_cpu_percent_mean"] == 30.0
    assert profile["resources"]["server_process_rss_mib_peak"] == 4.0
    assert profile["resources"]["cgroup_cpu_cores_mean"] == 0.5
    assert profile["resources"]["cgroup_cpu_throttled_usec_delta"] == 90
    assert profile["resources"]["server_process_write_mib_per_second"] == 0.5
