"""Membership and identity for shared spaces within one Iris instance.

This module stores no media. Access to a space's catalogue (see
:mod:`core.space_catalog`) is gated through membership here, independently of
instance administration. A space always keeps at least one manager.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from core.users_db import init_users_db, now_iso

ROLES = frozenset({"viewer", "contributor", "manager"})


class SpaceNotFound(Exception):
    """The actor cannot access this space, whether or not its id exists."""


class SpacePermissionDenied(Exception):
    """The actor belongs to the space but lacks the requested permission."""


class SpaceMemberExists(Exception):
    """The target account already belongs to the space."""


class SpaceUserNotFound(Exception):
    """The invited account is not registered in this instance."""


class SpaceMemberNotFound(Exception):
    """The target account does not belong to the space."""


class SpaceLastManager(Exception):
    """The change would leave the space without a manager."""


@dataclass(frozen=True)
class Space:
    id: int
    name: str
    created_by: int | None
    created_at: str
    role: str


@dataclass(frozen=True)
class SpaceMember:
    user_id: int
    username: str
    display_name: str
    role: str


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_shared_spaces_db(path: Path) -> None:
    """Add the first space tables to an existing account registry, idempotently."""
    init_users_db(path)
    with _connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shared_spaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shared_space_members (
                space_id INTEGER NOT NULL REFERENCES shared_spaces(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('viewer', 'contributor', 'manager')),
                added_at TEXT NOT NULL,
                added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                PRIMARY KEY (space_id, user_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_shared_space_members_user "
            "ON shared_space_members(user_id, space_id)"
        )


def _space_from_row(row: sqlite3.Row) -> Space:
    return Space(
        id=int(row["id"]),
        name=str(row["name"]),
        created_by=int(row["created_by"]) if row["created_by"] is not None else None,
        created_at=str(row["created_at"]),
        role=str(row["role"]),
    )


def _membership(connection: sqlite3.Connection, space_id: int, actor_id: int) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT role FROM shared_space_members
        WHERE space_id = ? AND user_id = ?
        """,
        (space_id, actor_id),
    ).fetchone()
    if row is None:
        raise SpaceNotFound
    return row


def member_role(path: Path, space_id: int, actor_id: int) -> str:
    """The actor's role in the space, or :class:`SpaceNotFound` if not a member.

    This is the only gate to a space's items: instance administration grants
    nothing here.
    """
    init_shared_spaces_db(path)
    with _connect(path) as connection:
        return str(_membership(connection, space_id, actor_id)["role"])


def usernames(path: Path, user_ids: set[int]) -> dict[int, str]:
    """Usernames of item authors; a deleted account is simply absent."""
    if not user_ids:
        return {}
    ids = sorted(user_ids)
    placeholders = ",".join("?" * len(ids))
    with _connect(path) as connection:
        rows = connection.execute(
            f"SELECT id, username FROM users WHERE id IN ({placeholders})", ids
        ).fetchall()
    return {int(row["id"]): str(row["username"]) for row in rows}


def create_space(path: Path, creator_id: int, name: str) -> Space:
    normalized = name.strip()
    if not 1 <= len(normalized) <= 120:
        raise ValueError("Space name must have 1-120 characters")
    init_shared_spaces_db(path)
    with _connect(path) as connection:
        now = now_iso()
        cursor = connection.execute(
            "INSERT INTO shared_spaces (name, created_by, created_at) VALUES (?, ?, ?)",
            (normalized, creator_id, now),
        )
        space_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO shared_space_members (space_id, user_id, role, added_at, added_by)
            VALUES (?, ?, 'manager', ?, ?)
            """,
            (space_id, creator_id, now, creator_id),
        )
    return Space(space_id, normalized, creator_id, now, "manager")


def list_spaces(path: Path, actor_id: int) -> list[Space]:
    init_shared_spaces_db(path)
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.name, s.created_by, s.created_at, m.role
            FROM shared_spaces AS s
            JOIN shared_space_members AS m ON m.space_id = s.id
            WHERE m.user_id = ?
            ORDER BY s.created_at DESC, s.id DESC
            """,
            (actor_id,),
        ).fetchall()
    return [_space_from_row(row) for row in rows]


