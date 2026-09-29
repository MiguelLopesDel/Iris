from __future__ import annotations

import threading
from pathlib import Path

from core.upload_processing_workers import UploadProcessingWorkers
from core.users_db import IrisUser


def _user(user_id: int, root: Path) -> IrisUser:
    return IrisUser(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="unused",
        display_name="",
        is_admin=False,
        db_path=root / f"{user_id}.db",
        media_root=root / str(user_id),
        model_name="test-model",
        session_version=1,
    )


def test_worker_preserves_account_context_and_stops_cleanly(monkeypatch, tmp_path: Path):
    import core.upload_processing_workers as module

    completed = threading.Event()
    seen = []

    def fake_process_upload(**kwargs):
        seen.append(kwargs)
        kwargs["on_finished"]()

    monkeypatch.setattr(module, "process_upload", fake_process_upload)
    workers = UploadProcessingWorkers(max_pending=2)
    user = _user(42, tmp_path)
    assert workers.submit(
        user,
        "upload-1",
        user.media_root / "original.jpg",
        use_ai=False,
        on_finished=lambda user_id: (seen.append({"callback_user_id": user_id}), completed.set()),
    )

    assert completed.wait(timeout=2)
    assert workers.stop(timeout=2)

    assert seen[0]["db_path"] == user.db_path
    assert seen[0]["media_root"] == user.media_root
    assert seen[0]["upload_id"] == "upload-1"
    assert seen[0]["use_ai"] is False
    assert seen[1] == {"callback_user_id": user.id}


def test_worker_rejects_jobs_after_shutdown(tmp_path: Path):
    workers = UploadProcessingWorkers()
    assert workers.stop(timeout=2)
    user = _user(1, tmp_path)

    assert not workers.submit(
        user,
        "late-job",
        user.media_root / "file.jpg",
        use_ai=True,
        on_finished=lambda _user_id: None,
    )


def test_worker_coalesces_duplicate_jobs_and_bounds_queue(monkeypatch, tmp_path: Path):
    import core.upload_processing_workers as module

    entered = threading.Event()
    release = threading.Event()
    two_finished = threading.Event()
    finished_count = 0
    finish_lock = threading.Lock()

    def fake_process_upload(**kwargs):
        if kwargs["upload_id"] == "active":
            entered.set()
            assert release.wait(timeout=2)
        kwargs["on_finished"]()

    def finished(_user_id):
        nonlocal finished_count
        with finish_lock:
            finished_count += 1
            if finished_count == 2:
                two_finished.set()

    monkeypatch.setattr(module, "process_upload", fake_process_upload)
    workers = UploadProcessingWorkers(max_pending=1)
    user = _user(7, tmp_path)
    def submit(upload_id: str) -> bool:
        return workers.submit(
            user,
            upload_id,
            user.media_root / f"{upload_id}.jpg",
            use_ai=False,
            on_finished=finished,
        )

    assert submit("active")
    assert entered.wait(timeout=2)
    assert submit("queued")
    assert submit("queued")  # same durable job is already running/queued
    assert not submit("rejected")  # a distinct job cannot exceed the bound

    release.set()
    assert two_finished.wait(timeout=2)
    workers.stop(timeout=2)


def test_shutdown_discards_only_volatile_queued_work_and_allows_requeue(
    monkeypatch, tmp_path: Path
):
    import core.upload_processing_workers as module

    active_started = threading.Event()
    release_active = threading.Event()
    durable_processed = threading.Event()
    processed = []
    processed_lock = threading.Lock()

    def fake_process_upload(**kwargs):
        if kwargs["upload_id"] == "active":
            active_started.set()
            assert release_active.wait(timeout=2)
        with processed_lock:
            processed.append(kwargs["upload_id"])
        if kwargs["upload_id"] == "durable-pending":
            durable_processed.set()

    monkeypatch.setattr(module, "process_upload", fake_process_upload)
    user = _user(23, tmp_path)
    workers = UploadProcessingWorkers(max_pending=1)

    def submit(workers_instance, upload_id):
        return workers_instance.submit(
            user,
            upload_id,
            user.media_root / f"{upload_id}.jpg",
            use_ai=False,
            on_finished=lambda _user_id: None,
        )

    assert submit(workers, "active")
    assert active_started.wait(timeout=2)
    assert submit(workers, "durable-pending")
    assert not workers.stop(timeout=0.01)
    assert "durable-pending" not in processed

    release_active.set()
    assert workers.stop(timeout=2)

    replacement = UploadProcessingWorkers(max_pending=1)
    assert submit(replacement, "durable-pending")
    assert durable_processed.wait(timeout=2)
    assert replacement.stop(timeout=2)
    assert processed.count("durable-pending") == 1
