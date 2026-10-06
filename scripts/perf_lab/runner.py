"""Run a scenario against a disposable Iris server and write its report."""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from scripts.perf_lab import report as report_mod
from scripts.perf_lab import sync_actor
from scripts.perf_lab.sampler import Sampler
from scripts.perf_lab.scenario import Scenario
from scripts.perf_lab.statistics import percentile as calculate_percentile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKER = ".iris-perf-lab"
SAMPLE_SECONDS = 0.5


def prepare_root(root: Path) -> None:
    """A fresh, marked directory: never a library someone uses."""
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"{root} is not empty; the perf lab only runs in a new, empty directory")
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("Disposable data created by scripts/perf_lab\n", encoding="utf-8")


def validate_output(root: Path, output: Path) -> None:
    """Refuse report paths that could overwrite data or the lab's own inputs."""
    if output == root:
        raise SystemExit("report output must be a separate directory, not the lab root")
    if output.exists():
        raise SystemExit(f"{output} already exists; choose a new report directory")


def prepare_output(root: Path, output: Path) -> None:
    validate_output(root, output)
    output.mkdir(parents=True)


def phase_timings(log_path: Path) -> dict[str, dict[str, float | int]]:
    """Summarize allowlisted server phase fields; never copy IDs or log payloads."""
    values: dict[str, list[float]] = {}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            event = json.loads(line)
            if event.get("event") != "sync_phase_completed":
                continue
            phase, duration = event.get("phase"), event.get("phase_ms")
            if isinstance(phase, str) and phase and isinstance(duration, (int, float)):
                if math.isfinite(duration) and duration >= 0:
                    values.setdefault(phase, []).append(float(duration))
        except (json.JSONDecodeError, AttributeError):
            continue

    return {
        name: {
            "count": len(series),
            "p50_ms": round(calculate_percentile(series, 0.50), 1),
            "p95_ms": round(calculate_percentile(series, 0.95), 1),
            "p99_ms": round(calculate_percentile(series, 0.99), 1),
        }
        for name, series in sorted(values.items())
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(root: Path, port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
            "IRIS_DATA_DIR": str(root / "lab" / "data"),
            "IRIS_SERVER_MODE": "private",
            "IRIS_SESSION_HTTPS_ONLY": "false",
            "IRIS_LOAD_MODEL": "0",
            "IRIS_LOG_FORMAT": "json",
            "IRIS_PERF_PROBE": "1",
        }
    )
    with (root / "server.log").open("wb") as log:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )


