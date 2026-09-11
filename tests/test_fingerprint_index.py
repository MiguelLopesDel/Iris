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

import pytest

from core.fingerprint_index import (
    Neighbour,
    find_neighbours,
    forget,
    index_fingerprints,
    indexed_count,
    probe_masks,
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
    masks = probe_masks(21, radius)
    assert len(masks) == comb(21, 0) + comb(21, 1) + comb(21, 2)
    assert all(bin(mask).count("1") <= radius for mask in masks)


def test_an_unknown_width_still_gets_a_usable_plan():
    """A fingerprint nobody measured yet must not silently lose neighbours."""
    slices = slice_count(128, 8)

    assert slices >= 1
    assert (8 // slices + 1) * slices > 8
