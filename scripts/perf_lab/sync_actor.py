"""The "syncing" actor: devices backing up media the way the Android app does.

Each device logs in on its own, then runs ``concurrency`` upload workers over
its share of the items. Like the app:

- reservations are coalesced into ``POST /api/sync/uploads/batch`` requests of
  up to 16 items (a short window lets concurrent workers join);
- each item is sent with chunked ``PUT``s of the server's chunk size;
- completions are coalesced into ``complete-batch`` requests, with
  ``completion_lanes`` of them in flight (the app keeps one).

Payloads are synthetic and unique per item (a tiny JPEG header and seeded
bytes), generated chunk by chunk so large files never sit in memory. Hashes
are computed before the clock starts, as the app hashes during its scan.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from scripts.perf_lab.scenario import SyncingActor
from scripts.perf_lab.statistics import percentile

_GEN_BLOCK = 1024 * 1024
_BATCH = 16
_COALESCE_SECONDS = 0.008


def _jpeg_header() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (40, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


_HEADER = _jpeg_header()


@dataclass
class Item:
    index: int
    size: int
    seed: int
    sha256: str = ""
    upload_id: str = ""
    chunk_size: int = 0
    # Monotonic times relative to the run start, seconds.
    started: float = 0.0
    reserved: float = 0.0
    sent: float = 0.0
    finished: float = 0.0
    state: str = ""
    error: str = ""

    def blocks(self, start: int = 0, end: int | None = None):
        """The item's bytes in [start, end), generated deterministically."""
        end = self.size if end is None else end
        position = start
        while position < end:
            if position < len(_HEADER):
                piece = _HEADER[position : min(end, len(_HEADER))]
            else:
                block = (position - len(_HEADER)) // _GEN_BLOCK
                offset = (position - len(_HEADER)) % _GEN_BLOCK
                data = random.Random(self.seed * 1_000_003 + block).randbytes(_GEN_BLOCK)
                piece = data[offset : offset + (end - position)]
            yield piece
            position += len(piece)

    def compute_hash(self) -> None:
        digest = hashlib.sha256()
        for piece in self.blocks():
            digest.update(piece)
        self.sha256 = digest.hexdigest()


@dataclass
class DeviceResult:
    items: list[Item] = field(default_factory=list)
    login_error: str = ""


class _Coalescer:
    """Gathers concurrent requests into batches, like the app's batchers."""

    def __init__(self, send, lanes: int) -> None:
        self._send = send
        self._queue: asyncio.Queue = asyncio.Queue()
        self._lanes = asyncio.Semaphore(lanes)
        self._task = asyncio.create_task(self._run())

    async def submit(self, item: Item) -> dict:
        future = asyncio.get_running_loop().create_future()
        await self._queue.put((item, future))
        return await future

    async def _run(self) -> None:
        while True:
            first = await self._queue.get()
            await asyncio.sleep(_COALESCE_SECONDS)
            batch = [first]
            while len(batch) < _BATCH and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            await self._lanes.acquire()
            asyncio.create_task(self._dispatch(batch))

    async def _dispatch(self, batch) -> None:
        try:
            results = await self._send([item for item, _ in batch])
            for (_, future), result in zip(batch, results, strict=True):
                if not future.done():
                    future.set_result(result)
        except Exception as exc:
            for _, future in batch:
                if not future.done():
                    future.set_exception(exc)
        finally:
            self._lanes.release()

    def close(self) -> None:
        self._task.cancel()


