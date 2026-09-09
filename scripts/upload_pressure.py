#!/usr/bin/env python3
"""Perform rate-limited, real multipart uploads against a disposable Iris lab.

The payload is a valid tiny JPEG followed by padding. It intentionally measures
network, request parsing, temporary storage and import admission; it does not claim
to benchmark image understanding. Never target a personal/production library.
"""
from __future__ import annotations

import argparse
import http.client
import http.cookiejar
import io
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.load_test import load_accounts, percentile  # noqa: E402


def tiny_jpeg() -> bytes:
    """Create a standards-compliant image header for the synthetic stream."""
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (1, 1), color=(32, 80, 160)).save(output, "JPEG")
    return output.getvalue()


_JPEG = tiny_jpeg()


def login(base_url: str, username: str, password: str, timeout: float) -> str:
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    request = urllib.request.Request(base_url.rstrip("/") + "/api/auth/login", data=body)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("login failed")
        response.read()
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookies)


def multipart_parts(size_bytes: int, filename: str) -> tuple[str, bytes, bytes]:
    if size_bytes < len(_JPEG):
        raise ValueError("upload must be at least 1 KiB")
    boundary = "----iris-load-" + uuid.uuid4().hex
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode()
    suffix = (
        "\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="low_resource"\r\n\r\ntrue\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    return boundary, prefix, suffix


def upload(
    base_url: str, cookie: str, *, size_bytes: int, rate_bytes_per_sec: int, timeout: float, label: str
) -> tuple[int, float, str]:
    parsed = urllib.parse.urlparse(base_url)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.netloc, timeout=timeout)
    boundary, prefix, suffix = multipart_parts(size_bytes, f"pressure-{label}.jpg")
    content_length = len(prefix) + size_bytes + len(suffix)
    target = parsed.path.rstrip("/") + "/api/import"
    started = time.perf_counter()
    try:
        connection.putrequest("POST", target)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(content_length))
        connection.putheader("Cookie", cookie)
        connection.endheaders()
        connection.send(prefix)
        remaining = size_bytes
        chunk_size = min(256 * 1024, max(1024, rate_bytes_per_sec // 10))
        next_deadline = time.perf_counter()
        first = _JPEG[: min(len(_JPEG), remaining)]
        connection.send(first)
        remaining -= len(first)
        while remaining:
            chunk = b"\0" * min(chunk_size, remaining)
            connection.send(chunk)
            remaining -= len(chunk)
            next_deadline += len(chunk) / rate_bytes_per_sec
            delay = next_deadline - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        connection.send(suffix)
        response = connection.getresponse()
        response.read()
        return response.status, (time.perf_counter() - started) * 1000, ""
    except Exception as exc:
        return 0, (time.perf_counter() - started) * 1000, type(exc).__name__
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8501")
    parser.add_argument("--accounts-file", type=Path, required=True)
    parser.add_argument("--uploaders", type=int, default=10)
    parser.add_argument("--upload-mib", type=int, default=8)
    parser.add_argument("--upload-mbps", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-total-gib", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        accounts = load_accounts(args.accounts_file)
    except ValueError as exc:
        parser.error(str(exc))
    if args.uploaders < 1 or args.upload_mib < 1 or args.upload_mbps <= 0:
        parser.error("uploaders, upload-mib, and upload-mbps must be positive")
    if args.uploaders > len(accounts):
        parser.error("uploaders exceeds the number of lab accounts")
    size_bytes = args.upload_mib * 1024 * 1024
    total_gib = args.uploaders * size_bytes / 1024**3
    if total_gib > args.max_total_gib:
        parser.error(
            f"scenario would write {total_gib:.2f} GiB; explicitly raise --max-total-gib to allow it"
        )

    results: list[tuple[int, float, str]] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        username, password = accounts[index]
        try:
            cookie = login(args.url, username, password, args.timeout)
            result = upload(
                args.url, cookie, size_bytes=size_bytes,
                rate_bytes_per_sec=max(1, int(args.upload_mbps * 1024 * 1024 / 8)),
                timeout=args.timeout, label=username,
            )
        except Exception as exc:
            result = (0, 0.0, type(exc).__name__)
        with lock:
            results.append(result)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.uploaders) as pool:
        list(pool.map(worker, range(args.uploaders)))
    elapsed = time.perf_counter() - started
    latencies = [elapsed_ms for _, elapsed_ms, error in results if not error]
    errors: dict[str, int] = {}
    for status, _, error in results:
        if status == 200 and not error:
            continue
        key = error or f"HTTP {status}"
        errors[key] = errors.get(key, 0) + 1
    report = {
        "uploaders": args.uploaders,
        "upload_mib_each": args.upload_mib,
        "upload_mbps_each": args.upload_mbps,
        "total_gib_written": round(total_gib, 3),
        "elapsed_sec": round(elapsed, 3),
        "successes": sum(1 for status, _, error in results if status == 200 and not error),
        "errors": errors,
        "latency_ms": {"p50": round(percentile(latencies, 0.5), 1), "p95": round(percentile(latencies, 0.95), 1)},
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
