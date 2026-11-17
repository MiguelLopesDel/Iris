"""SQLite databases without copy-on-write on btrfs.

A database is rewritten a page at a time, in place. On btrfs every such write
goes to a new place on disk, so a catalogue that has been in use a while ends
up in thousands of pieces: measured, a 330 MB library database in 7,653
extents on a hard disk, where reading it cold took over three minutes and held
up every other request meanwhile.

Only the database files are marked, not the data folder: the folder also holds
the photos, and on those copy-on-write is what gives btrfs checksums against
silent corruption and the reflinks shared spaces copy with. The mark is
inherited by what is created inside a folder afterwards, so marking the folder
would have taken both away from every new photo.

The attribute (``chattr +C``) only takes effect on an empty file, hence it is
set when the file is created, before SQLite writes to it. Elsewhere (ext4, xfs,
other systems) the request is refused and the file is simply created.
"""
from __future__ import annotations

import fcntl
import os
import struct
from pathlib import Path

# linux/fs.h
FS_IOC_GETFLAGS = 0x80086601
FS_IOC_SETFLAGS = 0x40086602
FS_NOCOW_FL = 0x00800000


def _set_nocow(fd: int) -> bool:
    buffer = fcntl.ioctl(fd, FS_IOC_GETFLAGS, struct.pack("i", 0))
    flags = struct.unpack("i", buffer)[0]
    if flags & FS_NOCOW_FL:
        return True
    fcntl.ioctl(fd, FS_IOC_SETFLAGS, struct.pack("i", flags | FS_NOCOW_FL))
    return True


def create_database_file(path: Path) -> bool:
    """Create ``path`` empty and without copy-on-write, if it does not exist yet.

    Returns whether the attribute was set. Never fails because of it: a
    filesystem without the attribute, or a race with another creator, leaves
    SQLite to create or open the file as usual.
    """
    path = Path(path)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    except FileExistsError:
        return False
    try:
        return _set_nocow(fd)
    except OSError:
        return False
    finally:
        os.close(fd)
