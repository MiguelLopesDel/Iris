"""The private library's Iris trash, without the HTTP layer."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core import library_trash
from core.concepts import create_concept_tables
from core.faces import create_face_tables, create_person
from core.indexer_db import init_db
from core.library_trash import TrashItemNotFound, TrashRestoreConflict

T0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def library(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    db = tmp_path / "users" / "1" / "iris.db"
    media = tmp_path / "users" / "1" / "media"
    media.mkdir(parents=True)
    connection = init_db(db)
    create_face_tables(connection)
    create_concept_tables(connection)
    files: dict[str, Path] = {}
    for n, name in enumerate(("praia.jpg", "bolo.jpg", "gato.jpg"), start=1):
        path = media / name
        path.write_bytes(f"photo {n}".encode())
        files[name] = path
        connection.execute(
            "INSERT INTO memes (id, arquivo, caminho, embedding, content_hash, descricao_ia)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (n, name, str(path), bytes([n]) * 16, f"hash-{n}", f"descrição {n}"),
        )
    connection.execute("INSERT INTO collections (id, name, created_at) VALUES (1, 'Férias', '')")
    connection.execute("INSERT INTO media_collections (meme_id, collection_id) VALUES (1, 1)")
    person = create_person(connection, "Ana")
    connection.execute(
        "INSERT INTO faces (meme_id, person_id, embedding, created_at) VALUES (1, ?, ?, '')",
        (person, b"\x01" * 8),
    )
    connection.commit()
    connection.close()
    return db, files


def _count(db: Path, sql: str) -> int:
    with sqlite3.connect(db) as connection:
        return int(connection.execute(sql).fetchone()[0])


def test_trash_removes_everything_and_restore_brings_it_all_back(library) -> None:
    db, files = library
    moved = library_trash.move_to_trash(db, {1: files["praia.jpg"]}, now=T0)
    assert moved == [1]

    assert _count(db, "SELECT COUNT(*) FROM memes WHERE id = 1") == 0
    assert _count(db, "SELECT COUNT(*) FROM media_collections") == 0  # cascaded
    assert _count(db, "SELECT COUNT(*) FROM faces") == 0
    assert not files["praia.jpg"].exists()
    held = library_trash.trashed_file(db, 1)
    assert held.is_relative_to(library_trash.trash_root(db))
    assert held.read_bytes() == b"photo 1"
    assert _count(db, "SELECT COUNT(*) FROM deleted_media WHERE content_hash = 'hash-1'") == 1

    [item] = library_trash.list_trash(db, days=30)
    assert (item.id, item.name, item.has_file) == (1, "praia.jpg", True)
    assert item.purge_after == (T0 + timedelta(days=30)).isoformat()

    restored, skipped = library_trash.restore(db, [1])
    assert restored == [(1, "hash-1")] and skipped == 0
    assert files["praia.jpg"].read_bytes() == b"photo 1"
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT arquivo, embedding, descricao_ia FROM memes WHERE id = 1"
        ).fetchone()
        assert row == ("praia.jpg", b"\x01" * 16, "descrição 1")  # same id, same bytes
        assert connection.execute("SELECT collection_id FROM media_collections").fetchall() == [(1,)]
        assert connection.execute("SELECT meme_id, person_id FROM faces").fetchall() == [(1, 1)]
    assert _count(db, "SELECT COUNT(*) FROM deleted_media WHERE content_hash = 'hash-1'") == 0
    assert library_trash.list_trash(db, days=30) == []
    assert not (library_trash.trash_root(db) / "1").exists()


def test_a_deleted_album_does_not_block_the_photo_coming_back(library) -> None:
    db, files = library
    library_trash.move_to_trash(db, {1: files["praia.jpg"]}, now=T0)
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM collections WHERE id = 1")
    restored, skipped = library_trash.restore(db, [1])
    assert restored == [(1, "hash-1")] and skipped == 1  # the membership had nowhere to go
    assert _count(db, "SELECT COUNT(*) FROM faces") == 1


def test_restore_refuses_to_overwrite_a_new_file_in_the_same_place(library) -> None:
    db, files = library
    library_trash.move_to_trash(db, {2: files["bolo.jpg"]}, now=T0)
    files["bolo.jpg"].write_bytes(b"a different photo")
    with pytest.raises(TrashRestoreConflict):
        library_trash.restore(db, [2])
    assert files["bolo.jpg"].read_bytes() == b"a different photo"
    assert library_trash.trashed_file(db, 2).read_bytes() == b"photo 2"
    assert _count(db, "SELECT COUNT(*) FROM memes WHERE id = 2") == 0


def test_an_item_whose_file_was_already_gone_can_still_be_trashed(library) -> None:
    db, _ = library
    assert library_trash.move_to_trash(db, {3: None}, now=T0) == [3]
    [item] = library_trash.list_trash(db, days=30)
    assert item.has_file is False
    with pytest.raises(TrashItemNotFound):
        library_trash.trashed_file(db, 3)
    assert library_trash.restore(db, [3])[0] == [(3, "hash-3")]
    assert _count(db, "SELECT COUNT(*) FROM memes WHERE id = 3") == 1


def test_a_failed_move_leaves_the_catalogue_untouched(library, monkeypatch) -> None:
    db, files = library

    def broken_move(source: Path, target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(library_trash, "_move", broken_move)
    with pytest.raises(OSError):
        library_trash.move_to_trash(db, {1: files["praia.jpg"]}, now=T0)
    assert _count(db, "SELECT COUNT(*) FROM memes WHERE id = 1") == 1
    assert _count(db, "SELECT COUNT(*) FROM media_collections") == 1
    assert library_trash.list_trash(db, days=30) == []
    assert files["praia.jpg"].exists()


def test_purge_waits_for_retention_then_deletes_the_original(library) -> None:
    db, files = library
    library_trash.move_to_trash(db, {1: files["praia.jpg"], 2: files["bolo.jpg"]}, now=T0)
    held = library_trash.trashed_file(db, 1)
    assert library_trash.purge_expired(db, 30, now=T0 + timedelta(days=29)) == 0
    assert held.exists()
    assert library_trash.purge_expired(db, 30, now=T0 + timedelta(days=31)) == 2
    assert not held.exists()
    assert library_trash.list_trash(db, days=30) == []
    with pytest.raises(TrashItemNotFound):
        library_trash.restore(db, [1])


def test_unknown_ids_are_ignored_on_trash_and_refused_on_restore(library) -> None:
    db, _ = library
    assert library_trash.move_to_trash(db, {99: None}, now=T0) == []
    with pytest.raises(TrashItemNotFound):
        library_trash.restore(db, [99])
