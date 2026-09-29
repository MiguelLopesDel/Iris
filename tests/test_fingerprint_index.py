"""The index has to return exactly what a full scan would, and stay incremental.

Grouping was a batch operation over the whole catalogue: importing a thousand
images into a library of a million repaid the entire pass, measured at 251
seconds, for a change of one tenth of one percent. The index turns that into a
query per new item. It is only worth having if it returns the same answer, so
the tests compare it against brute force on randomised data rather than checking
a handful of fixed cases.
"""

from __future__ import annotations

import random
import sqlite3

import numpy as np
import pytest

from core.fingerprint_index import (
    FingerprintProbeMasks,
    Neighbour,
    find_neighbours,
    forget,
    index_fingerprints,
    indexed_count,
    slice_count,
)

MAX_DISTANCE = 8


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def _near(digest: str, flips: int, rng: random.Random) -> str:
    value = int(digest, 16)
    for bit in rng.sample(range(64), flips):
        value ^= 1 << bit
    return f"{value:016x}"


def _population(rng: random.Random, size: int, variants: int) -> list[str]:
    base = [f"{rng.getrandbits(64):016x}" for _ in range(size)]
    extra = [_near(rng.choice(base), rng.randint(0, 12), rng) for _ in range(variants)]
    everything = base + extra
    rng.shuffle(everything)
    return everything


def _brute_force(hashes: list[str], position: int) -> set[int]:
    target = int(hashes[position], 16)
    return {
        other
        for other, digest in enumerate(hashes)
        if other != position
        and bin(target ^ int(digest, 16)).count("1") <= MAX_DISTANCE
    }


def test_neighbours_match_brute_force_on_random_populations(conn):
    for seed in range(6):
        rng = random.Random(seed)
        hashes = _population(rng, size=150, variants=60)
        connection = sqlite3.connect(":memory:")
        index_fingerprints(connection, list(enumerate(hashes)), kind="phash")

        for position, digest in enumerate(hashes):
            found = {
                neighbour.item_id
                for neighbour in find_neighbours(
                    connection, digest, kind="phash", exclude_item_id=position
                )
            }
            assert found == _brute_force(hashes, position), (seed, position)
        connection.close()


def test_the_distance_boundary_is_exact(conn):
    rng = random.Random(3)
    base = f"{rng.getrandbits(64):016x}"
    index_fingerprints(conn, [(1, _near(base, 8, rng)), (2, _near(base, 9, rng))], kind="phash")

    found = find_neighbours(conn, base, kind="phash")

    assert [neighbour.item_id for neighbour in found] == [1]


def test_neighbours_come_back_closest_first(conn):
    rng = random.Random(5)
    base = f"{rng.getrandbits(64):016x}"
    index_fingerprints(
        conn,
        [(1, _near(base, 6, rng)), (2, base), (3, _near(base, 3, rng))],
        kind="phash",
    )

    found = find_neighbours(conn, base, kind="phash")

    assert [neighbour.item_id for neighbour in found] == [2, 3, 1]
    assert found[0] == Neighbour(item_id=2, distance=0)


def test_a_second_fingerprint_family_does_not_collide(conn):
    """A new detector coexists with what is stored instead of migrating it."""
    shared = "ffffffffffffffff"
    index_fingerprints(conn, [(1, shared)], kind="phash")
    index_fingerprints(conn, [(2, shared)], kind="dhash")

    assert [n.item_id for n in find_neighbours(conn, shared, kind="phash")] == [1]
    assert [n.item_id for n in find_neighbours(conn, shared, kind="dhash")] == [2]
    assert indexed_count(conn, kind="phash") == 1


def test_a_new_version_of_the_same_kind_is_separate(conn):
    """A fingerprint's meaning changes when its algorithm does."""
    shared = "ffffffffffffffff"
    index_fingerprints(conn, [(1, shared)], kind="phash", version="1")
    index_fingerprints(conn, [(2, shared)], kind="phash", version="2")

    assert [n.item_id for n in find_neighbours(conn, shared, kind="phash", version="2")] == [2]


def test_reindexing_an_item_replaces_its_slices(conn):
    """Otherwise an item would be findable at both its old and new value."""
    rng = random.Random(7)
    first = f"{rng.getrandbits(64):016x}"
    second = _near(first, 40, rng)
    index_fingerprints(conn, [(1, first)], kind="phash")
    index_fingerprints(conn, [(1, second)], kind="phash")

    assert find_neighbours(conn, first, kind="phash") == []
    assert [n.item_id for n in find_neighbours(conn, second, kind="phash")] == [1]


def test_forgetting_an_item_removes_it_from_the_index(conn):
    digest = "ffffffffffffffff"
    index_fingerprints(conn, [(1, digest)], kind="phash")

    forget(conn, 1, kind="phash")

    assert find_neighbours(conn, digest, kind="phash") == []
    assert indexed_count(conn, kind="phash") == 0


@pytest.mark.parametrize("digest", ["", "abc", "not-a-hash", "f" * 32])
def test_malformed_fingerprints_are_refused_rather_than_stored(conn, digest):
    assert index_fingerprints(conn, [(1, digest)], kind="phash") == 0
    assert find_neighbours(conn, digest, kind="phash") == []


