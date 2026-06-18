"""Schema regression tests + an opt-in end-to-end dedup pipeline test.

The schema tests are fast (no models) and guard the two fixed bugs:
- create/rebuild/migrate must agree on the canonical `memes` columns (single source
  of truth) so a fresh DB is never missing columns the INSERT writes;
- the legacy UNIQUE(arquivo) rebuild must preserve data and reach the same schema.

``test_dedup_pipeline_*`` runs the real indexer (CLIP/EasyOCR on GPU) and is skipped
unless IRIS_INTEGRATION=1, since it loads several GB of models.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from core import import_review
from core.indexer_db import _MEMES_COLUMNS, init_db

CANONICAL = {name for name, _ in _MEMES_COLUMNS} | {"id"}
# Columns the indexer's INSERT writes — all must exist on a fresh DB.
INSERT_COLUMNS = {
    "arquivo", "caminho", "relative_path", "storage_path", "source_path", "library_id",
    "imported_at", "file_size", "file_mtime", "texto_extraido", "descricao_ia", "tags",
    "content_hash", "ocr_normalized", "visual_json", "objects", "style", "source_work",
    "humor", "context", "error_message", "model_name", "embedding_dim", "schema_version",
    "embedding", "desc_embedding", "audio_fingerprint", "audio_embedding", "perceptual_hash",
    "metadata_json",
}


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(memes)")}


def _memes_sql(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT sql FROM sqlite_master WHERE name='memes'").fetchone()[0]


def test_fresh_db_matches_canonical_schema(tmp_path):
    conn = init_db(tmp_path / "fresh.db")
    cols = _columns(conn)
    assert cols == CANONICAL  # no missing, no extra
    assert INSERT_COLUMNS <= cols  # every INSERT target exists
    assert "UNIQUE" not in _memes_sql(conn)  # arquivo is not unique


def _media_collections_cols(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(media_collections)")}


def test_fresh_db_allows_adding_to_collection(tmp_path):
    # Regression: media_collections must carry `added_at` (every insert site writes it),
    # otherwise adding records to a collection fails on a brand-new DB.
    conn = init_db(tmp_path / "fresh.db")
    assert "added_at" in _media_collections_cols(conn)
    conn.execute("INSERT INTO collections (name, description, created_at) VALUES ('c', '', 'now')")
    conn.execute("INSERT INTO memes (arquivo) VALUES ('a.png')")
    conn.execute(
        "INSERT INTO media_collections (meme_id, collection_id, added_at) VALUES (1, 1, 'now')"
    )  # must not raise
    conn.commit()


def test_media_collections_added_at_backfilled(tmp_path):
    db = tmp_path / "old.db"
    legacy = sqlite3.connect(db)
    legacy.execute(
        "CREATE TABLE media_collections (meme_id INTEGER, collection_id INTEGER, "
        "PRIMARY KEY (meme_id, collection_id))"
    )
    legacy.commit()
    legacy.close()
    conn = init_db(db)
    assert "added_at" in _media_collections_cols(conn)


def test_legacy_unique_db_migrates_and_preserves_data(tmp_path):
    db = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db)
    legacy.execute(
        "CREATE TABLE memes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "arquivo TEXT UNIQUE, caminho TEXT, content_hash TEXT)"
    )
    legacy.execute(
        "INSERT INTO memes (arquivo, caminho, content_hash) VALUES ('a.png', '/a.png', 'HASH')"
    )
    legacy.commit()
    legacy.close()

    conn = init_db(db)
    assert CANONICAL <= _columns(conn)  # gained every canonical column
    assert "UNIQUE" not in _memes_sql(conn)  # legacy constraint dropped
    row = conn.execute("SELECT arquivo, content_hash FROM memes").fetchone()
    assert tuple(row) == ("a.png", "HASH")  # data survived the rebuild


def test_create_and_rebuild_share_one_definition(tmp_path):
    # Fresh (via create) and rebuilt (via legacy path) DBs must end identical.
    fresh = _columns(init_db(tmp_path / "a.db"))

    db = tmp_path / "b.db"
    legacy = sqlite3.connect(db)
    legacy.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY AUTOINCREMENT, arquivo TEXT UNIQUE)")
    legacy.commit()
    legacy.close()
    rebuilt = _columns(init_db(db))

    assert fresh == rebuilt == CANONICAL


# ── Opt-in end-to-end pipeline (real models) ────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("IRIS_INTEGRATION") != "1",
    reason="carrega modelos (CLIP/EasyOCR); rode com IRIS_INTEGRATION=1",
)
def test_dedup_pipeline_quarantines_duplicates(tmp_path):
    from PIL import Image, ImageDraw

    from core.indexer import IndexerConfig, process_images, resolve_device

    def make_image(path: Path, seed: int) -> None:
        # Rich, seed-distinct content so different seeds are far apart in phash/CLIP.
        img = Image.new("RGB", (256, 256), (seed * 7 % 255, seed * 13 % 255, seed * 29 % 255))
        d = ImageDraw.Draw(img)
        for i in range(0, 256, 16):
            d.line([(i, 0), (255, i)], fill=(seed * 3 % 255, 200, i % 255), width=3)
        d.ellipse([40, 40, 200, 200], outline=(255, 255, 255), width=5)
        d.text((50, 110), f"PATTERN-{seed}", fill=(255, 255, 0))
        img.save(path)

    def cfg(media_dir: Path, db: Path) -> IndexerConfig:
        # batch_size=2 → the first incoming batch [alpha_copy, alpha_reenc] is entirely
        # duplicates (empty batch_images), exercising the commit-flush-on-dupe-batch path.
        return IndexerConfig(
            media_dir=media_dir, db_path=db,
            model_name="sentence-transformers/clip-ViT-L-14", batch_size=2,
            device=resolve_device("auto"), recursive=False, limit=None,
            rebuild_faiss_only=False, caption_model="none", whisper_model="tiny",
            sample_manifest=None, library_name="default",
            library_root=tmp_path / "lib", copy_to_library=True,
        )

    db = tmp_path / "e2e.db"
    orig = tmp_path / "orig"
    incoming = tmp_path / "incoming"
    orig.mkdir()
    incoming.mkdir()

    make_image(orig / "alpha.png", 1)
    make_image(orig / "gamma.png", 99)
    r1 = process_images(cfg(orig, db))
    assert r1["imported"] == 2 and r1["quarantined"] == 0

    import shutil
    shutil.copy2(orig / "alpha.png", incoming / "alpha_copy.png")           # exact hash
    Image.open(orig / "alpha.png").convert("RGB").save(
        incoming / "alpha_reenc.jpg", quality=92                            # near-dup (phash)
    )
    make_image(incoming / "delta.png", 250)                                 # genuinely new
    r2 = process_images(cfg(incoming, db))

    conn = sqlite3.connect(db)
    counts = import_review.counts_by_detection(conn)
    arquivos = {row[0] for row in conn.execute("SELECT arquivo FROM memes")}
    conn.close()

    assert counts.get("exact_hash", 0) == 1   # alpha_copy quarantined
    assert counts.get("perceptual", 0) == 1   # alpha_reenc quarantined
    assert r2["imported"] == 1                 # only delta indexed
    assert "delta.png" in arquivos and "alpha_copy.png" not in arquivos

    # Re-importing the same folder must be near-instant for already-handled files.
    # delta was imported on the previous pass; the fast ledger recognises it by a
    # single stat() and skips it, so it is neither re-hashed nor re-quarantined.
    r3 = process_images(cfg(incoming, db))
    assert r3["skipped"] >= 1   # delta short-circuited by the ledger
    assert r3["imported"] == 0
    conn = sqlite3.connect(db)
    counts_after = import_review.counts_by_detection(conn)
    conn.close()
    assert counts_after == counts  # no new quarantine rows on the second pass
