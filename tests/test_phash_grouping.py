"""Slice-based candidate selection must group exactly what all-pairs did.

`phash_groups` compared every pair of perceptual hashes, which grows with the
square of the library: 2.7 s for the 3.6k hashes in the real catalog, minutes at
the 25k-per-user target in the sync design. Candidates now come from matching
bit slices.

That optimisation is only worth anything if it changes no result, so the tests
below compare it against a brute-force implementation on randomised data rather
than checking a handful of fixed cases.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from core.duplicates import _phash_linking_pairs, phash_groups

MAX_DISTANCE = 8


@dataclass
class _Record:
    arquivo: str
    perceptual_hash: str
    content_hash: str = ""
    resolved_path: str | None = None


def _brute_force_groups(
    hashes: list[str], max_distance: int = MAX_DISTANCE
) -> set[frozenset[int]]:
    """Groups built by comparing literally every pair, the definition to match."""
    parent = list(range(len(hashes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            if bin(int(hashes[i], 16) ^ int(hashes[j], 16)).count("1") <= max_distance:
                parent[find(i)] = find(j)

    members: dict[int, set[int]] = {}
    for position in range(len(hashes)):
        members.setdefault(find(position), set()).add(position)
    return {frozenset(group) for group in members.values() if len(group) >= 2}


def _groups_from_pairs(hashes: list[str]) -> set[frozenset[int]]:
    """The same groups, built from the edges the implementation returns."""
    parent = list(range(len(hashes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for left, right in _phash_linking_pairs(hashes, MAX_DISTANCE):
        parent[find(left)] = find(right)

    members: dict[int, set[int]] = {}
    for position in range(len(hashes)):
        members.setdefault(find(position), set()).add(position)
    return {frozenset(group) for group in members.values() if len(group) >= 2}


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


def test_pairs_match_brute_force_on_random_populations():
    for seed in range(12):
        rng = random.Random(seed)
        hashes = _population(rng, size=120, variants=40)

        assert _groups_from_pairs(hashes) == _brute_force_groups(hashes), seed


def test_the_distance_boundary_is_exact():
    """Eight differing bits group, nine do not — the slices must not blur that."""
    rng = random.Random(99)
    base = f"{rng.getrandbits(64):016x}"

    at_limit = _near(base, 8, rng)
    assert _groups_from_pairs([base, at_limit]) == {frozenset({0, 1})}

    beyond = _near(base, 9, rng)
    assert _groups_from_pairs([base, beyond]) == set()


def test_a_chain_of_near_copies_becomes_one_group():
    """Union-find still merges transitively, even across different slices."""
    rng = random.Random(5)
    first = f"{rng.getrandbits(64):016x}"
    second = _near(first, 8, rng)
    third = _near(second, 8, rng)
    records = [
        _Record("a.jpg", first),
        _Record("b.jpg", second),
        _Record("c.jpg", third),
    ]

    groups = phash_groups(records)

    assert len(groups) == 1
    assert sorted(next(iter(groups.values()))) == [0, 1, 2]


def test_identical_hashes_group_and_unrelated_ones_do_not():
    rng = random.Random(11)
    shared = f"{rng.getrandbits(64):016x}"
    records = [
        _Record("a.jpg", shared),
        _Record("b.jpg", shared),
        _Record("c.jpg", f"{rng.getrandbits(64):016x}"),
    ]

    groups = phash_groups(records)

    assert len(groups) == 1
    assert sorted(next(iter(groups.values()))) == [0, 1]


def test_videos_and_audio_are_left_to_the_other_detectors():
    shared = "ffffffffffffffff"
    records = [
        _Record("a.mp4", shared),
        _Record("b.mp4", shared),
        _Record("c.mp3", shared),
    ]

    assert phash_groups(records) == {}


def test_hashes_that_are_not_full_width_are_ignored():
    """Comparing hashes of different bit widths is meaningless.

    The previous implementation widened a short hex string with leading zeros,
    so "1" and "0000000000000001" landed in the same group.
    """
    records = [
        _Record("a.jpg", "1"),
        _Record("b.jpg", "0000000000000001"),
        _Record("c.jpg", "not a hash"),
        _Record("d.jpg", ""),
    ]

    assert phash_groups(records) == {}


def test_a_single_hash_cannot_form_a_group():
    assert phash_groups([_Record("a.jpg", "0123456789abcdef")]) == {}


def test_slices_cover_every_bit_exactly_once():
    """The pigeonhole argument only holds if the slices partition the hash."""
    from core.duplicates import _PHASH_BITS, _PHASH_SLICES

    edges = [(i * _PHASH_BITS) // _PHASH_SLICES for i in range(_PHASH_SLICES + 1)]

    assert edges[0] == 0
    assert edges[-1] == _PHASH_BITS
    assert all(edges[i] < edges[i + 1] for i in range(_PHASH_SLICES))


def test_probe_masks_flip_no_more_than_the_radius():
    """Probing further than the per-slice share would be wasted work.

    Probing less would miss pairs, which the brute-force comparisons above
    would catch; this pins the other side.
    """
    from math import comb

    from core.fingerprint_index import FingerprintProbeMasks

    masks = FingerprintProbeMasks.generate(width=21, radius=2)

    assert len(masks) == comb(21, 0) + comb(21, 1) + comb(21, 2)
    assert len(set(masks)) == len(masks)
    assert all(bin(mask).count("1") <= 2 for mask in masks)
    assert all(mask < (1 << 21) for mask in masks)


def test_the_per_slice_radius_is_what_the_pigeonhole_allows():
    """max_distance // slices is the largest share a slice can be forced to hold."""
    from core.duplicates import _PHASH_MAX_DISTANCE, _PHASH_SLICES

    radius = _PHASH_MAX_DISTANCE // _PHASH_SLICES

    # If every slice held more than `radius` differences, the total would
    # exceed the distance limit -- so some slice always holds at most `radius`.
    assert (radius + 1) * _PHASH_SLICES > _PHASH_MAX_DISTANCE


