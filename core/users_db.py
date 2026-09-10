"""Account registry for a self-hosted Iris instance.

The account registry intentionally lives outside each private library database.
Library databases remain single-owner SQLite files, which keeps legacy Iris tables
and FAISS indexes isolated without adding an ownership predicate to every query.
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.search_engine import DEFAULT_MODEL

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,62}$")


@dataclass(frozen=True)
class IrisUser:
    id: int
    username: str
    password_hash: str
    display_name: str
    is_admin: bool
    db_path: Path
    media_root: Path
    model_name: str
    session_version: int


@dataclass(frozen=True)
class IrisDevice:
    id: str
    user_id: int
    name: str
    platform: str
    refresh_token_hash: str
    token_version: int
    revoked_at: str | None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_users_db(path: Path) -> None:
    with _connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                is_admin INTEGER NOT NULL DEFAULT 0,
                db_path TEXT NOT NULL,
                media_root TEXT NOT NULL,
                model_name TEXT NOT NULL,
                session_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                refresh_token_hash TEXT NOT NULL,
                token_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_user_id ON devices(user_id)")


def has_users(path: Path) -> bool:
    if not path.exists():
        return False
    init_users_db(path)
    with _connect(path) as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def validate_username(username: str) -> str:
    normalized = username.strip().lower()
    if not _USERNAME.fullmatch(normalized):
        raise ValueError("Usuário deve ter 2-63 caracteres: letras, números, ., _ ou -")
    return normalized


def _library_paths(data_dir: Path, user_id: int) -> tuple[Path, Path]:
    root = (data_dir / "users" / str(user_id)).resolve()
    return root / "iris.db", root / "media"


def _from_row(row: sqlite3.Row) -> IrisUser:
    return IrisUser(
        id=int(row["id"]),
        username=str(row["username"]),
        password_hash=str(row["password_hash"]),
        display_name=str(row["display_name"]),
        is_admin=bool(row["is_admin"]),
        db_path=Path(str(row["db_path"])),
        media_root=Path(str(row["media_root"])),
        model_name=str(row["model_name"]),
        session_version=int(row["session_version"]),
    )


def create_user(
    path: Path,
    data_dir: Path,
    *,
    username: str,
    password_hash: str,
    display_name: str = "",
    is_admin: bool = False,
    model_name: str = DEFAULT_MODEL,
) -> IrisUser:
    init_users_db(path)
    username = validate_username(username)
    with _connect(path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO users (username, password_hash, display_name, is_admin, db_path,
                               media_root, model_name, created_at)
            VALUES (?, ?, ?, ?, '', '', ?, ?)
            """,
            (username, password_hash, display_name.strip(), int(is_admin), model_name, now_iso()),
        )
        user_id = int(cursor.lastrowid)
        db_path, media_root = _library_paths(data_dir, user_id)
        root = db_path.parent
        root.mkdir(parents=True, exist_ok=True)
        media_root.mkdir(parents=True, exist_ok=True)
        # Private by default even when a permissive host umask is configured.
        for directory in (root, media_root):
            os.chmod(directory, 0o700)
        conn.execute(
            "UPDATE users SET db_path = ?, media_root = ? WHERE id = ?",
            (str(db_path), str(media_root), user_id),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row is not None
    return _from_row(row)


def get_user_by_id(path: Path, user_id: int) -> IrisUser | None:
    if not path.exists():
        return None
    init_users_db(path)
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _from_row(row) if row else None


def get_user_by_username(path: Path, username: str) -> IrisUser | None:
    if not path.exists():
        return None
    init_users_db(path)
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
    return _from_row(row) if row else None


def list_users(path: Path) -> list[IrisUser]:
    if not path.exists():
        return []
    init_users_db(path)
    with _connect(path) as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    return [_from_row(row) for row in rows]


def _device_from_row(row: sqlite3.Row) -> IrisDevice:
    return IrisDevice(
        id=str(row["id"]), user_id=int(row["user_id"]), name=str(row["name"]),
        platform=str(row["platform"]), refresh_token_hash=str(row["refresh_token_hash"]),
        token_version=int(row["token_version"]), revoked_at=row["revoked_at"],
    )


def create_device(path: Path, user_id: int, name: str, platform: str, refresh_token_hash: str) -> IrisDevice:
    init_users_db(path)
    device_id = os.urandom(16).hex()
    now = now_iso()
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO devices (id, user_id, name, platform, refresh_token_hash, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (device_id, user_id, name.strip()[:120] or "Unnamed device", platform.strip()[:60], refresh_token_hash, now, now),
        )
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    assert row is not None
    return _device_from_row(row)


def get_device(path: Path, device_id: str) -> IrisDevice | None:
    if not path.exists():
        return None
    init_users_db(path)
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return _device_from_row(row) if row else None


def rotate_device_refresh_token(path: Path, device_id: str, token_hash: str) -> bool:
    with _connect(path) as conn:
        cursor = conn.execute(
            "UPDATE devices SET refresh_token_hash = ?, last_seen_at = ? WHERE id = ? AND revoked_at IS NULL",
            (token_hash, now_iso(), device_id),
        )
    return cursor.rowcount == 1


def revoke_device(path: Path, user_id: int, device_id: str) -> bool:
    with _connect(path) as conn:
        cursor = conn.execute(
            "UPDATE devices SET revoked_at = ?, token_version = token_version + 1 WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (now_iso(), device_id, user_id),
        )
    return cursor.rowcount == 1


def list_devices(path: Path, user_id: int) -> list[IrisDevice]:
    init_users_db(path)
    with _connect(path) as conn:
        rows = conn.execute("SELECT * FROM devices WHERE user_id = ? ORDER BY last_seen_at DESC", (user_id,)).fetchall()
    return [_device_from_row(row) for row in rows]
