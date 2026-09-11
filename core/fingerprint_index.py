"""Persistent, incremental index over binary fingerprints.

The problem this exists to remove: grouping was a batch operation over the whole
catalogue. Importing a thousand images into a library of a million repaid the
entire grouping pass -- measured at 251 seconds -- for a change of one tenth of
one percent. That is wrong for a system that grows over years, and it is wrong
at every size, not only at fifty million.

The index answers one question: *which stored fingerprints are within
``max_distance`` bits of this one?* Import then becomes a query per new item
instead of a rebuild per import.

Deliberately agnostic about the fingerprint. Rows carry ``kind``, ``version``
and ``bits``, so a 256-bit hash, a second hash family, or a new version of an
existing one coexist with what is already stored instead of requiring a
migration. The 64-bit perceptual hash is the first user, not the contract:
assuming every future fingerprint fits in a ``uint64`` costs nothing today and
is expensive to undo.

**Slicing.** A fingerprint is split into ``slices`` parts. If two fingerprints
differ by at most ``max_distance`` bits in total, some slice carries at most
``max_distance // slices`` of those differences, because the differences cannot
all exceed their share. Probing every value within that small per-slice radius
therefore misses no neighbour -- that is a proof, not a measurement -- while
producing spurious candidates that the exact distance check then rejects.

**SQLite, for now.** WAL turns most of the writing into sequential appends and
lets readers continue during an import, which is what an old machine with a
spinning disk needs. A log-structured store with its own compaction would be a
small storage engine, and nothing here has yet shown SQLite to be the
bottleneck. That measurement should come before that code.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

# How a fingerprint of a given width is cut, per distance threshold. Chosen by
# measurement rather than by rule: for 64 bits at distance 8, three slices of 21
# bits probed to radius 2 is the most selective arrangement that stays cheap to
# probe -- 696 lookups per query against a candidate rate of 2.8e-4. More slices
# make the buckets too coarse; fewer make the probe count explode.
_SLICE_PLANS: dict[tuple[int, int], int] = {
    (64, 8): 3,
    (256, 18): 7,
}
_DEFAULT_SLICES = 3


@dataclass(frozen=True)
class Neighbour:
    item_id: int
    distance: int


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fingerprint_values (
            item_id INTEGER NOT NULL,
            kind    TEXT NOT NULL,
            version TEXT NOT NULL,
            bits    INTEGER NOT NULL,
            value   TEXT NOT NULL,
            PRIMARY KEY (item_id, kind, version)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fingerprint_slices (
            item_id     INTEGER NOT NULL,
            kind        TEXT NOT NULL,
            version     TEXT NOT NULL,
            slice_no    INTEGER NOT NULL,
            slice_value INTEGER NOT NULL,
            PRIMARY KEY (item_id, kind, version, slice_no)
        )"""
    )
    # The only lookup the query path performs. Column order matters: the probe
    # fixes kind, version and slice_no and varies slice_value, so slice_value
    # has to be last for the index to be usable as a range.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fingerprint_slice_lookup"
        " ON fingerprint_slices(kind, version, slice_no, slice_value)"
    )


def slice_count(bits: int, max_distance: int) -> int:
    return _SLICE_PLANS.get((bits, max_distance), _DEFAULT_SLICES)


def _edges(bits: int, slices: int) -> list[int]:
    return [(index * bits) // slices for index in range(slices + 1)]


def _slice_values(value: int, bits: int, slices: int) -> list[int]:
    edges = _edges(bits, slices)
    out = []
    for index in range(slices):
        start, end = edges[index], edges[index + 1]
        out.append((value >> (bits - end)) & ((1 << (end - start)) - 1))
    return out


def probe_masks(width: int, radius: int) -> list[int]:
    """XOR masks flipping up to ``radius`` bits of a slice of ``width`` bits."""
    masks = [0]
    for flips in range(1, radius + 1):
        for positions in combinations(range(width), flips):
            mask = 0
            for position in positions:
                mask ^= 1 << position
            masks.append(mask)
    return masks


def index_fingerprints(
    conn: sqlite3.Connection,
    entries: Iterable[tuple[int, str]],
    *,
    kind: str,
    version: str = "1",
    bits: int = 64,
    max_distance: int = 8,
) -> int:
    """Store fingerprints and their slices. Idempotent per (item, kind, version).

    Takes an iterable so an import can stream a batch into one transaction: the
    caller commits, because how often to commit is a decision about the whole
    import, not about one row.
    """
    ensure_tables(conn)
    slices = slice_count(bits, max_distance)
    values: list[tuple] = []
    slice_rows: list[tuple] = []
    for item_id, digest in entries:
        if not digest or len(digest) * 4 != bits:
            continue
        try:
            numeric = int(digest, 16)
        except ValueError:
            continue
        values.append((item_id, kind, version, bits, digest))
        slice_rows.extend(
            (item_id, kind, version, slice_no, slice_value)
            for slice_no, slice_value in enumerate(_slice_values(numeric, bits, slices))
        )
    if not values:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO fingerprint_values"
        " (item_id, kind, version, bits, value) VALUES (?, ?, ?, ?, ?)",
        values,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO fingerprint_slices"
        " (item_id, kind, version, slice_no, slice_value) VALUES (?, ?, ?, ?, ?)",
        slice_rows,
    )
    return len(values)