def test_larger_population_still_matches_brute_force():
    """Small populations can hide a bucketing mistake that volume reveals."""
    rng = random.Random(2024)
    hashes = _population(rng, size=1200, variants=300)

    assert _groups_from_pairs(hashes) == _brute_force_groups(hashes)


def test_many_copies_of_one_hash_do_not_explode_into_every_pair():
    """Blank screenshots and solid-colour images all share one hash.

    Union-find needs edges that connect a component, not every edge inside it.
    Emitting all of them made a group of n identical hashes cost n squared:
    5,000 such images produced 12.5 million pairs and took 13 seconds.
    """
    hashes = ["0000000000000000"] * 400 + ["ffffffffffffffff"]

    pairs = _phash_linking_pairs(hashes, MAX_DISTANCE)

    assert len(pairs) == 399, "voltou a materializar todos os pares do grupo"
    assert _groups_from_pairs(hashes) == {frozenset(range(400))}


def test_duplicate_hashes_still_merge_with_their_near_copies():
    """Collapsing equal hashes must not detach them from neighbouring groups."""
    rng = random.Random(31)
    shared = f"{rng.getrandbits(64):016x}"
    neighbour = _near(shared, 3, rng)
    hashes = [shared, shared, shared, neighbour]

    assert _groups_from_pairs(hashes) == _brute_force_groups(hashes)
    assert _groups_from_pairs(hashes) == {frozenset({0, 1, 2, 3})}


def test_a_large_identical_group_does_not_expand_into_every_pair():
    """The quadratic expansion removed from grouping must not return upstream.

    find_duplicate_groups used to write every pair inside a group: 5,000 copies
    of one file produced 12.5 million entries and about a gigabyte of
    dictionary, enough to exhaust a small machine. Only the pairs involving the
    anchor are ever read, so membership answers the same question.
    """
    import tracemalloc
    from types import SimpleNamespace

    from core.duplicates import find_duplicate_groups

    size = 3000
    records = [
        _Record(f"{i}.jpg", "0123456789abcdef")
        for i in range(size)
    ]
    for record in records:
        record.content_hash = "same"
        record.resolved_path = None
    engine = SimpleNamespace(records=records, image_matrix=None)

    tracemalloc.start()
    find_duplicate_groups(engine, require_existing_files=False)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # A pair per couple would need hundreds of megabytes at this size.
    assert peak < 50 * 1024 * 1024, f"pico de memória {peak / 1e6:.0f} MB"
