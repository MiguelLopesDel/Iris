# Iris sync performance lab

This lab measures the first workload in the performance plan: devices backing
up synthetic media to a disposable Iris installation. It reports both items/s
and decimal MB/s; those are separate goals, particularly for libraries with
many small files. The report includes reserve/send/complete/total latency
percentiles, server phase timings, resource samples and evidence-based
correlations. A possible bottleneck is not a proof of causation.

## Run locally or against another disk

Use Python 3.13 with the Iris development dependencies installed. The lab
starts an Iris server bound only to `127.0.0.1`, creates its own disposable
account/catalog, and refuses a non-empty data root or report directory. Never
point it at a real Iris data or media directory.

```sh
python3.13 -m scripts.perf_lab check scripts/perf_lab/scenarios/smoke.yaml
python3.13 -m scripts.perf_lab run scripts/perf_lab/scenarios/smoke.yaml \
  --root /tmp/iris-perf-smoke --output /tmp/iris-perf-smoke-report
```

For a disposable local host run, put both the empty lab root and report output
on the target disk. The one-command wrapper accepts the scenario, those two
explicit paths, and optionally a Python executable:

```sh
scripts/perf_lab/run.sh scripts/perf_lab/scenarios/sync-small-files.yaml \
  /mnt/benchmark/iris-perf-data /mnt/benchmark/iris-perf-report python3.13
```

Use a disposable test machine or a dedicated, empty test volume. The executor
does not contact a remote server and does not manage or stop other Iris
services. `--root` contains the disposable server database and received files;
the report directory must be separate. Preserve reports between runs by
choosing a new, not-yet-created output directory. The lab reserves it before
starting to avoid concurrent runs overwriting the same report.

For a containerized run, first build a CPU image from the same checkout so the
container has this branch's code and locked dependencies:

```sh
docker build --build-arg IRIS_PROFILE=cpu -t iris-perf-lab:local .
mkdir /mnt/benchmark/iris-perf-run
scripts/perf_lab/run-container.sh scripts/perf_lab/scenarios/sync-small-files.yaml \
  /mnt/benchmark/iris-perf-run
```

The container wrapper requires an existing empty workspace on the target disk.
It mounts the checkout read-only and the workspace read-write, has no network
access, and runs the disposable server and clients inside the same container.
The workspace gets `data/` and `report/`; use a new empty workspace per run.

## Scenarios

- `smoke.yaml`: a tiny end-to-end check suitable for local tests and CI.
- `sync-small-files.yaml`: a sustained small-file backup run, reporting
  completed items/s as well as bytes/s.
- `sync-mixed-library.yaml`: a weighted size distribution for comparing a
  mixed media workload.

Scenario targets are acceptance thresholds for the report, not pacing controls.
The first actor implementation uses the authenticated sync batch-reservation,
resumable chunk-upload and batch-completion API. Model loading and optional AI
processing are disabled. To guard against accidental huge synthetic workloads,
scenario parsing caps an individual item at 32 GiB.

## What is measured

Client-side timings include reservation, transfer and completion, plus total
item latency. The server emits existing aggregate `sync_phase_completed`
events; the report retains only phase names and latency aggregates, not request
IDs or account IDs. The optional `/api/_perf/probe` route and event-loop probe
exist only when `IRIS_PERF_PROBE=1`.

Resource samples include host/per-core CPU, server process CPU/RSS and I/O,
cgroup counters when available, server thread states, block-device utilization,
queue, read/write throughput and flush rates, plus event-loop/thread-pool probe
data. Device rates are not added across stacked partition/device-mapper layers;
the report uses the highest per-device rate to avoid double-counting. Flushes
are read from the whole disk when the data filesystem is mounted on a partition.

This first version does not simulate network shaping and does not yet run mixed
actors (video playback, gallery scrolling, search or downloads). It also does
not install a CI performance gate; those are follow-up increments after this
measurement path is stable and calibrated on SSD and HDD runs.

## Middleware variants

`--variant NAME` runs the same scenario against a modified copy of the app's
middleware stack, built only inside the disposable lab process
(`scripts/perf_lab/variants.py`; `server.py` is unchanged). It answers "where
does the event loop's time go?" before any security code is touched:

- `normal`: the real stack;
- `no_gzip`: without response compression;
- `trivial_auth_http` / `trivial_auth_asgi`: authentication replaced by a cheap
  check (token signature, identity cached in memory), as a `BaseHTTPMiddleware`
  or as pure ASGI; the difference is the wrapper's own cost;
- `trivial_auth_and_log_asgi`: request logging as pure ASGI as well;
- `real_auth_inline` / `real_auth_join_inline`: the real bearer checks run on
  the event loop, with two reads or with one JOIN on a reused connection. They
  write the identity read's p50/p95/p99/p99.9/max to `ROOT/auth-stats.json`.

The trivial variants skip checks the real one makes (revocation, versions) and
must never leave the lab. `run-container.sh` forwards options after the image,
for example `... IMAGE --variant no_gzip`.

Compare variants by repeating each run, interleaved, on a quiet machine: on a
busy laptop the same configuration varied by ±40% between runs.
