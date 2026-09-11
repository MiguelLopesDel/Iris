"""Hot paths look records up by key instead of walking the library.

Three request handlers used to scan every record: `/media/` rebuilt the whole
allow-list (one resolve() syscall per record — 330 ms for a 17k library, on the
event loop, for every original opened), `/api/trash` scanned the library once
per selected id, and `/api/info` recounted extensions on every poll.

The risk introduced by caching them is staleness, so these tests pin both
halves: the index must be used, and it must disappear when the backend reloads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import server
from core.search_types import IndexRecord


def _record(index: int, db_id: int, path: Path) -> IndexRecord:
    return IndexRecord(
        index=index,
        db_id=db_id,
        arquivo=path.name,
        caminho=str(path),
        texto_extraido="",
        descricao_ia="",
        tags="",
        embedding=None,
        desc_embedding=None,
        resolved_path=str(path),
    )


class _CountingBackend:
    """Backend that reports how often the full library was walked."""

    def __init__(self, records: list[IndexRecord]):
        self._records = records
        self.scans = 0

    def get_all_records(self) -> list[IndexRecord]:
        self.scans += 1
        return self._records


@pytest.fixture
def backend(tmp_path, monkeypatch):
    files = []
    for i in range(3):
        path = tmp_path / f"photo{i}.jpg"
        path.write_bytes(b"bytes")
        files.append(path)
    instance = _CountingBackend([_record(i, i + 1, p) for i, p in enumerate(files)])
    monkeypatch.setattr(server, "_get_backend", lambda: instance)
    server._invalidate_view_caches()
    yield instance
    server._invalidate_view_caches()


def test_looking_up_many_ids_walks_the_library_once(backend):
    """This is the shape that made deleting a selection scan per item."""
    for db_id in (1, 2, 3, 1, 2, 3):
        assert server._record_for_db_id(db_id) is not None

    assert backend.scans == 1


def test_the_two_lookup_helpers_share_one_index(backend):
    assert server._record_by_db_id(2) is server._record_for_db_id(2)

    assert backend.scans == 1


def test_an_unknown_id_returns_nothing_without_a_rescan(backend):
    server._record_for_db_id(1)

    assert server._record_for_db_id(9999) is None
    assert backend.scans == 1


def test_the_media_allow_list_is_resolved_once(backend, tmp_path):
    first = server._allowed_media_paths()
    second = server._allowed_media_paths()

    assert first == second
    assert backend.scans == 1
    assert str((tmp_path / "photo0.jpg").resolve()) in first


def test_the_allow_list_still_refuses_a_file_outside_the_catalog(backend, tmp_path):
    """Caching must not widen what the route is willing to serve."""
    intruder = tmp_path / "secret.txt"
    intruder.write_text("nope")

    assert str(intruder.resolve()) not in server._allowed_media_paths()


def test_extension_counts_are_not_recomputed_per_poll(backend):
    assert server._extension_counts(backend) == {".jpg": 3}
    assert server._extension_counts(backend) == {".jpg": 3}

    assert backend.scans == 1


def test_reloading_the_backend_drops_every_cached_index(backend, tmp_path):
    """A newly imported file must become servable without a restart.

    These caches join the invalidation contract `_sorted_records_cache` already
    relies on: cleared whenever the backend is rebuilt.
    """
    server._allowed_media_paths()
    server._record_for_db_id(1)
    server._extension_counts(backend)
    novo = tmp_path / "imported.png"
    novo.write_bytes(b"bytes")
    backend._records.append(_record(3, 4, novo))

    server._invalidate_view_caches()

    assert str(novo.resolve()) in server._allowed_media_paths()
    assert server._record_for_db_id(4) is not None
    assert server._extension_counts(backend) == {".jpg": 3, ".png": 1}


def test_coverage_separates_never_examined_from_no_hash_possible(tmp_path, monkeypatch):
    """A detector's quality and its coverage are different numbers.

    perceptual_hash is an additive column that had no backfill, so 79% of a real
    catalogue was null while the duplicate finder answered "460 groups" with no
    hint that four fifths of the library had never been examined. The column
    already carries three meanings and the report has to keep them apart.
    """
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, perceptual_hash TEXT)")
    connection.executemany(
        "INSERT INTO memes (perceptual_hash) VALUES (?)",
        [("ff00ff00ff00ff00",), ("ff00ff00ff00ff01",), ("",), (None,), (None,), (None,)],
    )
    engine = type("Engine", (), {"db": type("Db", (), {"get_connection": staticmethod(lambda: connection)})()})()
    backend = type("Backend", (), {"engine": engine})()
    server._invalidate_view_caches()

    coverage = server._fingerprint_coverage(backend)

    assert coverage["total"] == 6
    assert coverage["phash"] == {
        "evaluated": 2,
        "unavailable": 1,
        "pending": 3,
        "ratio": round(2 / 6, 4),
    }
    server._invalidate_view_caches()
    connection.close()


def test_coverage_is_empty_without_a_local_catalogue(monkeypatch):
    backend = type("Backend", (), {"engine": None})()
    server._invalidate_view_caches()

    assert server._fingerprint_coverage(backend) == {}
