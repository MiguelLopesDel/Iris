"""Which items were ever judged the same, including the ones already deleted.

Deduplication is usually written as an operation over the files that exist. That
works until a second detector arrives. Suppose a perceptual hash links A and B,
the user keeps A and deletes B, and a later crop-aware detector would have linked
B and C. Run it on the survivors and it sees A and C, which it cannot link. The
evidence that connected them was in B, and B is gone.

The fix is not to keep B. It is to keep what B *was*: a few dozen bytes of
fingerprints and the component it belonged to. Deduplication then stops being a
list of surviving files and becomes a record of every equivalence ever
established, which later detectors can still reason over.

This matters most at the moment it is least visible. Fingerprints can only be
computed while the file is on disk, so anything a future layer will need has to
be written *before* the first deletion. Whatever is not captured then is gone
for good, no matter how good the later detector is.

Two tables, for two different reasons:

``equivalence_members``
    One row per item ever considered, present or removed, carrying the component
    it belongs to. ``canonical_id`` is stored resolved rather than as a parent
    pointer, so reading it is one indexed lookup instead of a pointer chase --
    the same reason the rest of this pipeline avoids structures that assume
    everything is in memory.

``equivalence_fingerprints``
    ``(member_id, kind, value)``. A new detector adds rows of a new ``kind`` and
    needs no migration, which is the whole point: the schema has to accept
    evidence from detectors that do not exist yet.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping

# Fingerprint kinds in use. Values are free-form strings so a new detector can
# store whatever shape it needs (a hex digest, a joined list of segment hashes).
KIND_PHASH = "phash"
KIND_CONTENT = "content_sha256"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS equivalence_members (
            member_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash  TEXT NOT NULL UNIQUE,
            canonical_id  INTEGER NOT NULL,
            media_id      INTEGER,
            original_path TEXT,
            removed_at    TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS equivalence_fingerprints (
            member_id INTEGER NOT NULL,
            kind      TEXT NOT NULL,
            value     TEXT NOT NULL,
            PRIMARY KEY (member_id, kind, value),
            FOREIGN KEY (member_id) REFERENCES equivalence_members(member_id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_equiv_canonical ON equivalence_members(canonical_id)"
    )
    # The lookup a later detector performs: given a fingerprint it just computed,
    # which member does it belong to -- including members whose file is gone.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_equiv_fp_lookup ON equivalence_fingerprints(kind, value)"
    )


def record_member(
    conn: sqlite3.Connection,
    *,
    content_hash: str,
    fingerprints: Mapping[str, str] | None = None,
    media_id: int | None = None,
    original_path: str | None = None,
) -> int:
    """Register an item and its fingerprints, or add fingerprints to a known one.

    Idempotent on ``content_hash``, so the import path can call it freely. New
    fingerprint kinds are merged into an existing member rather than replacing
    it: a later detector enriching an old row is the normal case, not an error.
    """
    ensure_tables(conn)
    row = conn.execute(
        "SELECT member_id FROM equivalence_members WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if row is None:
        cursor = conn.execute(
            "INSERT INTO equivalence_members (content_hash, canonical_id, media_id, original_path)"
            " VALUES (?, 0, ?, ?)",
            (content_hash, media_id, original_path),
        )
        member_id = int(cursor.lastrowid)
        # A member with no known equivalences is its own component.
        conn.execute(
            "UPDATE equivalence_members SET canonical_id = ? WHERE member_id = ?",
            (member_id, member_id),
        )
    else:
        member_id = int(row[0])
        if media_id is not None or original_path is not None:
            conn.execute(
                "UPDATE equivalence_members SET media_id = COALESCE(?, media_id),"
                " original_path = COALESCE(?, original_path) WHERE member_id = ?",
                (media_id, original_path, member_id),
            )

    add_fingerprints(conn, member_id, {KIND_CONTENT: content_hash, **(fingerprints or {})})
    return member_id


def add_fingerprints(
    conn: sqlite3.Connection, member_id: int, fingerprints: Mapping[str, str]
) -> None:
    """Attach evidence to a member. Works after the file is gone."""
    ensure_tables(conn)
    rows = [
        (member_id, kind, value)
        for kind, value in fingerprints.items()
        if kind and value
    ]
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO equivalence_fingerprints (member_id, kind, value)"
            " VALUES (?, ?, ?)",
            rows,
        )


def canonical_of(conn: sqlite3.Connection, member_id: int) -> int | None:
    row = conn.execute(
        "SELECT canonical_id FROM equivalence_members WHERE member_id = ?", (member_id,)
    ).fetchone()
    return int(row[0]) if row else None


def link(conn: sqlite3.Connection, left_id: int, right_id: int) -> int:
    """Declare two members equivalent and merge their components.

    Returns the surviving canonical id. The lower id wins so the choice is
    stable across runs and independent of the order detectors happen to run in.
    """
    ensure_tables(conn)
    left = canonical_of(conn, left_id)
    right = canonical_of(conn, right_id)
    if left is None or right is None:
        raise ValueError("membro desconhecido no grafo de equivalências")
    if left == right:
        return left

    survivor, absorbed = (left, right) if left < right else (right, left)
    conn.execute(
        "UPDATE equivalence_members SET canonical_id = ? WHERE canonical_id = ?",
        (survivor, absorbed),
    )
    return survivor


def member_for_fingerprint(
    conn: sqlite3.Connection, kind: str, value: str
) -> int | None:
    """The member carrying this fingerprint, whether or not its file still exists.

    This is the bridge lookup: a detector that has just computed a fingerprint
    asks who else ever had it, and gets an answer even for deleted items.
    """
    ensure_tables(conn)
    row = conn.execute(
        "SELECT member_id FROM equivalence_fingerprints WHERE kind = ? AND value = ? LIMIT 1",
        (kind, value),
    ).fetchone()
    return int(row[0]) if row else None


def mark_removed(
    conn: sqlite3.Connection, content_hashes: Iterable[str], removed_at: str
) -> int:
    """Record that these files no longer exist, keeping their evidence.

    The row and its fingerprints stay. That is the point of the table.
    """
    ensure_tables(conn)
    hashes = [h for h in content_hashes if h]
    if not hashes:
        return 0
    placeholders = ",".join("?" for _ in hashes)
    cursor = conn.execute(
        f"UPDATE equivalence_members SET removed_at = ?, media_id = NULL"  # noqa: S608
        f" WHERE content_hash IN ({placeholders}) AND removed_at IS NULL",
        (removed_at, *hashes),
    )
    return cursor.rowcount


def component(conn: sqlite3.Connection, member_id: int) -> list[dict]:
    """Every member ever placed in this component, removed ones included."""
    ensure_tables(conn)
    canonical = canonical_of(conn, member_id)
    if canonical is None:
        return []
    rows = conn.execute(
        "SELECT member_id, content_hash, media_id, original_path, removed_at"
        " FROM equivalence_members WHERE canonical_id = ? ORDER BY member_id",
        (canonical,),
    ).fetchall()
    return [
        {
            "member_id": int(row[0]),
            "content_hash": row[1],
            "media_id": row[2],
            "original_path": row[3],
            "removed_at": row[4],
        }
        for row in rows
    ]


def fingerprints_of(conn: sqlite3.Connection, member_id: int) -> dict[str, list[str]]:
    ensure_tables(conn)
    result: dict[str, list[str]] = {}
    for kind, value in conn.execute(
        "SELECT kind, value FROM equivalence_fingerprints WHERE member_id = ? ORDER BY kind",
        (member_id,),
    ):
        result.setdefault(kind, []).append(value)
    return result
