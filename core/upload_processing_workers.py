"""Bounded, lifecycle-managed workers for post-acceptance upload processing."""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.sync_processor import process_upload
from core.users_db import IrisUser

_logger = logging.getLogger("iris.sync")
_STOP = object()


@dataclass(frozen=True)
class _ProcessingJob:
    db_path: Path
    media_root: Path
    model_name: str
    user_id: int
    upload_id: str
    file_path: Path
    use_ai: bool
    on_finished: Callable[[int], None]


class UploadProcessingWorkers:
    """Serialize expensive post-upload work and stop cleanly with the app.

    The queue is deliberately bounded. If saturated, accepted files remain
    durably represented in ``sync_uploads`` and the recovery scanner will pick
    them up later; transient in-memory work is never the source of truth.
    """

    def __init__(self, *, max_pending: int = 64) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self._jobs: queue.Queue[_ProcessingJob | object] = queue.Queue(maxsize=max_pending)
        self._stopping = threading.Event()
        self._keys_lock = threading.Lock()
        self._accepted_keys: set[tuple[str, str]] = set()
        self._thread = threading.Thread(
            target=self._run, name="iris-upload-processing", daemon=True
        )
        self._thread.start()

    def submit(
        self,
        user: IrisUser,
        upload_id: str,
        file_path: Path,
        *,
        use_ai: bool,
        on_finished: Callable[[int], None],
    ) -> bool:
        if self._stopping.is_set():
            return False
        key = (str(user.db_path), upload_id)
        job = _ProcessingJob(
            db_path=user.db_path,
            media_root=user.media_root,
            model_name=user.model_name,
            user_id=user.id,
            upload_id=upload_id,
            file_path=file_path,
            use_ai=use_ai,
            on_finished=on_finished,
        )
        with self._keys_lock:
            if key in self._accepted_keys:
                return True
            try:
                self._jobs.put_nowait(job)
            except queue.Full:
                return False
            self._accepted_keys.add(key)
        return True

    def stop(self, *, timeout: float = 10.0) -> None:
        """Stop accepting work, then give the active job a bounded grace period."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        # Pending jobs are durable and will be rediscovered at next startup.
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            if isinstance(job, _ProcessingJob):
                with self._keys_lock:
                    self._accepted_keys.discard((str(job.db_path), job.upload_id))
        try:
            self._jobs.put_nowait(_STOP)
        except queue.Full:  # defensive; queue was drained above
            pass
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is _STOP:
                return
            assert isinstance(job, _ProcessingJob)
            try:
                process_upload(
                    db_path=job.db_path,
                    media_root=job.media_root,
                    model_name=job.model_name,
                    upload_id=job.upload_id,
                    file_path=job.file_path,
                    on_finished=lambda current=job: current.on_finished(current.user_id),
                    use_ai=job.use_ai,
                )
            except Exception as exc:
                _logger.error(
                    "upload_processing_worker_failed user_id=%s error_type=%s",
                    job.user_id,
                    type(exc).__name__,
                )
            finally:
                with self._keys_lock:
                    self._accepted_keys.discard((str(job.db_path), job.upload_id))
