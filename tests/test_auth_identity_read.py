"""Request authentication reads the device and user on a reused connection.

Reusing the connection must not cache anything: the database is read on every
request, so revoking a device takes effect on the next one.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import core.users_db as users_db
from core.auth import hash_password


def _account(root: Path, name: str = "alice"):
    db = root / "users.db"
    user = users_db.create_user(db, root, username=name, password_hash=hash_password("senha segura 123"))
    device = users_db.create_device(db, user.id, "phone", "android", "x" * 64)
    return db, user, device


def test_one_read_returns_the_device_and_its_user(tmp_path: Path):
    db, user, device = _account(tmp_path)

    found_device, found_user = users_db.resolve_device_session(db, device.id)

    assert found_device == users_db.get_device(db, device.id)
    assert found_user == users_db.get_user_by_id(db, user.id)
    assert users_db.resolve_device_session(db, "no-such-device") is None


def test_a_revoked_device_is_refused_on_the_next_read_through_the_same_connection(tmp_path: Path):
    db, user, device = _account(tmp_path)
    assert users_db.resolve_device_session(db, device.id)[0].revoked_at is None

    assert users_db.revoke_device(db, user.id, device.id)

    assert users_db.resolve_device_session(db, device.id)[0].revoked_at is not None


def test_changes_by_another_connection_are_seen_at_once(tmp_path: Path):
    # The reused connection must start a new read each time, and see what
    # other connections committed meanwhile.
    db, user, device = _account(tmp_path)
    users_db.resolve_device_session(db, device.id)
    users_db.read_user(db, user.id)

    with sqlite3.connect(db) as other:
        other.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user.id,))
        other.execute("UPDATE devices SET token_version = token_version + 1 WHERE id = ?", (device.id,))

    found_device, found_user = users_db.resolve_device_session(db, device.id)
    assert found_device.token_version == device.token_version + 1
    assert found_user.session_version == user.session_version + 1
    assert users_db.read_user(db, user.id).session_version == user.session_version + 1


def test_a_users_database_replaced_at_the_same_path_is_read_anew(tmp_path: Path):
    db, _user, old_device = _account(tmp_path)
    users_db.resolve_device_session(db, old_device.id)

    for suffix in ("", "-wal", "-shm"):
        db.with_name(db.name + suffix).unlink(missing_ok=True)
    users_db._ready_databases.clear()
    _db, new_user, new_device = _account(tmp_path, "bob")

    assert users_db.resolve_device_session(db, old_device.id) is None
    assert users_db.resolve_device_session(db, new_device.id)[1].username == new_user.username
