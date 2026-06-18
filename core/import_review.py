"""Import deduplication: persistent job state + quarantine ("review later") queue.

When importing large batches where most files already exist, the indexer routes
suspected duplicates here instead of silently skipping them. The user reviews the
quarantine afterwards (panel grouped by detection category) and decides per item.

Two tables:
- ``import_jobs``   — persistent job state, so an import survives a server restart
  and resumes automatically (see server.py lifespan).
- ``import_review`` — one row per quarantined candidate (the file that was NOT
  imported), with the existing match it collided with and the detection category.

Both are created via ``ensure_tables`` (called from ``init_db``). Module keeps to
the stdlib so it is safe to import early.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

# Detection categories (order = display priority in the panel)
DETECTIONS = ("exact_hash", "perceptual", "clip_similarity", "deleted_registry")
DETECTION_LABELS = {
    "exact_hash": "Hash idêntico (cópia exata)",
    "perceptual": "Quase idêntica (perceptual hash)",
    "clip_similarity": "Visualmente parecida (CLIP)",
    "deleted_registry": "Já estava nos registros de deletados",
}
RESOLUTIONS = ("ignored", "trashed", "imported")


def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '[]',
            settings TEXT NOT NULL DEFAULT '{}',
            total INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            imported INTEGER NOT NULL DEFAULT 0,
            quarantined INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            candidate_path TEXT NOT NULL,
            candidate_hash TEXT,
            candidate_phash TEXT,
            candidate_thumb BLOB,
            detection TEXT NOT NULL,
            match_meme_id INTEGER,
            match_deleted_id INTEGER,
            score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            resolution TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            resolved_at TEXT
        )
        """
    )
    # UNIQUE on candidate_path makes re-queueing idempotent → safe resume.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_import_review_path ON import_review(candidate_path)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_import_review_status ON import_review(status)"
    )
    # Fast "have I seen this file before?" ledger. Keyed by path; the (size, mtime)
    # signature is a stat-only quick check (no read), so re-importing tens of
    # thousands of unchanged files costs one stat() each instead of a full SHA-256.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_ledger (
            path TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT 0,
            mtime INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT,
            outcome TEXT NOT NULL DEFAULT '',
            detection TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_import_ledger_hash ON import_ledger(content_hash)"
    )


# ── Job state ──────────────────────────────────────────────────────────────────


def create_job(conn: sqlite3.Connection, job_id: str, source: str, settings: str) -> None:
    ensure_tables(conn)
    ts = now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO import_jobs "
        "(id, status, source, settings, created_at, updated_at) "
        "VALUES (?, 'queued', ?, ?, ?, ?)",
        (job_id, source, settings, ts, ts),
    )
    conn.commit()