def find_neighbours(
    conn: sqlite3.Connection,
    digest: str,
    *,
    kind: str,
    version: str = "1",
    bits: int = 64,
    max_distance: int = 8,
    exclude_item_id: int | None = None,
) -> list[Neighbour]:
    """Stored fingerprints within ``max_distance`` bits of ``digest``.

    Exact within the predicate: the slicing loses no neighbour inside the
    radius, and every candidate it proposes is checked with a real distance
    before being returned. Exact about the fingerprints, which is not the same
    as exact about the images -- two photographs can sit within eight bits of
    each other and not be the same picture.
    """
    ensure_tables(conn)
    if not digest or len(digest) * 4 != bits:
        return []
    try:
        numeric = int(digest, 16)
    except ValueError:
        return []

    slices = slice_count(bits, max_distance)
    radius = max_distance // slices
    edges = _edges(bits, slices)
    candidates: set[int] = set()

    for slice_no, slice_value in enumerate(_slice_values(numeric, bits, slices)):
        width = edges[slice_no + 1] - edges[slice_no]
        wanted = [slice_value ^ mask for mask in probe_masks(width, radius)]
        # One statement per slice instead of one per probe: 232 round trips
        # through the SQLite layer per slice was the dominant cost, and the
        # planner handles the IN list against the covering index.
        placeholders = ",".join("?" for _ in wanted)
        rows = conn.execute(
            "SELECT item_id FROM fingerprint_slices"  # noqa: S608 - placeholders only
            f" WHERE kind = ? AND version = ? AND slice_no = ? AND slice_value IN ({placeholders})",
            (kind, version, slice_no, *wanted),
        ).fetchall()
        candidates.update(int(row[0]) for row in rows)

    candidates.discard(exclude_item_id)
    if not candidates:
        return []

    found: list[Neighbour] = []
    ordered = sorted(candidates)
    for start in range(0, len(ordered), 900):  # SQLite's variable limit
        chunk = ordered[start : start + 900]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            "SELECT item_id, value FROM fingerprint_values"  # noqa: S608
            f" WHERE kind = ? AND version = ? AND item_id IN ({placeholders})",
            (kind, version, *chunk),
        ).fetchall()
        for item_id, stored in rows:
            distance = bin(numeric ^ int(stored, 16)).count("1")
            if distance <= max_distance:
                found.append(Neighbour(item_id=int(item_id), distance=distance))
    found.sort(key=lambda neighbour: (neighbour.distance, neighbour.item_id))
    return found


def indexed_count(conn: sqlite3.Connection, *, kind: str, version: str = "1") -> int:
    ensure_tables(conn)
    row = conn.execute(
        "SELECT COUNT(*) FROM fingerprint_values WHERE kind = ? AND version = ?",
        (kind, version),
    ).fetchone()
    return int(row[0]) if row else 0


def forget(conn: sqlite3.Connection, item_id: int, *, kind: str, version: str = "1") -> None:
    ensure_tables(conn)
    conn.execute(
        "DELETE FROM fingerprint_slices WHERE item_id = ? AND kind = ? AND version = ?",
        (item_id, kind, version),
    )
    conn.execute(
        "DELETE FROM fingerprint_values WHERE item_id = ? AND kind = ? AND version = ?",
        (item_id, kind, version),
    )
