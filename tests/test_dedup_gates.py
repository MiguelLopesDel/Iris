"""Tests for the import dedup gates (core.indexer._DedupContext / _build_dedup_context).

No models are loaded — we feed synthetic embeddings and perceptual hashes directly.
"""

from __future__ import annotations

import sqlite3

import numpy as np

from core.indexer import (
    _build_dedup_context,
    _hamming_to_array,
    _phash_score,
    _phash_to_u64,
)


def _emb(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _flip_bits(hex_str: str, n: int) -> str:
    """Return phash hex with the lowest n bits flipped (Hamming distance n)."""
    value = int(hex_str, 16)
    for i in range(n):
        value ^= (1 << i)
    return format(value, "016x")


def _db_with(rows, deleted=None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE memes (id INTEGER PRIMARY KEY, content_hash TEXT, "
        "perceptual_hash TEXT, embedding BLOB)"
    )
    conn.execute(
        "CREATE TABLE deleted_media (id INTEGER PRIMARY KEY, content_hash TEXT, perceptual_hash TEXT)"
    )
    for mid, chash, phash, emb in rows:
        conn.execute(
            "INSERT INTO memes (id, content_hash, perceptual_hash, embedding) VALUES (?,?,?,?)",
            (mid, chash, phash, emb),
        )
    for did, phash in (deleted or []):
        conn.execute(
            "INSERT INTO deleted_media (id, perceptual_hash) VALUES (?,?)", (did, phash)
        )
    conn.commit()
    return conn


def test_hamming_helpers():
    assert _phash_to_u64("ffffffffffffffff") == 0xFFFFFFFFFFFFFFFF
    assert _phash_to_u64("not-hex") is None
    arr = np.array([0x0, 0xF], dtype=np.uint64)
    dist = _hamming_to_array(arr, 0x0)
    assert dist.tolist() == [0, 4]
    assert _phash_score(0) == 1.0 and _phash_score(8) < 1.0


def test_exact_hash_lookup():
    conn = _db_with([(1, "abc", None, _emb([1, 0, 0, 0]))])
    ctx = _build_dedup_context(conn)
    assert ctx.hash_to_meme == {"abc": 1}


def test_nearest_phash_within_and_beyond_threshold():
    base = "ffffffffffffffff"
    conn = _db_with([(7, "h", base, _emb([1, 0, 0, 0]))])
    ctx = _build_dedup_context(conn)
    # identical phash → distance 0
    assert ctx.nearest_phash(base) == (7, 0)
    # 4 bits flipped → within threshold (≤8)
    assert ctx.nearest_phash(_flip_bits(base, 4)) == (7, 4)
    # fully inverted → distance 64 → no match
    assert ctx.nearest_phash("0000000000000000") is None


def test_nearest_deleted_phash():
    conn = _db_with([], deleted=[(3, "ffffffffffffffff")])
    ctx = _build_dedup_context(conn)
    assert ctx.nearest_deleted_phash("ffffffffffffffff") == 3
    assert ctx.nearest_deleted_phash("0000000000000000") is None


def test_nearest_clip_threshold():
    conn = _db_with([(1, "h", None, _emb([1, 0, 0, 0]))])
    ctx = _build_dedup_context(conn)
    # identical direction → cosine 1.0 ≥ 0.985
    hit = ctx.nearest_clip(np.array([1, 0, 0, 0], dtype=np.float32))
    assert hit is not None and hit[0] == 1 and hit[1] > 0.98
    # orthogonal → below threshold → None
    assert ctx.nearest_clip(np.array([0, 1, 0, 0], dtype=np.float32)) is None


def test_add_makes_new_item_detectable():
    conn = _db_with([(1, "h1", "ffffffffffffffff", _emb([1, 0, 0, 0]))])
    ctx = _build_dedup_context(conn)
    # A brand-new item inserted mid-import becomes a dedup target for later candidates.
    ctx.add(2, "h2", "0000000000000000", np.array([0, 1, 0, 0], dtype=np.float32))
    assert ctx.hash_to_meme["h2"] == 2
    assert ctx.nearest_phash("0000000000000000") == (2, 0)
    hit = ctx.nearest_clip(np.array([0, 1, 0, 0], dtype=np.float32))
    assert hit is not None and hit[0] == 2
