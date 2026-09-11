"""Which items were ever judged the same, including the ones already deleted.

Deduplication is usually written as an operation over the files that exist. That
works until a second detector arrives. Suppose a perceptual hash links A and B,
the user keeps A and deletes B, and a later crop-aware detector would have linked
B and C. Run it on the survivors and it sees A and C, which it cannot link. The
evidence that connected them was in B, and B is gone.

The fix is not to keep B. It is to keep what B *was*: a few hundred bytes of
fingerprints and the component it belonged to. Deduplication then stops being a
list of surviving files and becomes a record of every equivalence ever
established, which later detectors can still reason over.

This matters most at the moment it is least visible. Fingerprints can only be
computed while the file is on disk, so anything a future layer will need has to
be written *before* the first deletion. Whatever is not captured then is gone
for good, no matter how good the later detector is.

Four tables, each for a reason that is easy to get wrong:

``equivalence_components``
    A component has its own identity. Naming it after a surviving member breaks
    the moment every member is deleted, and again when a component is restored,
    merged, or has its representative removed. Which member to show is an
    attribute of the component, not what the component *is*.

``equivalence_members``
    One row per item ever considered, present or removed. ``component_id`` is
    stored resolved rather than as a parent pointer, so reading it is one
    indexed lookup instead of a pointer chase.

``equivalence_edges``
    Every assertion of equivalence, with who made it. An edge asserted by a
    detector and one confirmed by a person are different claims, and a schema
    that stores both as "linked" becomes a pile of indistinguishable inferences
    within a year. Keeping the edges also means components can be rebuilt from
    scratch if a class of assertion is later found to be wrong.

``equivalence_fingerprints``
    ``(member_id, kind, version, value)``. A new detector adds rows of a new
    kind and needs no migration, which is the point: the schema has to accept
    evidence from detectors that do not exist yet. ``version`` and ``bits`` are
    there because a fingerprint's meaning changes when its algorithm does, and
    because assuming every future hash fits in 64 bits is exactly the kind of
    assumption that is free now and expensive later.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping

# Fingerprint kinds in use. Values are text so a new detector can store whatever
# shape it needs: a hex digest, a joined list of segment hashes, a base64 blob.
KIND_PHASH = "phash"
KIND_CONTENT = "content_sha256"

# Who asserted an equivalence. The distinction is the whole reason edges are
# stored rather than only their resulting components.
PROVENANCE_USER_CONFIRMED = "user_confirmed_duplicate"
PROVENANCE_EXACT_CONTENT = "exact_content_match"
PROVENANCE_IMPORT_QUARANTINE = "import_quarantine_match"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS equivalence_components (
            component_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            preferred_member_id INTEGER,
            created_at          TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS equivalence_members (
            member_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash  TEXT NOT NULL UNIQUE,
            component_id  INTEGER NOT NULL,
            media_id      INTEGER,
            original_path TEXT,
            removed_at    TEXT,
            FOREIGN KEY (component_id) REFERENCES equivalence_components(component_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS equivalence_edges (
            member_a         INTEGER NOT NULL,
            member_b         INTEGER NOT NULL,
            provenance       TEXT NOT NULL,
            detector_version TEXT NOT NULL DEFAULT '',
            confirmed_at     TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (member_a, member_b, provenance)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS equivalence_fingerprints (
            member_id INTEGER NOT NULL,
            kind      TEXT NOT NULL,
            version   TEXT NOT NULL DEFAULT '1',
            bits      INTEGER,
            value     TEXT NOT NULL,
            PRIMARY KEY (member_id, kind, version, value),
            FOREIGN KEY (member_id) REFERENCES equivalence_members(member_id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_equiv_component ON equivalence_members(component_id)"
    )
    # The lookup a later detector performs: given a fingerprint it just computed,
    # which member does it belong to -- including members whose file is gone.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_equiv_fp_lookup"
        " ON equivalence_fingerprints(kind, value)"
    )


def record_member(
    conn: sqlite3.Connection,
    *,
    content_hash: str,
    fingerprints: Mapping[str, str] | None = None,
    media_id: int | None = None,
    original_path: str | None = None,
    now: str = "",
) -> int:
    """Register an item and its fingerprints, or enrich one already known.

    Idempotent on ``content_hash``, so import and deletion paths can call it
    freely. New fingerprint kinds merge into an existing member rather than
    replacing it: a later detector enriching an old row is the normal case.
    """
    ensure_tables(conn)
    row = conn.execute(
        "SELECT member_id FROM equivalence_members WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if row is None:
        component = conn.execute(
            "INSERT INTO equivalence_components (created_at) VALUES (?)", (now,)
        ).lastrowid
        member_id = int(
            conn.execute(
                "INSERT INTO equivalence_members"
                " (content_hash, component_id, media_id, original_path) VALUES (?, ?, ?, ?)",
                (content_hash, component, media_id, original_path),
            ).lastrowid
        )
        conn.execute(
            "UPDATE equivalence_components SET preferred_member_id = ? WHERE component_id = ?",
            (member_id, component),
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
    conn: sqlite3.Connection,
    member_id: int,
    fingerprints: Mapping[str, str],
    *,
    version: str = "1",
    bits: int | None = None,
) -> None:
    """Attach evidence to a member. Works after the file is gone."""
    ensure_tables(conn)
    rows = [
        (member_id, kind, version, bits, value)
        for kind, value in fingerprints.items()
        if kind and value
    ]
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO equivalence_fingerprints"
            " (member_id, kind, version, bits, value) VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def component_of(conn: sqlite3.Connection, member_id: int) -> int | None:
    row = conn.execute(
        "SELECT component_id FROM equivalence_members WHERE member_id = ?", (member_id,)
    ).fetchone()
    return int(row[0]) if row else None


def assert_equivalent(
    conn: sqlite3.Connection,
    left_id: int,
    right_id: int,
    *,
    provenance: str,
    detector_version: str = "",
    confirmed_at: str = "",
) -> int:
    """Record that two members were declared equivalent, and merge components.

    ``provenance`` is required and not defaulted, because the difference between
    a detector's suggestion and a person's decision is the one thing this table
    exists to keep. An edge is kept even when it changes no component, so the
    assertion survives and components can be rebuilt without it later.
    """
    ensure_tables(conn)
    left = component_of(conn, left_id)
    right = component_of(conn, right_id)
    if left is None or right is None:
        raise ValueError("membro desconhecido no grafo de equivalências")

    first, second = sorted((left_id, right_id))
    conn.execute(
        "INSERT OR IGNORE INTO equivalence_edges"
        " (member_a, member_b, provenance, detector_version, confirmed_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (first, second, provenance, detector_version, confirmed_at),
    )
    if left == right:
        return left

    # The older component absorbs the newer one, so the surviving id does not
    # depend on the order detectors happen to run in.
    survivor, absorbed = (left, right) if left < right else (right, left)
    conn.execute(
        "UPDATE equivalence_members SET component_id = ? WHERE component_id = ?",
        (survivor, absorbed),
    )
    conn.execute("DELETE FROM equivalence_components WHERE component_id = ?", (absorbed,))
    return survivor


def edges_of(conn: sqlite3.Connection, member_id: int) -> list[dict]:
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT member_a, member_b, provenance, detector_version, confirmed_at"
        " FROM equivalence_edges WHERE member_a = ? OR member_b = ?"
        " ORDER BY member_a, member_b",
        (member_id, member_id),
    ).fetchall()
    return [
        {
            "member_a": int(row[0]),
            "member_b": int(row[1]),
            "provenance": row[2],
            "detector_version": row[3],
            "confirmed_at": row[4],
        }
        for row in rows
    ]


def member_for_fingerprint(conn: sqlite3.Connection, kind: str, value: str) -> int | None:
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

    The row, its fingerprints and its edges all stay, and the component keeps
    its identity even when nothing in it survives on disk.
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


def members_of_component(conn: sqlite3.Connection, member_id: int) -> list[dict]:
    """Every member ever placed in this component, removed ones included."""
    ensure_tables(conn)
    component = component_of(conn, member_id)
    if component is None:
        return []
    rows = conn.execute(
        "SELECT member_id, content_hash, media_id, original_path, removed_at"
        " FROM equivalence_members WHERE component_id = ? ORDER BY member_id",
        (component,),
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
