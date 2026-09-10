#!/usr/bin/env python3
"""Measure Iris HTTP capacity without mutating a library.

Examples:
  python scripts/load_test.py --url https://iris.tailnet.ts.net --expect-private \
    --username alice --password 'senha forte' --scenario browse --concurrency 4
  python scripts/load_test.py --url https://iris.tailnet.ts.net --expect-private \
    --username alice --password 'senha forte' --scenario search --concurrency 2
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    elapsed_ms: float
    status_code: int
    error: str = ""


class IrisClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

    def request(self, path: str, body: bytes | None = None, content_type: str = "") -> int:
        request = urllib.request.Request(
            self.base_url + path, data=body, method="POST" if body is not None else "GET"
        )
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as exc:
            exc.read()
            return exc.code

    def login(self, username: str, password: str) -> None:
        form = urllib.parse.urlencode({"username": username, "password": password}).encode()
        if self.request("/api/auth/login", form, "application/x-www-form-urlencoded") != 200:
            raise RuntimeError("login failed")


def load_accounts(path: Path) -> list[tuple[str, str]]:
    """Load disposable lab credentials without ever printing them."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        accounts = [(str(item["username"]), str(item["password"])) for item in raw]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"invalid accounts file: {path}") from exc
    if not accounts or any(not username or not password for username, password in accounts):
        raise ValueError("accounts file has no usable credentials")
    return accounts


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def make_path(scenario: str, sequence: int, query: str) -> str:
    if scenario == "browse":
        return f"/api/records?page={(sequence % 3) + 1}&per_page=24"
    encoded = urllib.parse.quote(query)
    if scenario == "search":
        return f"/api/search?q={encoded}&top_k=50&translate=false"
    if sequence % 4 == 0:
        return f"/api/search?q={encoded}&top_k=50&translate=false"
    return f"/api/records?page={(sequence % 3) + 1}&per_page=24"


def summarize(samples: list[Sample], elapsed_sec: float, scenario: str, concurrency: int) -> dict:
    latencies = [sample.elapsed_ms for sample in samples if not sample.error]
    success = [sample for sample in samples if sample.status_code == 200 and not sample.error]
    errors: dict[str, int] = {}
    for sample in samples:
        if sample.status_code == 200 and not sample.error:
            continue
        key = sample.error or f"HTTP {sample.status_code}"
        errors[key] = errors.get(key, 0) + 1
    return {
        "scenario": scenario,
        "concurrency": concurrency,
        "requests": len(samples),
        "successes": len(success),
        "errors": errors,
        "elapsed_sec": round(elapsed_sec, 3),
        "requests_per_sec": round(len(samples) / elapsed_sec, 2) if elapsed_sec else 0.0,
        "latency_ms": {
            "min": round(min(latencies), 1) if latencies else 0.0,
            "p50": round(percentile(latencies, 0.50), 1),
            "p95": round(percentile(latencies, 0.95), 1),
            "p99": round(percentile(latencies, 0.99), 1),
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8501")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--accounts-file", type=Path, help="load_lab disposable-account JSON")
    parser.add_argument("--expect-private", action="store_true")
    parser.add_argument("--scenario", choices=("browse", "search", "mixed"), default="browse")
    parser.add_argument("--query", default="foto de família")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--output", type=Path, help="grava o relatório JSON")
    args = parser.parse_args()
    if bool(args.username) != bool(args.password):
        parser.error("--username and --password must be used together")
    if args.accounts_file and args.username:
        parser.error("use either --accounts-file or --username/--password, not both")
    try:
        accounts = load_accounts(args.accounts_file) if args.accounts_file else []
    except ValueError as exc:
        parser.error(str(exc))
    if args.expect_private and not (args.username or accounts):
        parser.error("--expect-private requires credentials")
    if args.concurrency < 1 or args.requests < 1 or args.warmup < 0:
        parser.error("concurrency and requests must be positive; warmup cannot be negative")

    anonymous = IrisClient(args.url, args.timeout)
    if anonymous.request("/healthz") != 200:
        raise RuntimeError("healthz failed")
    if args.expect_private and anonymous.request("/api/info") != 401:
        raise RuntimeError("private server accepted anonymous /api/info")

    sequence = 0
    sequence_lock = threading.Lock()
    samples: list[Sample] = []
    samples_lock = threading.Lock()

    def next_sequence() -> int:
        nonlocal sequence
        with sequence_lock:
            value = sequence
            sequence += 1
            return value

    def worker(total: int, record: bool, worker_index: int) -> None:
        client = IrisClient(args.url, args.timeout)
        if accounts:
            username, password = accounts[worker_index % len(accounts)]
            client.login(username, password)
        elif args.username:
            client.login(args.username, args.password)
        local: list[Sample] = []
        for _ in range(total):
            started = time.perf_counter()
            try:
                status = client.request(make_path(args.scenario, next_sequence(), args.query))
                local.append(Sample((time.perf_counter() - started) * 1000, status))
            except Exception as exc:
                local.append(Sample((time.perf_counter() - started) * 1000, 0, type(exc).__name__))
        if record:
            with samples_lock:
                samples.extend(local)

    def distribute(total: int, record: bool) -> None:
        counts = [total // args.concurrency] * args.concurrency
        for index in range(total % args.concurrency):
            counts[index] += 1
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(worker, count, record, index) for index, count in enumerate(counts)]
            for future in futures:
                future.result()

    if args.warmup:
        distribute(args.warmup, False)
    started = time.perf_counter()
    distribute(args.requests, True)
    report = summarize(samples, time.perf_counter() - started, args.scenario, args.concurrency)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
