"""Unit tests for core/backup.py — catalog snapshots, restore, retention, media.

No models/torch: builds a realistic memes DB via init_db and tiny fake embeddings.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tarfile

import numpy as np

from core import backup
from core.indexer_db import init_db, now_iso


def _make_db(path, rows):
    """Create a DB with `rows` = list of (arquivo, storage_path, content_hash, size)."""
    conn = init_db(path)
    for arquivo, storage_path, chash, size in rows:
        emb = np.zeros(4, dtype=np.float32).tobytes()
        conn.execute(
            "INSERT INTO memes (arquivo, storage_path, content_hash, file_size, "
            "embedding, desc_embedding, embedding_dim, schema_version, model_name, imported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 4, 4, 'clip', ?)",
            (arquivo, storage_path, chash, size, emb, emb, now_iso()),
        )
    conn.commit()
    conn.close()


def test_snapshot_written_outside_data_dir(tmp_path):
    data = tmp_path / "data"
    dest = tmp_path / "external"
    data.mkdir()
    db = data / "cat.db"
    _make_db(db, [("a.png", "a.png", "h1", 10), ("b.png", "b.png", "h2", 20)])

    before = set(data.iterdir())
    info = backup.catalog_snapshot(db, dest, reason="manual")

    assert info["meme_count"] == 2
    assert info["media_count"] == 2
    archive = dest / info["id"]
    assert archive.exists() and archive.suffix == ".gz"
    # Anti-quota: nothing new written into the data dir during the snapshot.
    assert set(data.iterdir()) == before


def test_list_and_retention(tmp_path):
    db = tmp_path / "cat.db"
    dest = tmp_path / "ext"
    _make_db(db, [("a.png", "a.png", "h1", 10)])
    for r in ("one", "two", "three"):
        backup.catalog_snapshot(db, dest, reason=r)

    snaps = backup.list_snapshots(dest)
    assert len(snaps) == 3
    assert all("created_at" in s for s in snaps)

    removed = backup.apply_retention(dest, keep_last=2)
    assert len(removed) == 1
    assert len(backup.list_snapshots(dest)) == 2


def test_restore_roundtrip_rebuilds_faiss(tmp_path):
    data = tmp_path / "data"
    dest = tmp_path / "ext"
    data.mkdir()
    db = data / "cat.db"
    _make_db(db, [("a.png", "a.png", "h1", 10), ("b.png", "b.png", "h2", 20)])
    info = backup.catalog_snapshot(db, dest)

    # Mutate the live DB after the snapshot.
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM memes")
    conn.commit()
    conn.close()
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM memes").fetchone()[0] == 0

    calls = []
    backup.restore_snapshot(
        dest / info["id"], db_path=db,
        rebuild_faiss=lambda p, m: calls.append((str(p), m)), model_name="clip",
    )
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM memes").fetchone()[0] == 2
    assert calls == [(str(db), "clip")]


def test_restore_mirror_removes_orphans(tmp_path):
    data = tmp_path / "data"
    dest = tmp_path / "ext"
    data.mkdir()
    db = data / "cat.db"
    _make_db(db, [("a.png", "a.png", "h1", 10)])
    info = backup.catalog_snapshot(db, dest)
    # Orphan files that mirror restore should remove.
    (data / "other.db").write_text("x")
    (data / "other_image.faiss").write_text("x")
    (data / "cat_image.faiss").write_text("keep")  # belongs to restored db → kept

    res = backup.restore_snapshot(dest / info["id"], db_path=db, mode="mirror")
    assert "other.db" in res["removed"]
    assert not (data / "other.db").exists()
    assert (data / "cat_image.faiss").exists()  # not removed (same stem)


def test_reconcile_relinks_by_hash(tmp_path):
    data = tmp_path / "data"
    lib = data / "library"
    originals = tmp_path / "media"
    lib.mkdir(parents=True)
    originals.mkdir()
    db = data / "cat.db"

    payload = b"the real bytes of the meme"
    chash = hashlib.sha256(payload).hexdigest()
    # Original exists; the library copy is missing.
    (originals / "found.png").write_bytes(payload)
    _make_db(db, [("found.png", "found.png", chash, len(payload))])

    res = backup.reconcile_media(db, lib, originals)
    assert res["total"] == 1
    assert res["relinked"] == ["found.png"]
    assert (lib / "found.png").read_bytes() == payload

    # Second pass: now present, nothing to relink.
    res2 = backup.reconcile_media(db, lib, originals)
    assert res2["present"] == 1 and res2["relinked"] == []


def test_reconcile_reports_missing(tmp_path):
    data = tmp_path / "data"
    lib = data / "library"
    originals = tmp_path / "media"
    lib.mkdir(parents=True)
    originals.mkdir()
    db = data / "cat.db"
    _make_db(db, [("gone.png", "gone.png", "nonexistent", 5)])

    res = backup.reconcile_media(db, lib, originals)
    assert res["missing"] == ["gone.png"] and res["relinked"] == []


def test_export_media_streams_tar(tmp_path):
    lib = tmp_path / "library"
    (lib / "sub").mkdir(parents=True)
    (lib / "a.png").write_bytes(b"aaa")
    (lib / "sub" / "b.png").write_bytes(b"bbbb")
    dest = tmp_path / "ext"

    res = backup.export_media(lib, dest)
    assert res["files"] == 2
    out = dest / "iris_library__" if False else res["path"]
    with tarfile.open(out) as tar:
        names = set(tar.getnames())
    assert names == {"a.png", "sub/b.png"}
