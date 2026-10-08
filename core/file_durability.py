"""Group file and directory fsyncs from concurrent requests into shared barriers.

Each fsync makes the disk wait; on a spinning disk issued one after another
they bound how many files per second can be stored. Issued together, the
filesystem and the drive can serve many at once (on the reference HDD about
four times the files per second at 64 in flight). This service lets every
ingestion request hand over its files and receive a Future that completes
once those files and their directory entries are on disk.

It only orders file data. Database state is made durable by the account
writer's FULL commit, which callers issue after this barrier completes.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from core.ingest_policy import IngestPolicy
from core.sync_file_ops import fsync_directory

_STOP = object()


class FileDurabilityBusy(RuntimeError):
    """The pending queue is full; the caller should apply backpressure."""


@dataclass
class _Request:
    files: tuple[Path, ...]
    directories: frozenset[Path]
    future: Future[None] = field(default_factory=Future)


class FileDurabilityService:
    """Bounded group commit for file contents and directory entries.

    One coordinator thread collects requests: it starts a group with the
    first waiting request, adds every request that arrives within
    ``durability_window_s`` until the group holds ``fsync_concurrency`` files,
    then fsyncs those files in parallel and each distinct directory once.
    Requests that arrive while a group is being synced form the next group.
    """

    def __init__(self, policy: IngestPolicy | None = None, *, max_pending: int = 1024) -> None:
        self._policy = policy or IngestPolicy()
        self._queue: queue.Queue[_Request | object] = queue.Queue(maxsize=max_pending)
        self._pool = ThreadPoolExecutor(
            max_workers=self._policy.fsync_concurrency, thread_name_prefix="iris-fsync"
        )
        self._state_lock = threading.Lock()
        self._stopping = False
        self._thread = threading.Thread(
            target=self._run, name="iris-file-durability", daemon=True
        )
        self._thread.start()

    def flush(self, files: Iterable[Path], directories: Iterable[Path] = ()) -> Future[None]:
        """Queue files and directories; the Future completes once all are synced."""
        request = _Request(tuple(files), frozenset(directories))
        if not request.files and not request.directories:
            request.future.set_result(None)
            return request.future
        with self._state_lock:
            if self._stopping:
                raise RuntimeError("file durability service is stopping")
            try:
                self._queue.put_nowait(request)
            except queue.Full as exc:
                raise FileDurabilityBusy("file durability queue is full") from exc
        return request.future

    def stop(self, *, timeout: float = 10.0) -> bool:
        """Stop accepting work, finish queued groups, and join the coordinator."""
        with self._state_lock:
            if not self._stopping:
                self._stopping = True
                self._queue.put(_STOP)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            return False
        self._pool.shutdown(wait=True)
        return True

    def _run(self) -> None:
        while True:
            first = self._queue.get()
            if first is _STOP:
                return
            group = [first]
            files = len(first.files)
            deadline = time.monotonic() + self._policy.durability_window_s
            stop_after = False
            while files < self._policy.fsync_concurrency:
                try:
                    # Requests already waiting join without delay; the window
                    # only bounds how long a small group waits for company.
                    remaining = deadline - time.monotonic()
                    following = (
                        self._queue.get_nowait() if remaining <= 0
                        else self._queue.get(timeout=remaining)
                    )
                except queue.Empty:
                    break
                if following is _STOP:
                    stop_after = True
                    break
                group.append(following)
                files += len(following.files)
            self._sync_group(group)
            if stop_after:
                return

    def _sync_group(self, group: list[_Request]) -> None:
        # Files of several requests share a group; each request fails only on
        # its own files and directories (another request's file may already
        # be gone, removed with its abandoned batch).
        failures: dict[Path, BaseException] = {}
        futures = {
            path: self._pool.submit(_fsync_file, path)
            for path in {path for request in group for path in request.files}
        }
        wait(futures.values())
        for path, future in futures.items():
            if future.exception() is not None:
                failures[path] = future.exception()
        for directory in sorted({d for request in group for d in request.directories}):
            try:
                fsync_directory(directory)
            except BaseException as exc:
                failures[directory] = exc
        for request in group:
            if request.future.done():
                continue
            error = next(
                (failures[path] for path in (*request.files, *request.directories) if path in failures),
                None,
            )
            if error is None:
                request.future.set_result(None)
            else:
                request.future.set_exception(error)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
