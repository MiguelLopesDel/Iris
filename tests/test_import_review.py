"""Unit tests for core/import_review.py — the dedup quarantine + job state store."""

from __future__ import annotations

import sqlite3

from core import import_review


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    import_review.ensure_tables(conn)
    return conn


def _quarantine(conn, path, *, detection="exact_hash", chash="h", match=1):
    import_review.quarantine_candidate(
        conn, job_id="job1", candidate_path=path, candidate_hash=chash,
        candidate_phash=None, candidate_thumb=None, detection=detection,
        match_meme_id=match, score=1.0,
    )
    conn.commit()


def test_ensure_tables_idempotent():
    conn = _conn()
    import_review.ensure_tables(conn)  # second call must not raise
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"import_jobs", "import_review", "import_ledger"} <= names


def test_quarantine_is_idempotent_on_path():
    conn = _conn()
    _quarantine(conn, "/a.jpg")
    _quarantine(conn, "/a.jpg")  # same path → ignored
    assert conn.execute("SELECT COUNT(*) FROM import_review").fetchone()[0] == 1


def test_pending_skip_sets():
    conn = _conn()
    _quarantine(conn, "/a.jpg", chash="aaa")
    _quarantine(conn, "/b.jpg", chash="bbb", detection="perceptual")
    assert import_review.pending_paths(conn) == {"/a.jpg", "/b.jpg"}
    assert import_review.pending_hashes(conn) == {"aaa", "bbb"}


def test_counts_and_list_and_resolve():
    conn = _conn()
    _quarantine(conn, "/a.jpg", detection="exact_hash")
    _quarantine(conn, "/b.jpg", detection="perceptual")
    _quarantine(conn, "/c.jpg", detection="perceptual")

    assert import_review.counts_by_detection(conn) == {"exact_hash": 1, "perceptual": 2}

    perceptual = import_review.list_items(conn, detection="perceptual")
    assert len(perceptual) == 2
    assert all(item["detection"] == "perceptual" for item in perceptual)
    assert all(item["has_thumb"] == 0 for item in perceptual)

    ids = import_review.ids_for_detection(conn, "perceptual")
    resolved = import_review.mark_resolved(conn, ids, "ignored")
    assert resolved == 2
    assert import_review.counts_by_detection(conn) == {"exact_hash": 1}


def test_thumb_roundtrip():
    conn = _conn()
    import_review.quarantine_candidate(
        conn, job_id=None, candidate_path="/t.jpg", candidate_hash="h",
        candidate_phash=None, candidate_thumb=b"JPEGDATA", detection="perceptual",
        match_meme_id=2, score=0.99,
    )
    conn.commit()
    item = import_review.list_items(conn)[0]
    assert item["has_thumb"] == 1
    assert import_review.get_thumb(conn, item["id"]) == b"JPEGDATA"


def test_job_lifecycle():
    conn = _conn()
    import_review.create_job(conn, "j1", '["/folder"]', "{}")
    job = import_review.get_job(conn, "j1")
    assert job["status"] == "queued"

    import_review.update_job(conn, "j1", status="running", total=10, done=3, imported=2)
    job = import_review.get_job(conn, "j1")
    assert job["status"] == "running" and job["total"] == 10 and job["imported"] == 2

    assert [j["id"] for j in import_review.unfinished_jobs(conn)] == ["j1"]
    import_review.update_job(conn, "j1", status="completed")
    assert import_review.unfinished_jobs(conn) == []
    assert import_review.latest_job(conn)["id"] == "j1"


def test_interrupted_job_is_resumable():
    # An interrupted (paused) import must remain in the auto-resume set.
    conn = _conn()
    import_review.create_job(conn, "ji", '["/mnt/x"]', "{}")
    import_review.update_job(conn, "ji", status="interrupted")
    assert [j["id"] for j in import_review.unfinished_jobs(conn)] == ["ji"]


def test_ledger_record_and_lookup_roundtrip():
    conn = _conn()
    import_review.ledger_record(
        conn, path="/m/a.jpg", name="a.jpg", size=123, mtime=999.7,
        content_hash="abc", outcome="imported",
    )
    conn.commit()
    row = import_review.ledger_lookup(conn, "/m/a.jpg")
    assert row is not None
    assert row["size"] == 123 and row["mtime"] == 999  # mtime stored as int
    assert row["content_hash"] == "abc" and row["outcome"] == "imported"
    assert import_review.ledger_lookup(conn, "/m/missing.jpg") is None


def test_ledger_record_upserts_on_path():
    conn = _conn()
    import_review.ledger_record(
        conn, path="/m/a.jpg", name="a.jpg", size=1, mtime=1,
        content_hash=None, outcome="quarantined", detection="perceptual",
    )
    import_review.ledger_record(
        conn, path="/m/a.jpg", name="a.jpg", size=2, mtime=2,
        content_hash="h", outcome="imported",
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM import_ledger").fetchone()[0] == 1
    row = import_review.ledger_lookup(conn, "/m/a.jpg")
    assert row["outcome"] == "imported" and row["size"] == 2


def test_ledger_mark_paths_records_outcome(tmp_path):
    conn = _conn()
    f = tmp_path / "x.jpg"
    f.write_bytes(b"data")
    import_review.ledger_mark_paths(conn, [str(f), "/gone/y.jpg"], "ignored")
    seen = import_review.ledger_lookup(conn, str(f))
    assert seen["outcome"] == "ignored" and seen["size"] == 4
    # A missing path is still remembered (size 0) so it won't be re-examined.
    gone = import_review.ledger_lookup(conn, "/gone/y.jpg")
    assert gone["outcome"] == "ignored" and gone["size"] == 0
