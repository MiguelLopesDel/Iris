"""Bring a file that is already on the server into an account's private library.

The Android upload path receives bytes over the network; this one starts from
bytes Iris already holds (an item of a shared space) and clones them, so on a
reflink-capable filesystem saving a shared photo costs no data blocks. From
the queue onwards both paths are the same: a ``sync_uploads`` row in
``pending_processing``, a change in the feed, and :func:`process_upload`.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.fs_clone import clone_file
from core.indexer_db import init_db
from core.sync_db import append_change, ensure_tables, now_iso
from core.upload_processing_workers import UploadProcessingWorkers
from core.users_db import IrisUser

_SAFE_NAME = re.compile(r"[^\w.\- ]+")


class LibraryQuotaExceeded(Exception):
    """The account's library has no room for this file."""


@dataclass(frozen=True)
class Intake:
    state: str  # "pending_processing" | "duplicate"
    upload_id: str | None
    media_id: int | None
    path: Path | None
    created: bool = False


def _library_usage_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _known(conn: sqlite3.Connection, sha256: str) -> Intake | None:
    """Already in the library, or already queued: saving twice is a no-op."""
    media = conn.execute(
        "SELECT id FROM memes WHERE content_hash = ? LIMIT 1", (sha256,)
    ).fetchone()
    if media is not None:
        return Intake("duplicate", None, int(media[0]), None)
    queued = conn.execute(
        """SELECT id, temp_path FROM sync_uploads
           WHERE expected_hash = ? AND state IN ('pending_processing', 'processing')
           ORDER BY created_at DESC LIMIT 1""",
        (sha256,),
    ).fetchone()
    if queued is not None:
        return Intake("pending_processing", str(queued[0]), None, Path(queued[1]))
    return None


def save_copy(
    user: IrisUser,
    source: Path,
    filename: str,
    sha256: str,
    size: int,
    *,
    strategy: str,
    quota_bytes: int,
    device_id: str | None,
    origin: dict[str, Any],
) -> Intake:
    """Clone ``source`` into ``user``'s library and queue it for processing."""
    init_db(user.db_path).close()
    conn = sqlite3.connect(user.db_path)
    try:
        ensure_tables(conn)
        known = _known(conn, sha256)
        if known is not None:
            return known
        if size > quota_bytes - _library_usage_bytes(user.media_root):
            raise LibraryQuotaExceeded
        name = _SAFE_NAME.sub("_", Path(filename).name).strip() or "item"
        month = now_iso()[:7]
        destination_dir = user.media_root / "shared" / month
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{sha256[:12]}-{name}"
        if not destination.exists():
            clone_file(source, destination, strategy)
        upload_id = uuid.uuid4().hex
        with conn:
            conn.execute(
                """INSERT INTO sync_uploads
                (id, device_id, filename, expected_size, expected_hash, received_size,
                 state, temp_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending_processing', ?, ?, ?)""",
                (upload_id, device_id or "", name, size, sha256, size,
                 str(destination), now_iso(), now_iso()),
            )
            append_change(conn, "media", upload_id, "created", 1, {
                "upload_id": upload_id, "filename": name, "path": str(destination),
                "state": "pending_processing", "origin": origin,
            })
        return Intake("pending_processing", upload_id, None, destination, created=True)
    finally:
        conn.close()


def start_processing(
    user: IrisUser,
    intake: Intake,
    on_finished,
    *,
    use_ai: bool,
    workers: UploadProcessingWorkers,
) -> None:
    """Submit durable intake work to the server-owned processing worker."""
    if not intake.created or intake.upload_id is None or intake.path is None:
        return
    workers.submit(
        user,
        intake.upload_id,
        intake.path,
        use_ai=use_ai,
        on_finished=lambda _user_id: on_finished(),
    )
