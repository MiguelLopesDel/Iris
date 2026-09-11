"""Embeddings as a file the kernel owns, not as memory the process owns.

A catalogue's embeddings are the largest thing the server holds: measured at 6.3
KB per item across the two modalities, which is 3.4 GB at the size an 8 GB
machine already struggles with and 300 GB at the size this project aims for.
They are also the easiest thing to stop holding. Contiguous, fixed width,
immutable between indexing runs, addressed by position -- exactly the shape
``mmap`` exists to serve.

The change is about ownership rather than about size. Read through SQLite into
a numpy array, the bytes live in the process and stay there. Read through a
memory map, the same bytes live in the page cache: the kernel keeps what is
being used, drops what is not under pressure, and shares them between processes
reading the same catalogue. A search that touches a thousand candidates pays
for a thousand candidates instead of for the catalogue.

**Random access needs saying so.** Measured cold, pulling 2,000 scattered rows
from a 614 MB map read 146 MB of pages -- twenty-four times what was asked for,
because the kernel reads ahead assuming a sequential scan. ``MADV_RANDOM`` turns
that off: the same access read 12 MB and ran in half the time. Without it the
map is worse than the array it replaces.

The sidecar is derived data and is rebuilt whenever it disagrees with the
catalogue, so a stale file degrades to a slower load rather than to wrong
vectors.
"""

from __future__ import annotations

import ctypes
import sqlite3
from pathlib import Path

import numpy as np

# Written at the head of the file so a map can be validated without reading it.
_MAGIC = b"IRISVEC1"
_HEADER_DTYPE = np.dtype(
    [("magic", "S8"), ("count", "<i8"), ("width", "<i8"), ("first_id", "<i8"), ("last_id", "<i8")]
)

_MADV_RANDOM = 1


def _advise_random(array: np.ndarray) -> None:
    """Tell the kernel not to read ahead. Silently skipped where unsupported."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.madvise(
            ctypes.c_void_p(array.ctypes.data),
            ctypes.c_size_t(array.nbytes),
            ctypes.c_int(_MADV_RANDOM),
        )
    except Exception:
        pass


# Which rows a sidecar covers. Every map must describe the same rows in the
# same order, or a position means a different item in each of them: the
# description column is missing on rows the image column has, so selecting by
# each column separately produced files of different lengths that indexed as if
# they were parallel.
SELECTOR = "embedding"


def sidecar_path(db_path: Path, column: str) -> Path:
    return db_path.with_name(f"{db_path.stem}.{column}.vec")


def _catalogue_shape(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """The rows every sidecar covers, and the first and last id among them."""
    row = conn.execute(
        f"SELECT COUNT(*), MIN(id), MAX(id) FROM memes WHERE {SELECTOR} IS NOT NULL"  # noqa: S608
    ).fetchone()
    return (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))


def build(conn: sqlite3.Connection, db_path: Path, column: str) -> Path | None:
    """Write every embedding of one column to its sidecar, streaming.

    Streamed on purpose: collecting the rows first would need the whole column
    in memory, which is the cost this exists to remove.
    """
    count, first_id, last_id = _catalogue_shape(conn)
    if not count:
        return None
    path = sidecar_path(db_path, column)
    temporary = path.with_suffix(".vec.tmp")

    width = 0
    written = 0
    with temporary.open("wb") as handle:
        handle.write(b"\0" * _HEADER_DTYPE.itemsize)
        for (blob,) in conn.execute(
            f"SELECT {column} FROM memes WHERE {SELECTOR} IS NOT NULL ORDER BY id"  # noqa: S608
        ):
            vector = np.frombuffer(blob, dtype=np.float32) if blob else None
            if vector is not None and not width:
                width = vector.shape[0]
            if vector is None or vector.shape[0] != width:
                # Absent here, or left by an older model: a zero row keeps this
                # file the same length and order as every other sidecar, which
                # is what lets one position mean one item across all of them.
                vector = np.zeros(width or 1, dtype=np.float32)
            handle.write(vector.tobytes())
            written += 1

    header = np.zeros(1, dtype=_HEADER_DTYPE)
    header[0] = (_MAGIC, written, width, first_id, last_id)
    with temporary.open("r+b") as handle:
        handle.write(header.tobytes())
    # Renamed at the end so a reader never sees a half-written map.
    temporary.replace(path)
    return path


def open_map(
    conn: sqlite3.Connection, db_path: Path, column: str, *, rebuild: bool = True
) -> np.ndarray | None:
    """The column as a read-only memory map, built or rebuilt if it disagrees.

    Returns None when there is nothing to map, so callers fall back to reading
    the column themselves rather than serving an empty matrix.
    """
    path = sidecar_path(db_path, column)
    expected = _catalogue_shape(conn)
    if not expected[0]:
        return None

    if not _matches(path, expected):
        if not rebuild:
            return None
        if build(conn, db_path, column) is None:
            return None
        if not _matches(path, expected):
            return None

    header = np.fromfile(path, dtype=_HEADER_DTYPE, count=1)[0]
    mapped = np.memmap(
        path,
        dtype=np.float32,
        mode="r",
        offset=_HEADER_DTYPE.itemsize,
        shape=(int(header["count"]), int(header["width"])),
    )
    _advise_random(mapped)
    return mapped


def _matches(path: Path, expected: tuple[int, int, int]) -> bool:
    """Whether the file on disk still describes this catalogue.

    Count with first and last id, not a checksum of every id: the point is to
    catch a sidecar left behind by an earlier catalogue without reading 400 MB
    of ids to do it. A mismatch costs a rebuild, never a wrong answer, because
    the file is derived data.
    """
    if not path.is_file():
        return False
    try:
        header = np.fromfile(path, dtype=_HEADER_DTYPE, count=1)
    except (OSError, ValueError):
        return False
    if not len(header) or bytes(header[0]["magic"]) != _MAGIC:
        return False
    count, first_id, last_id = expected
    stored = header[0]
    if int(stored["count"]) != count:
        return False
    if int(stored["first_id"]) != first_id or int(stored["last_id"]) != last_id:
        return False
    body = path.stat().st_size - _HEADER_DTYPE.itemsize
    return body == count * int(stored["width"]) * 4
