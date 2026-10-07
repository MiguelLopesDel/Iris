"""The "syncing" actor: devices backing up media the way the Android app does.

Each device logs in on its own, then runs ``concurrency`` upload workers over
its share of the items. Like the app:

- reservations are coalesced into ``POST /api/sync/uploads/batch`` requests of
  up to 16 items (a short window lets concurrent workers join);
- each item is sent with chunked ``PUT``s of the server's chunk size, or, with
  ``bundle`` set, small items travel whole in ``POST /api/sync/ingest``
  requests of up to that many bytes, which also finish them (no separate
  completion);
- with ``rtt`` set, every request first waits that long, as a phone's request
  would on its way to a server over Wi-Fi or a VPN (the lab runs on the server
  itself, where a round trip costs nothing);
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
_BUNDLE_ITEMS = 64
_BUNDLE_LANES = 4
_COALESCE_SECONDS = 0.008


def _jpeg_header() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (40, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


_HEADER = _jpeg_header()
# Random bytes generated once; each item reads its own slice of them.
_POOL = memoryview(random.Random(20261007).randbytes(64 * 1024 * 1024))


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
        """The item's bytes in [start, end), deterministic and unique per item.

        A JPEG header, then 8 bytes of the item's seed (so no two items share
        a hash), then a slice of one random pool chosen by the seed. Slicing
        a pool built once keeps the client cheap: generating fresh random
        bytes for every file took over a quarter of each batch's time, and
        the lab runs the client on the server's machine.
        """
        end = self.size if end is None else end
        prefix = _HEADER + self.seed.to_bytes(8, "big")
        position = start
        while position < end:
            if position < len(prefix):
                piece = prefix[position : min(end, len(prefix))]
            else:
                # Wraps around the pool, so items of any size are covered.
                body_start = (self.seed + position - len(prefix)) % len(_POOL)
                length = min(end - position, _GEN_BLOCK, len(_POOL) - body_start)
                piece = _POOL[body_start : body_start + length]
            yield bytes(piece)
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

    def __init__(self, send, lanes: int, max_items: int = _BATCH, max_bytes: int = 0) -> None:
        self._send = send
        self._queue: asyncio.Queue = asyncio.Queue()
        self._lanes = asyncio.Semaphore(lanes)
        self._max_items, self._max_bytes = max_items, max_bytes
        self._carry = None  # an entry that did not fit the previous batch
        self._task = asyncio.create_task(self._run())

    def _fits(self, batch, entry) -> bool:
        if len(batch) >= self._max_items:
            return False
        if not self._max_bytes:
            return True
        return sum(item.size for item, _ in batch) + entry[0].size <= self._max_bytes

    async def submit(self, item: Item) -> dict:
        future = asyncio.get_running_loop().create_future()
        await self._queue.put((item, future))
        return await future

    async def _run(self) -> None:
        while True:
            first, self._carry = self._carry or await self._queue.get(), None
            await asyncio.sleep(_COALESCE_SECONDS)
            batch = [first]
            while not self._queue.empty():
                entry = self._queue.get_nowait()
                if not self._fits(batch, entry):
                    self._carry = entry
                    break
                batch.append(entry)
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


class _DelayedClient:
    """An HTTP client whose every request costs a network round trip first."""

    def __init__(self, client: httpx.AsyncClient, rtt: float) -> None:
        self._client, self._rtt = client, rtt

    async def post(self, *args, **kwargs) -> httpx.Response:
        await asyncio.sleep(self._rtt)
        return await self._client.post(*args, **kwargs)

    async def put(self, *args, **kwargs) -> httpx.Response:
        await asyncio.sleep(self._rtt)
        return await self._client.put(*args, **kwargs)

    async def get(self, *args, **kwargs) -> httpx.Response:
        await asyncio.sleep(self._rtt)
        return await self._client.get(*args, **kwargs)


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
    client = _DelayedClient(client, actor.rtt) if actor.rtt else client
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

    async def bundle(batch: list[Item]) -> list[dict]:
        # Received and finished in one request: no separate completion.
        response = await client.post(
            f"{base_url}/api/sync/ingest",
            headers={
                **headers,
                "X-Iris-Ingest": ",".join(f"{item.upload_id}:{item.size}" for item in batch),
            },
            content=b"".join(piece for item in batch for piece in item.blocks()),
        )
        response.raise_for_status()
        return response.json()["uploads"]

    if actor.batch_items:
        await _run_full_batches(
            client, base_url, headers, items, actor, reserve, clock_start, deadline
        )
        return result

    reservations = _Coalescer(reserve, lanes=1)  # one in flight, as the app's init batcher
    completions = _Coalescer(complete, lanes=actor.completion_lanes)
    bundles = (
        _Coalescer(bundle, lanes=_BUNDLE_LANES, max_items=_BUNDLE_ITEMS, max_bytes=actor.bundle_bytes)
        if actor.bundle_bytes else None
    )
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
                if bundles is not None and item.size <= actor.bundle_item_max:
                    finished = await bundles.submit(item)
                    item.sent = item.finished = clock() - clock_start
                    item.state = finished.get("state") or f"error {finished.get('error_code')}"
                    if "error_code" in finished:
                        item.error = f"{finished['error_code']}: {finished.get('error_message', '')}"[:200]
                    continue
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
        if bundles is not None:
            bundles.close()
    return result


async def _run_full_batches(client, base_url, headers, items, actor, reserve, clock_start, deadline):
    """Reserve and ingest whole batches, sized as the server suggests or as set.

    Each batch is one reservation and one ingest request for all its photos.
    One task reserves the next batches while ``batches_in_flight`` lanes send
    the reserved ones, so a lane never waits for a reservation: the queue
    between them holds at most one reserved batch per lane.
    """
    clock = time.monotonic
    limits = (await client.get(f"{base_url}/api/sync/ingest/limits", headers=headers)).json()
    pending = list(items)
    ready: asyncio.Queue = asyncio.Queue(maxsize=actor.batches_in_flight)

    def next_batch() -> list[Item]:
        wanted = limits["suggested_items"] if actor.batch_items < 0 else actor.batch_items
        wanted = min(wanted, limits["max_items"])
        batch, size = [], 0
        while pending and len(batch) < wanted and size + pending[0].size <= limits["max_bytes"]:
            size += pending[0].size
            batch.append(pending.pop(0))
        if not batch and pending:
            batch.append(pending.pop(0))  # larger than a batch: refused on its own
        return batch

    def fail(batch: list[Item], exc: BaseException) -> None:
        now = clock() - clock_start
        for item in batch:
            if not item.finished:
                item.finished = now
                item.state, item.error = "error", f"{type(exc).__name__}: {exc}"[:200]

    async def reserver() -> None:
        while pending and clock() < deadline:
            batch = next_batch()
            started = clock() - clock_start
            for item in batch:
                item.started = started
            try:
                reserved = await reserve(batch)
            except Exception as exc:
                fail(batch, exc)
                continue
            now = clock() - clock_start
            sending = []
            for item, answer in zip(batch, reserved, strict=True):
                item.reserved = now
                if answer.get("state") != "uploading":
                    item.state = answer.get("state") or f"error {answer.get('error_code')}"
                    item.sent = item.finished = now
                    continue
                item.upload_id = answer["upload_id"]
                sending.append(item)
            if sending:
                await ready.put(sending)
        for _ in range(actor.batches_in_flight):
            await ready.put(None)

    async def lane() -> None:
        nonlocal limits
        while (sending := await ready.get()) is not None:
            if clock() >= deadline:
                continue  # drain without sending; the run is over
            try:
                response = await client.post(
                    f"{base_url}/api/sync/ingest",
                    headers={
                        **headers,
                        "X-Iris-Ingest": ",".join(f"{item.upload_id}:{item.size}" for item in sending),
                    },
                    content=b"".join(piece for item in sending for piece in item.blocks()),
                )
                response.raise_for_status()
                answer = response.json()
                limits = answer.get("limits", limits)
                now = clock() - clock_start
                for item, outcome in zip(sending, answer["uploads"], strict=True):
                    item.sent = item.finished = now
                    item.state = outcome.get("state") or f"error {outcome.get('error_code')}"
                    if "error_code" in outcome:
                        item.error = f"{outcome['error_code']}: {outcome.get('error_message', '')}"[:200]
            except Exception as exc:
                fail(sending, exc)

    await asyncio.gather(reserver(), *(lane() for _ in range(actor.batches_in_flight)))


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
