"""SQLite databases are created without copy-on-write where the filesystem has it."""
from __future__ import annotations

import errno
import struct
from pathlib import Path

from core import nocow
from core.indexer_db import init_db


class _FakeFlags:
    """The inode flags ioctl, as btrfs answers it, recorded per call."""

    def __init__(self, refuse: bool = False):
        self.flags = 0
        self.refuse = refuse
        self.calls: list[int] = []

    def __call__(self, fd, request, buffer):
        self.calls.append(request)
        if self.refuse:
            raise OSError(errno.ENOTTY, "Inappropriate ioctl for device")
        if request == nocow.FS_IOC_SETFLAGS:
            self.flags = struct.unpack("i", buffer)[0]
            return buffer
        return struct.pack("i", self.flags)


def test_a_new_library_database_is_marked_before_sqlite_writes(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeFlags()
    monkeypatch.setattr(nocow.fcntl, "ioctl", fake)

    init_db(tmp_path / "users" / "1" / "iris.db").close()

    assert fake.flags & nocow.FS_NOCOW_FL
    assert (tmp_path / "users" / "1" / "iris.db").stat().st_size > 0  # SQLite then filled it


def test_an_existing_database_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeFlags()
    monkeypatch.setattr(nocow.fcntl, "ioctl", fake)
    existing = tmp_path / "iris.db"
    existing.write_bytes(b"SQLite format 3\0")

    assert nocow.create_database_file(existing) is False
    assert fake.calls == []


def test_a_filesystem_without_the_attribute_still_gets_its_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(nocow.fcntl, "ioctl", _FakeFlags(refuse=True))

    assert nocow.create_database_file(tmp_path / "iris.db") is False
    init_db(tmp_path / "iris.db").close()
    assert (tmp_path / "iris.db").stat().st_size > 0


def test_the_accounts_database_is_marked_too(tmp_path: Path, monkeypatch) -> None:
    from core.users_db import init_users_db

    fake = _FakeFlags()
    monkeypatch.setattr(nocow.fcntl, "ioctl", fake)

    init_users_db(tmp_path / "users.db")

    assert fake.flags & nocow.FS_NOCOW_FL
