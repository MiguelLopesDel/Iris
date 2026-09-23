"""Copy media the cheapest way the filesystem under ``data/`` allows.

A shared space and a private library each keep their own copy of an item, so
deleting one never breaks the other. On a copy-on-write filesystem (btrfs, XFS
with reflink, bcachefs, ZFS with block cloning) that copy can be a *reflink*:
a new file sharing the same extents until one side writes, which costs no data
blocks and is as independent as a real copy. Elsewhere it is a byte copy.

A hard link is offered only on request: it also costs nothing, but both names
are the same inode, so an in-place edit of one changes the other.

The filesystem type is read for the report; what decides is a probe that tries
each operation for real in the target directory, because support depends on
mount options and kernel, not on the name.

Run ``python -m core.fs_clone <dir>`` to see what a directory supports.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

STRATEGIES = ("auto", "reflink", "hardlink", "copy")
DEFAULT_STRATEGY = "auto"
# linux/fs.h: _IOW(0x94, 9, int)
_FICLONE = 0x40049409
# Errors that mean "not possible here", as opposed to a real I/O failure.
_UNSUPPORTED = {
    errno.EXDEV,
    errno.EOPNOTSUPP,
    errno.ENOTSUP,
    errno.EINVAL,
    errno.ENOTTY,
    errno.EPERM,
    errno.EMLINK,
}


class StorageConfigError(RuntimeError):
    """The configured strategy cannot work on this filesystem."""


@dataclass(frozen=True)
class StorageReport:
    path: str
    filesystem: str
    reflink: bool
    hardlink: bool
    requested: str
    strategy: str


def filesystem_type(path: Path) -> str:
    """The type of the mount holding ``path``, from /proc/self/mountinfo."""
    target = str(Path(path).resolve())
    best, best_type = "", "unknown"
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return best_type
    for line in lines:
        left, _, right = line.partition(" - ")
        fields = left.split()
        if len(fields) < 5 or not right:
            continue
        mount_point = fields[4].replace("\\040", " ")
        inside = target == mount_point or target.startswith(mount_point.rstrip("/") + "/")
        if inside and len(mount_point) >= len(best):
            best, best_type = mount_point, right.split()[0]
    return best_type


def _reflink(source: Path, destination: Path) -> None:
    with source.open("rb") as reader, destination.open("wb") as writer:
        fcntl.ioctl(writer.fileno(), _FICLONE, reader.fileno())


def probe(directory: Path) -> tuple[bool, bool]:
    """Try a reflink and a hard link inside ``directory``: (reflink, hardlink)."""
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    source = directory / f".probe-{token}"
    clone = directory / f".probe-{token}.clone"
    link = directory / f".probe-{token}.link"
    try:
        source.write_bytes(b"iris storage probe\n")
        try:
            _reflink(source, clone)
            reflink = clone.read_bytes() == source.read_bytes()
        except OSError:
            reflink = False
        try:
            os.link(source, link)
            hardlink = True
        except OSError:
            hardlink = False
        return reflink, hardlink
    finally:
        # Only the probe's own scratch files.
        for path in (source, clone, link):
            path.unlink(missing_ok=True)


def resolve(requested: str, directory: Path) -> StorageReport:
    """Choose the strategy for ``directory``, failing early on an impossible one."""
    requested = (requested or DEFAULT_STRATEGY).strip().lower()
    if requested not in STRATEGIES:
        raise StorageConfigError(
            f"IRIS_SPACE_STORAGE={requested!r}: use um de: {', '.join(STRATEGIES)}"
        )
    reflink, hardlink = probe(directory)
    if requested == "reflink" and not reflink:
        raise StorageConfigError(f"o disco de {directory} não suporta reflink")
    if requested == "hardlink" and not hardlink:
        raise StorageConfigError(f"o disco de {directory} não suporta hard link")
    strategy = requested
    if requested == "auto":
        strategy = "reflink" if reflink else "copy"
    return StorageReport(
        path=str(directory),
        filesystem=filesystem_type(directory),
        reflink=reflink,
        hardlink=hardlink,
        requested=requested,
        strategy=strategy,
    )


def clone_file(source: Path, destination: Path, strategy: str) -> str:
    """Materialise ``source`` at ``destination``; return the method actually used.

    A per-file fallback to a copy is expected, not an error: the source may be
    on another mount (a legacy library outside ``data/``) even when ``data/``
    itself supports reflinks.
    """
    if strategy == "reflink":
        try:
            _reflink(source, destination)
            return "reflink"
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED:
                raise
            destination.unlink(missing_ok=True)
    elif strategy == "hardlink":
        try:
            os.link(source, destination)
            return "hardlink"
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED:
                raise
    shutil.copyfile(source, destination)
    return "copy"


def main(argv: list[str]) -> int:
    directory = Path(argv[1] if len(argv) > 1 else "data")
    requested = argv[2] if len(argv) > 2 else os.environ.get("IRIS_SPACE_STORAGE", "auto")
    try:
        report = resolve(requested, directory)
    except StorageConfigError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