def update_job(conn: sqlite3.Connection, job_id: str, **fields) -> None:
    """Update mutable job fields and commit. Unknown keys are ignored."""
    allowed = {
        "status", "total", "done", "imported", "quarantined",
        "message", "error_message", "finished_at",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    sets["updated_at"] = now_iso()
    assignments = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(
        f"UPDATE import_jobs SET {assignments} WHERE id = ?",
        (*sets.values(), job_id),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    ensure_tables(conn)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def latest_job(conn: sqlite3.Connection) -> dict | None:
    ensure_tables(conn)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM import_jobs ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def unfinished_jobs(conn: sqlite3.Connection) -> list[dict]:
    """Jobs left in a running/queued state — candidates for auto-resume."""
    ensure_tables(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM import_jobs WHERE status IN ('running', 'queued', 'interrupted') "
        "ORDER BY created_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


# ── Quarantine queue ───────────────────────────────────────────────────────────


def quarantine_candidate(
    conn: sqlite3.Connection,
    *,
    job_id: str | None,
    candidate_path: str,
    candidate_hash: str | None,
    candidate_phash: str | None,
    candidate_thumb: bytes | None,
    detection: str,
    match_meme_id: int | None = None,
    match_deleted_id: int | None = None,
    score: float = 0.0,
) -> None:
    """Record a suspected duplicate. Idempotent on candidate_path (safe on resume)."""
    conn.execute(
        "INSERT OR IGNORE INTO import_review "
        "(job_id, candidate_path, candidate_hash, candidate_phash, candidate_thumb, "
        " detection, match_meme_id, match_deleted_id, score, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (
            job_id, candidate_path, candidate_hash, candidate_phash, candidate_thumb,
            detection, match_meme_id, match_deleted_id, float(score), now_iso(),
        ),
    )


def pending_paths(conn: sqlite3.Connection) -> set[str]:
    """Candidate paths already queued — skip them on resume so we don't re-examine."""
    try:
        ensure_tables(conn)
        return {
            row[0]
            for row in conn.execute(
                "SELECT candidate_path FROM import_review WHERE status = 'pending'"
            )
        }
    except sqlite3.OperationalError:
        return set()


def pending_hashes(conn: sqlite3.Connection) -> set[str]:
    try:
        ensure_tables(conn)
        return {
            row[0]
            for row in conn.execute(
                "SELECT candidate_hash FROM import_review "
                "WHERE status = 'pending' AND candidate_hash IS NOT NULL"
            )
        }
    except sqlite3.OperationalError:
        return set()


def counts_by_detection(conn: sqlite3.Connection) -> dict[str, int]:
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT detection, COUNT(*) FROM import_review WHERE status = 'pending' "
        "GROUP BY detection"
    ).fetchall()
    return {detection: int(count) for detection, count in rows}


def list_items(
    conn: sqlite3.Connection,
    *,
    detection: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    ensure_tables(conn)
    conn.row_factory = sqlite3.Row
    where = "status = 'pending'"
    params: list = []
    if detection:
        where += " AND detection = ?"
        params.append(detection)
    params.extend([int(limit), int(offset)])
    rows = conn.execute(
        "SELECT id, job_id, candidate_path, candidate_hash, candidate_phash, "
        "       detection, match_meme_id, match_deleted_id, score, "
        "       (candidate_thumb IS NOT NULL) AS has_thumb, created_at "
        f"FROM import_review WHERE {where} ORDER BY detection, id LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_thumb(conn: sqlite3.Connection, item_id: int) -> bytes | None:
    row = conn.execute(
        "SELECT candidate_thumb FROM import_review WHERE id = ?", (item_id,)
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def items_for_ids(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, candidate_path, candidate_hash, detection "
        f"FROM import_review WHERE id IN ({placeholders}) AND status = 'pending'",
        ids,
    ).fetchall()
    return [dict(r) for r in rows]


def ids_for_detection(conn: sqlite3.Connection, detection: str) -> list[int]:
    return [
        int(row[0])
        for row in conn.execute(
            "SELECT id FROM import_review WHERE status = 'pending' AND detection = ?",
            (detection,),
        )
    ]


def mark_resolved(conn: sqlite3.Connection, ids: list[int], resolution: str) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE import_review SET status = 'resolved', resolution = ?, resolved_at = ? "
        f"WHERE id IN ({placeholders}) AND status = 'pending'",
        (resolution, now_iso(), *ids),
    )
    conn.commit()
    return cur.rowcount


# ── Fast file ledger ────────────────────────────────────────────────────────────
# Records the outcome of every file the importer has already looked at, so a later
# re-import of the same paths skips them with a single stat() (no read, no hashing).

LEDGER_OUTCOMES = ("imported", "ignored", "trashed", "quarantined")


def ledger_lookup(conn: sqlite3.Connection, path: str) -> dict | None:
    """Return the ledger row for ``path`` (or None). Cheap point lookup on the PK."""
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT path, name, size, mtime, content_hash, outcome, detection "
            "FROM import_ledger WHERE path = ?",
            (path,),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def ledger_record(
    conn: sqlite3.Connection,
    *,
    path: str,
    name: str,
    size: int,
    mtime: float,
    content_hash: str | None,
    outcome: str,
    detection: str = "",
) -> None:
    """Upsert a file's outcome. Does NOT commit — caller batches the commit."""
    conn.execute(
        "INSERT OR REPLACE INTO import_ledger "
        "(path, name, size, mtime, content_hash, outcome, detection, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (path, name, int(size), int(mtime), content_hash, outcome, detection, now_iso()),
    )


def ledger_mark_paths(
    conn: sqlite3.Connection, paths: list[str], outcome: str
) -> None:
    """Record an outcome decided outside the indexer (review-panel resolve actions).

    Stats each path so the (size, mtime) quick-check stays valid next import. Missing
    files (e.g. trashed/unmounted) are still recorded by path so they are remembered.
    """
    if not paths:
        return
    import os as _os

    ts = now_iso()
    rows = []
    for p in paths:
        try:
            st = _os.stat(p)
            size, mtime = int(st.st_size), int(st.st_mtime)
        except OSError:
            size, mtime = 0, 0
        rows.append((p, _os.path.basename(p), size, mtime, outcome, ts))
    conn.executemany(
        "INSERT INTO import_ledger (path, name, size, mtime, outcome, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "name=excluded.name, size=excluded.size, mtime=excluded.mtime, "
        "outcome=excluded.outcome, updated_at=excluded.updated_at",
        rows,
    )
    conn.commit()