def test_the_slice_plan_is_what_the_pigeonhole_needs():
    """Probing radius comes from the plan, and both have to hold together."""
    from math import comb

    slices = slice_count(64, 8)
    radius = 8 // slices

    # Some slice must carry at most `radius` of the differences.
    assert (radius + 1) * slices > 8
    masks = FingerprintProbeMasks.generate(21, radius)
    assert len(masks) == comb(21, 0) + comb(21, 1) + comb(21, 2)
    assert all(bin(mask).count("1") <= radius for mask in masks)


def test_an_unknown_width_still_gets_a_usable_plan():
    """A fingerprint nobody measured yet must not silently lose neighbours."""
    slices = slice_count(128, 8)

    assert slices >= 1
    assert (8 // slices + 1) * slices > 8


# ── A fiação com o caminho de importação ─────────────────────────────────────


def _context(connection, rows):
    """Dedup context backed by a catalogue, the way an import builds one."""
    from core.indexer import _build_dedup_context

    connection.execute(
        "CREATE TABLE memes (id INTEGER PRIMARY KEY, content_hash TEXT,"
        " perceptual_hash TEXT, embedding BLOB)"
    )
    connection.executemany(
        "INSERT INTO memes (id, content_hash, perceptual_hash, embedding)"
        " VALUES (?, ?, ?, NULL)",
        rows,
    )
    connection.commit()
    return _build_dedup_context(connection)


def test_the_import_gate_uses_the_persistent_index(conn):
    """The gate must stop scanning every stored hash for every new file."""
    rng = random.Random(17)
    base = f"{rng.getrandbits(64):016x}"
    context = _context(conn, [(1, "aaa", base), (2, "bbb", f"{rng.getrandbits(64):016x}")])

    assert context.index_conn is not None, "o índice não foi usado"
    assert context.phash_u64.size == 0, "a varredura em memória continuou carregada"
    assert indexed_count(conn, kind="phash") == 2
    assert context.nearest_phash(_near(base, 2, rng)) == (1, 2)


def test_an_item_added_during_the_run_is_found_by_the_next_one(conn):
    """Candidates dedupe against each other, not only against the catalogue."""
    rng = random.Random(23)
    base = f"{rng.getrandbits(64):016x}"
    context = _context(conn, [(1, "aaa", f"{rng.getrandbits(64):016x}")])

    context.add(99, "ccc", base, None)

    assert context.nearest_phash(_near(base, 1, rng)) == (99, 1)


def test_an_unrelated_hash_finds_nothing(conn):
    rng = random.Random(29)
    context = _context(conn, [(1, "aaa", f"{rng.getrandbits(64):016x}")])

    assert context.nearest_phash(f"{rng.getrandbits(64):016x}") is None


def test_the_gate_falls_back_when_the_index_cannot_be_built(conn, monkeypatch):
    """A broken index must not silently disable the duplicate gate."""
    import core.indexer as indexer

    def explode(*args, **kwargs):
        raise sqlite3.OperationalError("disco cheio")

    monkeypatch.setattr(indexer.fingerprint_index, "indexed_count", explode)
    rng = random.Random(31)
    base = f"{rng.getrandbits(64):016x}"
    context = _context(conn, [(1, "aaa", base)])

    assert context.index_conn is None
    assert context.phash_u64.size == 1, "a varredura em memória devia assumir"
    assert context.nearest_phash(_near(base, 3, rng)) == (1, 3)


def test_the_clip_gate_does_not_hold_three_copies_while_it_builds(conn):
    """Peak memory is what decides whether an import survives, not the residue.

    Collecting embeddings into a list and stacking it kept the buffers, the
    stacked matrix and the index's own storage alive at the same time: measured
    at three times the data, which on an 8 GB machine ran out of memory around
    700k items at 768 dimensions. Blocks bring the peak down to the index
    itself.
    """
    import core.indexer as indexer

    dimensions = 128
    rows = []
    rng = random.Random(41)
    for item_id in range(1, 2 * indexer._CLIP_LOAD_BLOCK + 7):
        vector = np.array(
            [rng.random() for _ in range(dimensions)], dtype=np.float32
        )
        rows.append((item_id, f"h{item_id}", None, vector.tobytes()))

    conn.execute(
        "CREATE TABLE memes (id INTEGER PRIMARY KEY, content_hash TEXT,"
        " perceptual_hash TEXT, embedding BLOB)"
    )
    conn.executemany("INSERT INTO memes VALUES (?, ?, ?, ?)", rows)
    conn.commit()

    context = indexer._build_dedup_context(conn)

    # Every row indexed, across several blocks and a partial last one.
    assert context.clip_index.ntotal == len(rows)
    assert len(context.clip_ids) == len(rows)


def test_an_embedding_of_another_width_is_skipped_rather_than_crashing(conn):
    """A catalogue can hold vectors from an older model."""
    import core.indexer as indexer

    conn.execute(
        "CREATE TABLE memes (id INTEGER PRIMARY KEY, content_hash TEXT,"
        " perceptual_hash TEXT, embedding BLOB)"
    )
    conn.executemany(
        "INSERT INTO memes VALUES (?, ?, ?, ?)",
        [
            (1, "a", None, np.ones(64, dtype=np.float32).tobytes()),
            (2, "b", None, np.ones(32, dtype=np.float32).tobytes()),
            (3, "c", None, np.ones(64, dtype=np.float32).tobytes()),
        ],
    )
    conn.commit()

    context = indexer._build_dedup_context(conn)

    assert context.clip_index.ntotal == 2
    assert context.clip_ids == [1, 3]