async def run_device(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    username: str,
    password: str,
    device: str,
    items: list[Item],
    actor: SyncingActor,
    clock_start: float,
    deadline: float,
) -> DeviceResult:
    result = DeviceResult(items=items)
    login = await client.post(
        f"{base_url}/api/auth/devices/login",
        data={
            "username": username,
            "password": password,
            "device_name": device,
            "platform": "perf-lab",
        },
    )
    if login.status_code != 200:
        result.login_error = f"HTTP {login.status_code}"
        return result
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    async def reserve(batch: list[Item]) -> list[dict]:
        response = await client.post(
            f"{base_url}/api/sync/uploads/batch",
            headers=headers,
            json={
                "uploads": [
                    {
                        "client_upload_id": f"{device}-{item.index}",
                        "filename": f"IMG_{item.index:06d}.jpg",
                        "size": item.size,
                        "sha256": item.sha256,
                        "captured_at": "2026-09-01T12:00:00Z",
                        "source": {"id": "perf-lab", "name": "Perf lab", "media_kind": "image"},
                    }
                    for item in batch
                ]
            },
        )
        response.raise_for_status()
        return response.json()["uploads"]

    async def complete(batch: list[Item]) -> list[dict]:
        response = await client.post(
            f"{base_url}/api/sync/uploads/complete-batch",
            headers=headers,
            json={
                "uploads": [{"upload_id": item.upload_id} for item in batch],
            },
        )
        response.raise_for_status()
        return response.json()["uploads"]

    reservations = _Coalescer(reserve, lanes=1)  # one in flight, as the app's init batcher
    completions = _Coalescer(complete, lanes=actor.completion_lanes)
    pending = list(items)
    clock = time.monotonic

    async def worker() -> None:
        while pending and clock() < deadline:
            item = pending.pop(0)
            item.started = clock() - clock_start
            try:
                reserved = await reservations.submit(item)
                item.reserved = clock() - clock_start
                if reserved.get("state") != "uploading":
                    item.state = reserved.get("state") or f"error {reserved.get('error_code')}"
                    item.sent = item.finished = item.reserved
                    continue
                item.upload_id, item.chunk_size = reserved["upload_id"], int(reserved["chunk_size"])
                offset = int(reserved.get("offset", 0))
                while offset < item.size:
                    end = min(item.size, offset + item.chunk_size)
                    body = b"".join(item.blocks(offset, end))
                    put = await client.put(
                        f"{base_url}/api/sync/uploads/{item.upload_id}",
                        params={"offset": offset},
                        headers=headers,
                        content=body,
                    )
                    put.raise_for_status()
                    offset = int(put.json()["offset"])
                item.sent = clock() - clock_start
                completed = await completions.submit(item)
                item.finished = clock() - clock_start
                item.state = completed.get("state") or f"error {completed.get('error_code')}"
            except Exception as exc:
                item.finished = clock() - clock_start
                item.state, item.error = "error", f"{type(exc).__name__}: {exc}"[:200]

    try:
        await asyncio.gather(*(worker() for _ in range(actor.concurrency)))
    finally:
        reservations.close()
        completions.close()
    return result


def make_items(actor: SyncingActor, rng: random.Random, start_index: int) -> list[Item]:
    items = [
        Item(index=start_index + n, size=actor.sizes.sample(rng), seed=rng.getrandbits(48))
        for n in range(actor.items)
    ]
    for item in items:
        item.compute_hash()
    return items


def summarize(items: list[Item], *, elapsed: float) -> dict[str, Any]:
    done = [
        item
        for item in items
        if item.finished and item.state in {"ready", "duplicate", "pending_processing"}
    ]
    failed = [
        item for item in items
        if item.state.startswith("error") or item.state in {"failed", "timeout"}
    ]

    def percentiles(values: list[float]) -> dict[str, float]:
        if not values:
            return {}

        def at(q: float) -> float:
            return round(percentile(values, q) * 1000, 1)

        return {"p50_ms": at(0.50), "p95_ms": at(0.95), "p99_ms": at(0.99)}

    sent = [item for item in done if item.sent > item.reserved]
    return {
        "items_done": len(done),
        "items_failed": len(failed),
        "items_timed_out": sum(1 for item in items if item.state == "timeout"),
        "items_not_started": sum(1 for item in items if not item.started),
        "bytes_done": sum(item.size for item in done),
        "items_per_s": round(len(done) / elapsed, 2) if elapsed else 0.0,
        "mb_per_s": round(sum(item.size for item in done) / 1e6 / elapsed, 2) if elapsed else 0.0,
        "error_rate": round(len(failed) / max(1, len(done) + len(failed)), 4),
        "total": percentiles([item.finished - item.started for item in done]),
        "reserve": percentiles([item.reserved - item.started for item in done]),
        "send": percentiles([item.sent - item.reserved for item in sent]),
        "complete": percentiles([item.finished - item.sent for item in sent]),
        "errors": sorted({item.error for item in failed if item.error})[:5],
    }
