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
    workers.stop(timeout=2)

    assert not workers._thread.is_alive()
    assert seen[0]["db_path"] == user.db_path
    assert seen[0]["media_root"] == user.media_root
    assert seen[0]["upload_id"] == "upload-1"
    assert seen[0]["use_ai"] is False
    assert seen[1] == {"callback_user_id": user.id}


def test_worker_rejects_jobs_after_shutdown(tmp_path: Path):
    workers = UploadProcessingWorkers()
    workers.stop(timeout=2)
    user = _user(1, tmp_path)

    assert not workers.submit(
        user,
        "late-job",
        user.media_root / "file.jpg",
        use_ai=True,
        on_finished=lambda _user_id: None,
    )
