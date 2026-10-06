#!/usr/bin/env python3
"""Benchmark the resumable device-sync API against a disposable local Iris lab.

The script refuses non-loopback URLs and requires the marker created by
``scripts/load_lab.py``. Payloads are synthetic valid JPEGs with deterministic
padding; no production library or user media is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.load_lab import MARKER, accounts_path, lab_root  # noqa: E402


def _is_loopback_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _make_fixture(path: Path, size: int, color_seed: int) -> str:
    image_path = path.with_suffix(".jpg")
    Image.new("RGB", (64, 64), (color_seed % 255, (color_seed * 7) % 255, 160)).save(
        image_path, "JPEG", quality=85
    )
    image_size = image_path.stat().st_size
    if image_size > size:
        raise ValueError("fixture size is too small for a JPEG")
    with image_path.open("ab") as output:
        remaining = size - image_size
        pattern = hashlib.sha256(str(color_seed).encode()).digest()
        block = (pattern * (64 * 1024 // len(pattern) + 1))[: min(64 * 1024, remaining)]
        while remaining:
            chunk = block[: min(len(block), remaining)]
            output.write(chunk)
            remaining -= len(chunk)
    digest = hashlib.sha256()
    with image_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _upload_one(client: httpx.Client, base_url: str, headers: dict[str, str], item: dict) -> float:
    started = time.perf_counter()
    with item["path"].open("rb") as payload:
        response = client.put(
            f"{base_url}/api/sync/uploads/{item['upload_id']}",
            params={"offset": 0}, headers=headers, content=payload,
        )
    response.raise_for_status()
    if response.json().get("offset") != item["size"]:
        raise RuntimeError("server acknowledged an unexpected upload offset")
    return time.perf_counter() - started


# "pending_processing" is a durable acceptance whose catalog registration was
# taken over by the background processor; the item becomes ready on its own.
_ACCEPTED_STATES = {"ready", "duplicate", "pending_processing"}


def _complete_batch(client: httpx.Client, base_url: str, headers: dict[str, str], batch: list[dict]) -> int:
    response = client.post(
        f"{base_url}/api/sync/uploads/complete-batch",
        headers=headers,
        json={"uploads": [{"upload_id": item["upload_id"]} for item in batch]},
    )
    response.raise_for_status()
    results = response.json().get("uploads", [])
    unconfirmed = [result for result in results if result.get("state") not in _ACCEPTED_STATES]
    if len(results) != len(batch) or unconfirmed:
        detail = {key: result.get(key) for result in unconfirmed[:1] for key in ("state", "error_code", "error_message")}
        raise RuntimeError(f"server did not durably confirm every synthetic upload: {detail}")
    return sum(result.get("state") == "pending_processing" for result in results)


def _complete_batches(
    client: httpx.Client, base_url: str, headers: dict[str, str], items: list[dict],
    *, lanes: int = 1, batch_size: int = 16,
) -> int:
    """Finalize in batches; more than one lane keeps several batches in flight."""
    batches = [items[offset:offset + batch_size] for offset in range(0, len(items), batch_size)]
    with ThreadPoolExecutor(max_workers=lanes) as pool:
        futures = [pool.submit(_complete_batch, client, base_url, headers, b) for b in batches]
        return sum(future.result() for future in as_completed(futures))


def run_profile(
    client: httpx.Client, base_url: str, headers: dict[str, str], root: Path,
    *, file_count: int, total_bytes: int, concurrency: int, label: str,
    completion_lanes: int = 1, completion_batch: int = 16,
) -> dict:
    bytes_each = total_bytes // file_count
    if bytes_each < 1024:
        raise ValueError("each synthetic JPEG must be at least 1 KiB")
    if bytes_each > 32 * 1024 * 1024:
        raise ValueError("each file must fit the server's 32 MiB resumable chunk limit")
    with tempfile.TemporaryDirectory(prefix="sync-benchmark-", dir=root) as temporary:
        fixture_dir = Path(temporary)
        items: list[dict] = []
        seed_base = int(label.split("-", 1)[0], 16)
        for index in range(file_count):
            path = fixture_dir / f"{label}-{index:04d}.jpg"
            digest = _make_fixture(path, bytes_each, seed_base + index)
            items.append({"path": path, "filename": path.name, "size": path.stat().st_size, "sha256": digest})

        hash_started = time.perf_counter()
        for item in items:
            digest = hashlib.sha256(item["path"].read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise RuntimeError("synthetic fixture changed after preparation")
        hash_seconds = time.perf_counter() - hash_started

        init_started = time.perf_counter()
        reservations = []
        for offset in range(0, len(items), 16):
            batch = items[offset:offset + 16]
            response = client.post(
                f"{base_url}/api/sync/uploads/batch",
                headers=headers,
                json={"uploads": [
                    {
                        "client_upload_id": f"{label}-{index:04d}",
                        "filename": item["filename"],
                        "size": item["size"],
                        "sha256": item["sha256"],
                        "captured_at": "2026-09-25T00:00:00Z",
                        "source": {"id": "synthetic-benchmark", "name": "Synthetic benchmark", "media_kind": "image"},
                    }
                    for index, item in enumerate(batch, start=offset)
                ]},
            )
            response.raise_for_status()
            reservations.extend(response.json().get("uploads", []))
        if len(reservations) != len(items) or any(result.get("state") != "uploading" for result in reservations):
            raise RuntimeError("server did not reserve every synthetic upload")
        for item, reservation in zip(items, reservations, strict=True):
            item["upload_id"] = reservation["upload_id"]
        init_seconds = time.perf_counter() - init_started

        pipeline_started = time.perf_counter()
        transfer_started = time.perf_counter()
        upload_durations: list[float] = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_upload_one, client, base_url, headers, item) for item in items]
            for future in as_completed(futures):
                upload_durations.append(future.result())
        transfer_seconds = time.perf_counter() - transfer_started

        completion_started = time.perf_counter()
        deferred = _complete_batches(
            client, base_url, headers, items, lanes=completion_lanes, batch_size=completion_batch,
        )
        completion_seconds = time.perf_counter() - completion_started
        pipeline_seconds = time.perf_counter() - pipeline_started
        end_to_end_seconds = hash_seconds + init_seconds + pipeline_seconds
        return {
            "files": file_count,
            "bytes": sum(item["size"] for item in items),
            "concurrency": concurrency,
            "completion_lanes": completion_lanes,
            "completion_batch": completion_batch,
            "completion_items_per_second": round(file_count / completion_seconds, 2),
            "deferred_to_processor": deferred,
            "hash_seconds": round(hash_seconds, 4),
            "init_seconds": round(init_seconds, 4),
            "transfer_seconds": round(transfer_seconds, 4),
            "completion_seconds": round(completion_seconds, 4),
            "pipeline_seconds": round(pipeline_seconds, 4),
            "end_to_end_seconds": round(end_to_end_seconds, 4),
            "end_to_end_mbps": round(sum(item["size"] for item in items) / end_to_end_seconds / 1_000_000, 2),
            "confirmed_items_per_second": round(file_count / end_to_end_seconds, 2),
            "transfer_item_p50_ms": round(statistics.median(upload_durations) * 1000, 1),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".iris-load", help="marked disposable lab root")
    parser.add_argument("--url", default="http://127.0.0.1:8851")
    parser.add_argument("--total-mib", type=int, default=32)
    parser.add_argument("--files", default="1,8,32")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--completion-lanes", type=int, default=1,
                        help="complete-batch requests kept in flight at once")
    parser.add_argument("--completion-batch", type=int, default=16, help="uploads per complete-batch")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not _is_loopback_url(args.url):
        parser.error("only plain HTTP loopback URLs are allowed; production targets are refused")
    if not 1 <= args.total_mib <= 256 or not 1 <= args.concurrency <= 32:
        parser.error("total-mib must be 1..256 and concurrency must be 1..32")
    if not 1 <= args.completion_lanes <= 16 or not 1 <= args.completion_batch <= 64:
        parser.error("completion-lanes must be 1..16 and completion-batch 1..64")
    try:
        root = lab_root(args.root)
    except ValueError as exc:
        parser.error(str(exc))
    if not (root / MARKER).is_file() or not accounts_path(root).is_file():
        parser.error("recognized disposable lab not found; run scripts/load_lab.py prepare first")
    accounts = json.loads(accounts_path(root).read_text(encoding="utf-8"))
    if not accounts:
        parser.error("the disposable lab has no accounts")
    account = accounts[0]
    base_url = args.url.rstrip("/")
    with httpx.Client(timeout=180, trust_env=False) as client:
        login = client.post(
            f"{base_url}/api/auth/devices/login",
            data={"username": account["username"], "password": account["password"],
                  "device_name": "Disposable sync benchmark", "platform": "benchmark"},
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        reports = []
        run_id = uuid.uuid4().hex[:8]
        for index, raw_count in enumerate(args.files.split(",")):
            file_count = int(raw_count)
            if file_count < 1 or file_count > 256:
                parser.error("each file count must be between 1 and 256")
            reports.append(run_profile(
                client, base_url, headers, root,
                file_count=file_count,
                total_bytes=args.total_mib * 1024 * 1024,
                concurrency=args.concurrency,
                completion_lanes=args.completion_lanes,
                completion_batch=args.completion_batch,
                label=f"{run_id}-profile-{index}-{file_count}",
            ))
    result = {"target": "disposable local Iris lab", "ai_requested": False, "profiles": reports}
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + os.linesep, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