def wait_healthy(base_url: str, server: subprocess.Popen, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit("the lab server exited during startup; see server.log")
        try:
            if httpx.get(f"{base_url}/healthz", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("the lab server did not become healthy")


def _probe_reader(base_url: str, username: str, password: str):
    client = httpx.Client(timeout=5, trust_env=False)
    token = client.post(
        f"{base_url}/api/auth/devices/login",
        data={
            "username": username,
            "password": password,
            "device_name": "perf-lab-probe",
            "platform": "perf-lab",
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return lambda: client.get(f"{base_url}/api/_perf/probe", headers=headers).json()


async def _run_actors(
    scenario: Scenario,
    base_url: str,
    accounts: list[dict[str, str]],
    items_by_actor: list[list[sync_actor.Item]],
) -> tuple[dict[str, Any], float]:
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
    async with httpx.AsyncClient(timeout=600, limits=limits, trust_env=False) as client:
        start = time.monotonic()
        deadline = start + scenario.duration
        tasks: list[asyncio.Task[sync_actor.DeviceResult]] = []
        labels, task_items = [], []
        for number, (actor, items) in enumerate(zip(scenario.actors, items_by_actor, strict=True)):
            share = [items[device :: actor.devices] for device in range(actor.devices)]
            for device in range(actor.devices):
                account = accounts[device % actor.accounts]
                device_items = share[device]
                tasks.append(
                    asyncio.create_task(sync_actor.run_device(
                        client,
                        base_url,
                        username=account["username"],
                        password=account["password"],
                        device=f"perf-{number}-{device}",
                        items=device_items,
                        actor=actor,
                        clock_start=start,
                        deadline=deadline,
                    ))
                )
                labels.append(number)
                task_items.append(device_items)
        completed_tasks, pending_tasks = await asyncio.wait(tasks, timeout=scenario.duration)
        timed_out = bool(pending_tasks)
        if pending_tasks:
            # The scenario duration is a wall-clock cap for the whole run,
            # including requests already in flight. Cancel them and account
            # for every item that had started before the cap was reached.
            timed_out = True
            elapsed = time.monotonic() - start
            for task in pending_tasks:
                task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)
            for items in items_by_actor:
                for item in items:
                    if item.started and not item.finished:
                        item.finished = elapsed
                        item.state = "timeout"
        results = [
            task.result() if task in completed_tasks else sync_actor.DeviceResult(items=items)
            for task, items in zip(tasks, task_items, strict=True)
        ]
        elapsed = time.monotonic() - start
    actors: dict[str, Any] = {}
    for number, actor in enumerate(scenario.actors):
        device_results = [r for r, label in zip(results, labels, strict=True) if label == number]
        items = [item for r in device_results for item in r.items]
        summary = sync_actor.summarize(items, elapsed=elapsed)
        summary["login_errors"] = [r.login_error for r in device_results if r.login_error]
        summary["timed_out"] = timed_out
        name = f"{number}:{actor.label}"
        actors[name] = {
            "config": {
                "devices": actor.devices,
                "items": actor.items,
                "concurrency": actor.concurrency,
                "completion_lanes": actor.completion_lanes,
                "accounts": actor.accounts,
            },
            "summary": summary,
            "targets": report_mod.target_results(summary, actor.target),
            "_finishes": sorted(
                item.finished
                for item in items
                if item.state in {"ready", "duplicate", "pending_processing"}
            ),
        }
    return actors, elapsed


def run(scenario: Scenario, root: Path, output: Path) -> dict[str, Any]:
    from scripts.load_lab import accounts_path
    from scripts.load_lab import prepare as prepare_lab

    if output == root:
        raise SystemExit("report output must be separate from the lab root")
    validate_output(root, output)
    prepare_root(root)
    prepare_output(root, output)
    prepare_lab(
        root / "lab", users=scenario.accounts, records_per_user=max(1, scenario.library_items)
    )
    accounts = json.loads(accounts_path(root / "lab").read_text(encoding="utf-8"))
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = start_server(root, port)
    samples: list[dict[str, Any]] = []
    stop = threading.Event()
    try:
        wait_healthy(base_url, server)
        rng = random.Random(scenario.seed)
        print(
            f"[perf-lab] hashing synthetic media for {sum(a.items for a in scenario.actors)} items…",
            flush=True,
        )
        items_by_actor, start_index = [], 0
        for actor in scenario.actors:
            items_by_actor.append(sync_actor.make_items(actor, rng, start_index))
            start_index += actor.items
        # uvicorn runs in this one process (no workers, no reload).
        sampler = Sampler(
            server.pid,
            root,
            probe=_probe_reader(base_url, accounts[0]["username"], accounts[0]["password"]),
        )

        def sample_loop() -> None:
            while not stop.wait(SAMPLE_SECONDS):
                samples.append(sampler.sample())

        thread = threading.Thread(target=sample_loop, name="perf-lab-sampler", daemon=True)
        thread.start()
        print(f"[perf-lab] running {scenario.name} (hard cap {scenario.duration:.0f} s)…", flush=True)
        actors, elapsed = asyncio.run(_run_actors(scenario, base_url, accounts, items_by_actor))
        stop.set()
        thread.join(timeout=5)
        finishes = {name: actor.pop("_finishes") for name, actor in actors.items()}
        environment = {"data_devices": sampler.devices, "cpu_count": psutil.cpu_count()}
        phases = phase_timings(root / "server.log")
        result = report_mod.build(
            scenario.name, actors, samples, finishes, elapsed, environment, phases
        )
    finally:
        stop.set()
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(report_mod.dumps(result), encoding="utf-8")
    (output / "report.md").write_text(report_mod.markdown(result), encoding="utf-8")
    return result