def get_space(path: Path, space_id: int, actor_id: int) -> Space:
    init_shared_spaces_db(path)
    with _connect(path) as connection:
        row = connection.execute(
            """
            SELECT s.id, s.name, s.created_by, s.created_at, m.role
            FROM shared_spaces AS s
            JOIN shared_space_members AS m ON m.space_id = s.id
            WHERE s.id = ? AND m.user_id = ?
            """,
            (space_id, actor_id),
        ).fetchone()
    if row is None:
        raise SpaceNotFound
    return _space_from_row(row)


def list_members(path: Path, space_id: int, actor_id: int) -> list[SpaceMember]:
    init_shared_spaces_db(path)
    with _connect(path) as connection:
        _membership(connection, space_id, actor_id)
        rows = connection.execute(
            """
            SELECT u.id AS user_id, u.username, u.display_name, m.role
            FROM shared_space_members AS m
            JOIN users AS u ON u.id = m.user_id
            WHERE m.space_id = ?
            ORDER BY u.username
            """,
            (space_id,),
        ).fetchall()
    return [SpaceMember(**dict(row)) for row in rows]


def add_member(path: Path, space_id: int, actor_id: int, username: str, role: str) -> SpaceMember:
    if role not in ROLES:
        raise ValueError("Invalid space role")
    init_shared_spaces_db(path)
    with _connect(path) as connection:
        if _membership(connection, space_id, actor_id)["role"] != "manager":
            raise SpacePermissionDenied
        user = connection.execute(
            "SELECT id, username, display_name FROM users WHERE username = ?",
            (username.strip().lower(),),
        ).fetchone()
        if user is None:
            raise SpaceUserNotFound
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO shared_space_members
                (space_id, user_id, role, added_at, added_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (space_id, int(user["id"]), role, now_iso(), actor_id),
        )
        if cursor.rowcount == 0:
            raise SpaceMemberExists
    return SpaceMember(int(user["id"]), str(user["username"]), str(user["display_name"]), role)


@contextmanager
def _serialised(path: Path) -> Iterator[sqlite3.Connection]:
    """A write transaction taken up front, so "am I the last manager?" and the
    change that depends on it cannot interleave with another manager's."""
    init_shared_spaces_db(path)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
    finally:
        connection.close()


def _target_role(connection: sqlite3.Connection, space_id: int, user_id: int) -> str:
    row = connection.execute(
        "SELECT role FROM shared_space_members WHERE space_id = ? AND user_id = ?",
        (space_id, user_id),
    ).fetchone()
    if row is None:
        raise SpaceMemberNotFound
    return str(row["role"])


def _managers(connection: sqlite3.Connection, space_id: int) -> int:
    return int(connection.execute(
        "SELECT COUNT(*) FROM shared_space_members WHERE space_id = ? AND role = 'manager'",
        (space_id,),
    ).fetchone()[0])


def change_role(path: Path, space_id: int, actor_id: int, user_id: int, role: str) -> SpaceMember:
    """A manager sets any member's role; the last manager cannot step down."""
    if role not in ROLES:
        raise ValueError("Invalid space role")
    with _serialised(path) as connection:
        if _membership(connection, space_id, actor_id)["role"] != "manager":
            raise SpacePermissionDenied
        current = _target_role(connection, space_id, user_id)
        if current == "manager" and role != "manager" and _managers(connection, space_id) == 1:
            raise SpaceLastManager
        connection.execute(
            "UPDATE shared_space_members SET role = ? WHERE space_id = ? AND user_id = ?",
            (role, space_id, user_id),
        )
        user = connection.execute(
            "SELECT username, display_name FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return SpaceMember(user_id, str(user["username"]), str(user["display_name"]), role)


def remove_member(path: Path, space_id: int, actor_id: int, user_id: int) -> None:
    """A manager removes a member, or a member leaves.

    Access ends at once, because every request checks membership. What the
    member added stays in the space: items belong to the space, not to the
    account that contributed them.
    """
    with _serialised(path) as connection:
        actor_role = str(_membership(connection, space_id, actor_id)["role"])
        if actor_id != user_id and actor_role != "manager":
            raise SpacePermissionDenied
        target = _target_role(connection, space_id, user_id)
        if target == "manager" and _managers(connection, space_id) == 1:
            raise SpaceLastManager
        connection.execute(
            "DELETE FROM shared_space_members WHERE space_id = ? AND user_id = ?",
            (space_id, user_id),
        )
