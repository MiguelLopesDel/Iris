"""Whole-instance backup: consistent, incremental, verifiable, restorable."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from core import instance_backup
from core.auth import hash_password
from core.indexer_db import init_db
from core.instance_backup import BackupError
from core.library_trash import move_to_trash
from core.shared_spaces import add_member, create_space
from core.space_catalog import add_item, space_root
from core.users_db import create_user

PASSWORD = "synthetic backup password"
T0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def _image(path: Path, colour: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 16), colour).save(path)
    return path


def _catalogue(db_path: Path, *images: Path) -> None:
    init_db(db_path).close()
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            "INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
            [(image.name, str(image), b"\0" * 16) for image in images],
        )


@pytest.fixture
def instance(tmp_path: Path) -> dict[str, Path]:
    """Two accounts, a shared space, a legacy media root and rebuildable clutter."""
    data, media = tmp_path / "data", tmp_path / "media"
    alice = create_user(data / "users.db", data, username="alice",
                        password_hash=hash_password(PASSWORD), is_admin=True)
    bob = create_user(data / "users.db", data, username="bob",
                      password_hash=hash_password(PASSWORD))
    alice_photo = _image(alice.media_root / "alice.jpg", (200, 20, 20))
    legacy = _image(media / "old" / "legacy.jpg", (20, 200, 20))  # outside data/
    _catalogue(alice.db_path, alice_photo, legacy)
    _catalogue(bob.db_path, _image(bob.media_root / "bob.jpg", (20, 20, 200)))
    (data / "secret_key").write_bytes(b"synthetic secret")

    space = create_space(data / "users.db", alice.id, "Family")
    add_member(data / "users.db", space.id, alice.id, "bob", "viewer")
    add_item(space_root(data, space.id), alice_photo, "alice.jpg", alice.id)

    # Rebuildable or transient: must stay out of the backup.
    (data / "users" / "1" / "thumbnails").mkdir()
    (data / "users" / "1" / "thumbnails" / "t.jpg").write_bytes(b"thumb")
    (data / "users" / "1" / "iris_image.faiss").write_bytes(b"index")
    (data / "users" / "1" / "iris.embedding.vec").write_bytes(b"vectors")
    (data / "users" / "1" / "sync_uploads").mkdir()
    (data / "users" / "1" / "sync_uploads" / "x.part").write_bytes(b"half")
    return {"data": data, "media": media, "space_id": Path(str(space.id))}


def _roots(instance: dict[str, Path]) -> dict[str, Path]:
    return {"data": instance["data"], "media": instance["media"]}


def _manifest(snapshot: Path) -> dict:
    return json.loads((snapshot / "manifest.json").read_text())


def _downgrade_space_catalogue_to_v2(root: Path) -> None:
    """Write the prior on-disk schema, preserving the space's catalogued items."""
    database = root / "space.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        items = connection.execute(
            "SELECT id, sha256, storage_name, original_name, mime_type, size_bytes, "
            "added_by, added_at, removed_at, removed_by, storage_method, purged_at "
            "FROM items"
        ).fetchall()
        usage = connection.execute("SELECT bytes FROM usage WHERE id = 1").fetchone()[0]
    finally:
        connection.close()
    Path(f"{database}-wal").unlink(missing_ok=True)
    Path(f"{database}-shm").unlink(missing_ok=True)

    old_database = root / "space-v2.db"
    connection = sqlite3.connect(old_database)
    try:
        connection.executescript(
            """
            CREATE TABLE items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL,
                storage_name TEXT NOT NULL,
                original_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                added_by INTEGER,
                added_at TEXT NOT NULL,
                removed_at TEXT,
                removed_by INTEGER,
                storage_method TEXT NOT NULL DEFAULT 'copy',
                purged_at TEXT
            );
            CREATE UNIQUE INDEX idx_items_visible_sha256 ON items(sha256)
                WHERE removed_at IS NULL;
            CREATE INDEX idx_items_sha256 ON items(sha256);
            CREATE INDEX idx_items_trash ON items(removed_at)
                WHERE removed_at IS NOT NULL AND purged_at IS NULL;
            CREATE TABLE usage (id INTEGER PRIMARY KEY CHECK (id = 1), bytes INTEGER NOT NULL);
            PRAGMA user_version = 2;
            """
        )
        connection.executemany(
            "INSERT INTO items (id, sha256, storage_name, original_name, mime_type, "
            "size_bytes, added_by, added_at, removed_at, removed_by, storage_method, purged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            items,
        )
        connection.execute("INSERT INTO usage (id, bytes) VALUES (1, ?)", (usage,))
        connection.commit()
    finally:
        connection.close()
    database.unlink()
    old_database.rename(database)


def test_snapshot_holds_the_recovery_unit_and_nothing_rebuildable(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    summary = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0)
    assert summary.snapshot.name == "iris-backup-20260923T120000Z"
    assert summary.snapshot.stat().st_mode & 0o077 == 0
    paths = {f"{e['root']}/{e['path']}" for e in _manifest(summary.snapshot)["files"]}
    space = instance["space_id"]
    for expected in (
        "data/users.db", "data/secret_key", "data/users/1/iris.db", "data/users/2/iris.db",
        "data/users/1/media/alice.jpg", "data/users/2/media/bob.jpg",
        f"data/spaces/{space}/space.db", "media/old/legacy.jpg",
    ):
        assert expected in paths, expected
    assert any(p.startswith(f"data/spaces/{space}/media/") for p in paths)
    for rebuildable in ("thumbnails", ".faiss", ".vec", ".part", "-wal", "-shm"):
        assert not any(rebuildable in p for p in paths), rebuildable
    assert summary.warnings == []
    assert instance_backup.verify(summary.snapshot) == []


def test_verify_and_restore_support_a_read_only_snapshot(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    paths_before = sorted(path.relative_to(snapshot) for path in snapshot.rglob("*"))
    paths = [snapshot, *snapshot.rglob("*")]
    try:
        for path in paths:
            path.chmod(0o555 if path.is_dir() else 0o444)

        assert instance_backup.verify(snapshot) == []
        restored = instance_backup.restore(snapshot, {
            "data": tmp_path / "restored-data",
            "media": tmp_path / "restored-media",
        })
        assert restored == {}
        assert (tmp_path / "restored-data/users.db").is_file()
        assert (tmp_path / "restored-media/old/legacy.jpg").is_file()
        assert sorted(path.relative_to(snapshot) for path in snapshot.rglob("*")) == paths_before
    finally:
        for path in reversed(paths):
            path.chmod(0o755 if path.is_dir() else 0o644)


def test_second_snapshot_links_unchanged_files_and_copies_new_ones(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    dest = tmp_path / "backups"
    first = instance_backup.create(_roots(instance), dest, now=T0)
    _image(instance["data"] / "users" / "2" / "media" / "new.jpg", (1, 2, 3))
    second = instance_backup.create(_roots(instance), dest, now=T0 + timedelta(hours=1))

    photo = "data/users/2/media/bob.jpg"
    assert (first.snapshot / photo).stat().st_ino == (second.snapshot / photo).stat().st_ino
    assert (second.snapshot / "data/users/2/media/new.jpg").is_file()
    # Databases are always a fresh consistent copy, never a link.
    db = "data/users.db"
    assert (first.snapshot / db).stat().st_ino != (second.snapshot / db).stat().st_ino
    assert second.linked >= 4 and second.files == first.files + 1
    assert instance_backup.verify(second.snapshot) == []


def test_a_moved_file_is_linked_by_content_not_copied_again(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    """Attaching a library moves every original: the backup must not store them twice."""
    dest = tmp_path / "backups"
    old = instance["data"] / "import" / "library" / "clip.mp4"
    old.parent.mkdir(parents=True)
    old.write_bytes(os.urandom(300_000))
    first = instance_backup.create(_roots(instance), dest, now=T0)

    moved = instance["data"] / "archive" / "clip.mp4"
    moved.parent.mkdir(parents=True)
    os.rename(old, moved)
    second = instance_backup.create(_roots(instance), dest, now=T0 + timedelta(hours=1))

    assert (second.snapshot / "data/archive/clip.mp4").stat().st_ino == (
        first.snapshot / "data/import/library/clip.mp4"
    ).stat().st_ino
    assert not (second.snapshot / "data/import/library/clip.mp4").exists()
    # Only the databases (always fresh) were written again.
    databases = sum(1 for e in _manifest(second.snapshot)["files"] if e["kind"] == "sqlite")
    assert second.copied == databases
    assert instance_backup.verify(second.snapshot) == []


def test_identical_files_in_one_snapshot_are_stored_once(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    content = os.urandom(200_000)
    for name in ("a.bin", "copy-of-a.bin"):
        (instance["data"] / "extra").mkdir(exist_ok=True)
        (instance["data"] / "extra" / name).write_bytes(content)
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    a, b = snapshot / "data/extra/a.bin", snapshot / "data/extra/copy-of-a.bin"
    assert a.stat().st_ino == b.stat().st_ino and b.read_bytes() == content
    assert instance_backup.verify(snapshot) == []


def test_file_edited_in_place_with_the_same_size_is_copied_again(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    dest = tmp_path / "backups"
    instance_backup.create(_roots(instance), dest, now=T0)
    photo = instance["data"] / "users" / "2" / "media" / "bob.jpg"
    edited = bytearray(photo.read_bytes())
    edited[-3] ^= 0xFF  # same size, different bytes, e.g. a rotation flag
    photo.write_bytes(bytes(edited))
    stat = photo.stat()
    os.utime(photo, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    second = instance_backup.create(_roots(instance), dest, now=T0 + timedelta(hours=1))
    assert (second.snapshot / "data/users/2/media/bob.jpg").read_bytes() == bytes(edited)
    assert instance_backup.verify(second.snapshot) == []


def test_disk_without_hard_links_falls_back_to_copies(
    instance: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "backups"
    instance_backup.create(_roots(instance), dest, now=T0)

    def no_links(*_: object) -> None:
        raise OSError(1, "Operation not permitted")  # exFAT

    monkeypatch.setattr(instance_backup.os, "link", no_links)
    second = instance_backup.create(_roots(instance), dest, now=T0 + timedelta(hours=1))
    assert second.linked == 0 and second.warnings == []
    assert instance_backup.verify(second.snapshot) == []


def test_uncommitted_writes_are_not_captured(instance: dict[str, Path], tmp_path: Path) -> None:
    writer = sqlite3.connect(instance["data"] / "users.db")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE users SET display_name = 'half-written' WHERE username = 'bob'")
    try:
        snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    finally:
        writer.rollback()
        writer.close()
    with sqlite3.connect(snapshot / "data" / "users.db") as copy:
        names = [row[0] for row in copy.execute("SELECT display_name FROM users")]
    assert "half-written" not in names


def test_originals_outside_the_backed_up_roots_are_reported(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    dest = tmp_path / "backups"
    with pytest.raises(BackupError, match="legacy.jpg"):
        instance_backup.create({"data": instance["data"]}, dest, now=T0)
    assert list(dest.glob("iris-backup-*")) == []


def test_missing_media_root_does_not_publish_a_partial_snapshot(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    dest = tmp_path / "backups"
    with pytest.raises(BackupError, match="media"):
        instance_backup.create(
            {"data": instance["data"], "media": tmp_path / "missing"}, dest, now=T0
        )
    assert list(dest.glob("iris-backup-*")) == []


def _point_alice_at(instance: dict[str, Path], path: Path) -> None:
    with sqlite3.connect(instance["data"] / "users" / "1" / "iris.db") as connection:
        connection.execute("UPDATE memes SET caminho = ? WHERE arquivo = 'alice.jpg'", (str(path),))


def _no_copying(monkeypatch: pytest.MonkeyPatch) -> None:
    def copying(*_: object) -> str:
        raise AssertionError("nothing may be copied once an original is missing unaccepted")

    monkeypatch.setattr(instance_backup, "_copy_hashing", copying)


def test_a_missing_original_is_never_accepted_silently(
    instance: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part of a catalogue missing on the first backup: lost just now, or long ago? Ask."""
    gone = instance["data"] / "users" / "1" / "media" / "gone.jpg"
    _point_alice_at(instance, gone)  # alice's legacy photo is still there
    dest = tmp_path / "backups"
    _no_copying(monkeypatch)
    with pytest.raises(BackupError, match=r"1 originais.*gone\.jpg.*--accept-missing"):
        instance_backup.create(_roots(instance), dest, now=T0)
    assert list(dest.glob("iris-backup-*")) == [] and list(dest.glob(".incomplete-*")) == []
    assert not (instance["data"] / instance_backup.ACCEPTED_MISSING_FILE).exists()


def test_accepted_absences_are_reported_and_new_ones_fail_again(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    gone = instance["data"] / "users" / "1" / "media" / "gone.jpg"
    _point_alice_at(instance, gone)
    dest = tmp_path / "backups"
    accepted = instance_backup.create(_roots(instance), dest, now=T0, accept_missing=True)

    assert _manifest(accepted.snapshot)["missing_originals"] == [f"users/1/iris.db:{gone}"]
    assert any("1 itens do catálogo sem o arquivo original, aceitos" in w for w in accepted.warnings)
    # The decision is part of the instance, so it travels with the backup.
    assert (accepted.snapshot / "data" / instance_backup.ACCEPTED_MISSING_FILE).is_file()
    assert instance_backup.verify(accepted.snapshot) == []

    later = instance_backup.create(_roots(instance), dest, now=T0 + timedelta(hours=1))
    assert _manifest(later.snapshot)["missing_originals"] == [f"users/1/iris.db:{gone}"]

    # A new absence is a new decision.
    with sqlite3.connect(instance["data"] / "users" / "1" / "iris.db") as connection:
        connection.execute("INSERT INTO memes (arquivo, caminho, embedding) VALUES ('new.jpg', ?, ?)",
                           (str(gone.with_name("new.jpg")), b"\0" * 16))
    with pytest.raises(BackupError, match=r"new\.jpg"):
        instance_backup.create(_roots(instance), dest, now=T0 + timedelta(hours=2))


def test_an_original_held_by_the_previous_backup_that_vanished_stops_it_before_copying(
    instance: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "backups"
    instance_backup.create(_roots(instance), dest, now=T0)
    (instance["data"] / "users" / "1" / "media" / "alice.jpg").unlink()  # deleted outside Iris
    with monkeypatch.context() as patched:
        _no_copying(patched)
        with pytest.raises(BackupError, match=r"sumiram.*alice\.jpg.*restaure"):
            instance_backup.create(_roots(instance), dest, now=T0 + timedelta(hours=1))
    assert len(list(dest.glob("iris-backup-*"))) == 1
    # Accepting the loss is still possible, explicitly.
    accepted = instance_backup.create(_roots(instance), dest, now=T0 + timedelta(hours=2), accept_missing=True)
    assert instance_backup.verify(accepted.snapshot) == []


def test_a_catalogue_with_no_original_at_all_looks_like_an_unmounted_disk(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    (instance["data"] / "users" / "2" / "media" / "bob.jpg").unlink()
    dest = tmp_path / "backups"
    with pytest.raises(BackupError, match=r"nenhum original de users/2/iris.db.*bob\.jpg.*montado"):
        instance_backup.create(_roots(instance), dest, now=T0)
    assert list(dest.glob("iris-backup-*")) == []


def test_relinked_library_path_takes_precedence_over_stale_legacy_path(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    library = instance["data"] / "users" / "1" / "library" / "default"
    photo = _image(library / "relinked.jpg", (3, 4, 5))
    database = instance["data"] / "users" / "1" / "iris.db"
    with sqlite3.connect(database) as connection:
        library_id = connection.execute(
            "INSERT INTO media_libraries (name, root_path) VALUES (?, ?)",
            ("default", str(library)),
        ).lastrowid
        connection.execute(
            "INSERT INTO memes (arquivo, caminho, library_id, storage_path, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            (photo.name, "/old/not/there/relinked.jpg", library_id, photo.name, b"\0" * 16),
        )
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    assert instance_backup.verify(snapshot) == []


def test_missing_trashed_original_does_not_publish_snapshot(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    database = instance["data"] / "users" / "2" / "iris.db"
    photo = instance["data"] / "users" / "2" / "media" / "bob.jpg"
    with sqlite3.connect(database) as connection:
        item_id = connection.execute("SELECT id FROM memes WHERE arquivo = 'bob.jpg'").fetchone()[0]
    move_to_trash(database, {item_id: photo})
    held = database.parent / "trash" / str(item_id) / photo.name
    held.unlink()
    with pytest.raises(BackupError, match="bob.jpg"):
        instance_backup.create(_roots(instance), tmp_path / "backups", now=T0)


def test_missing_session_secret_does_not_publish_snapshot(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    (instance["data"] / "secret_key").unlink()
    with pytest.raises(BackupError, match="secret_key"):
        instance_backup.create(_roots(instance), tmp_path / "backups", now=T0)


def test_effective_environment_secret_is_captured(
    instance: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IRIS_SECRET_KEY", "effective synthetic secret")
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    assert (snapshot / "data" / "secret_key").read_text() == "effective synthetic secret"
    assert instance_backup.verify(snapshot) == []


def test_database_change_during_media_copy_does_not_publish_snapshot(
    instance: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_copy = instance_backup._copy_hashing
    changed = False

    def copy_while_account_changes(source: Path, target: Path) -> str:
        nonlocal changed
        result = original_copy(source, target)
        if source.suffix == ".jpg" and not changed:
            changed = True
            with sqlite3.connect(instance["data"] / "users.db") as connection:
                connection.execute(
                    "UPDATE users SET display_name = 'changed' WHERE username = 'bob'"
                )
        return result

    monkeypatch.setattr(instance_backup, "_copy_hashing", copy_while_account_changes)
    dest = tmp_path / "backups"
    with pytest.raises(BackupError, match="mudou durante"):
        instance_backup.create(_roots(instance), dest, now=T0)
    assert changed
    assert list(dest.glob("iris-backup-*")) == []


def test_original_change_during_copy_does_not_publish_snapshot(
    instance: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_copy = instance_backup._copy_hashing
    changed = False

    def copy_while_photo_changes(source: Path, target: Path) -> str:
        nonlocal changed
        result = original_copy(source, target)
        if source.suffix == ".jpg" and not changed:
            changed = True
            source.write_bytes(source.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(instance_backup, "_copy_hashing", copy_while_photo_changes)
    dest = tmp_path / "backups"
    with pytest.raises(BackupError, match="mudou durante"):
        instance_backup.create(_roots(instance), dest, now=T0)
    assert changed
    assert list(dest.glob("iris-backup-*")) == []


def test_verify_rejects_manifest_that_omits_a_catalogued_photo(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = [
        entry for entry in manifest["files"]
        if entry["path"] != "users/1/media/alice.jpg"
    ]
    os.chmod(manifest_path, 0o644)
    manifest_path.write_text(json.dumps(manifest))
    assert any("alice.jpg" in problem for problem in instance_backup.verify(snapshot))


def test_verify_works_after_the_original_disk_is_lost(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    instance["data"].rename(tmp_path / "lost-data")
    instance["media"].rename(tmp_path / "lost-media")
    assert instance_backup.verify(snapshot) == []


def test_verify_finds_corruption_and_interrupted_snapshots(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    photo = snapshot / "data" / "users" / "2" / "media" / "bob.jpg"
    os.chmod(photo, 0o644)
    with photo.open("r+b") as handle:  # bit rot
        handle.seek(10)
        handle.write(b"\xff")
    assert instance_backup.verify(snapshot) == ["conteúdo alterado: data/users/2/media/bob.jpg"]

    partial = tmp_path / "backups" / ".incomplete-20260923T130000Z"
    partial.mkdir()
    assert instance_backup.verify(partial) != []


def test_refuses_unsafe_destinations_and_non_instances(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    with pytest.raises(BackupError):
        instance_backup.create(_roots(instance), instance["data"] / "backups")
    with pytest.raises(BackupError):
        instance_backup.create({"data": tmp_path / "empty"}, tmp_path / "backups")


def test_restore_verifies_first_and_never_deletes_the_live_state(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    photo = snapshot / "data" / "users" / "2" / "media" / "bob.jpg"
    os.chmod(photo, 0o644)
    photo.write_bytes(b"corrupt")
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(BackupError):
        instance_backup.restore(snapshot, _roots(instance))
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # nothing touched


def test_restored_instance_boots_with_accounts_libraries_and_spaces(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    data = instance["data"]
    space_id = int(instance["space_id"].name)
    # Simulate the prior release's space database before taking a real snapshot.
    _downgrade_space_catalogue_to_v2(space_root(data, space_id))
    snapshot = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0).snapshot
    alice_photo = data / "users" / "1" / "media" / "alice.jpg"
    expected = hashlib.sha256(alice_photo.read_bytes()).hexdigest()

    # Disaster: a library loses its photo and its catalogue.
    alice_photo.rename(tmp_path / "lost.jpg")
    (data / "users" / "1" / "iris.db").rename(tmp_path / "lost.db")

    kept = instance_backup.restore(snapshot, _roots(instance), now=T0 + timedelta(days=1))
    assert kept["data"].name == "data.before-restore-20260924T120000Z"
    assert (kept["data"] / "users.db").is_file()  # the old state is kept aside
    assert hashlib.sha256(alice_photo.read_bytes()).hexdigest() == expected
    assert alice_photo.stat().st_mode & 0o777 == 0o600  # writable again, private
    assert not (data / "users" / "1" / "thumbnails").exists()

    script = rf'''
import hashlib
from fastapi.testclient import TestClient
import server

with TestClient(server.app) as alice, TestClient(server.app) as bob:
    for name, client in (("alice", alice), ("bob", bob)):
        assert client.post("/api/auth/login", data={{
            "username": name, "password": "{PASSWORD}"}}).status_code == 200
    rows = alice.get("/api/records").json()["records"]
    assert {{r["arquivo"] for r in rows}} == {{"alice.jpg", "legacy.jpg"}}
    assert alice.get(rows[0]["thumbnail_url"]).status_code == 200
    items = bob.get("/api/spaces/{instance["space_id"]}/items").json()["items"]
    original = bob.get(items[0]["original_url"])
    assert hashlib.sha256(original.content).hexdigest() == "{expected}"
    albums = bob.get("/api/spaces/{instance["space_id"]}/albums")
    assert albums.status_code == 200, albums.text
    assert albums.json()["albums"] == []
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_LOAD_MODEL="0",
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(data / "spaces" / str(space_id) / "space.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_command_line_round_trip(instance: dict[str, Path], tmp_path: Path) -> None:
    dest = tmp_path / "backups"
    roots = [f"data={instance['data']}", f"media={instance['media']}"]
    assert instance_backup.main(["create", str(dest), *(f"--root={r}" for r in roots)]) == 0
    snapshot = next(dest.glob("iris-backup-*"))
    assert instance_backup.main(["verify", str(snapshot)]) == 0
    assert instance_backup.main(["restore", str(snapshot), *(f"--target={r}" for r in roots)]) == 0
    assert instance_backup.main(["verify", str(tmp_path / "nowhere")]) == 1


# -- version and retention ------------------------------------------------------


def _fake_snapshot(dest: Path, created: datetime, retention: str | None) -> Path:
    """A minimal snapshot as an older or newer Iris would have written it."""
    path = dest / f"iris-backup-{created.strftime('%Y%m%dT%H%M%SZ')}"
    path.mkdir(parents=True)
    manifest = {"format": 1, "created_at": created.isoformat(), "roots": {}, "files": [],
                "warnings": []}
    if retention is not None:
        manifest.update(retention=retention, iris_version="0.3.0", iris_commit="abc1234")
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path


def test_every_snapshot_records_the_iris_version_that_wrote_it(
    instance: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IRIS_COMMIT", "feed123")
    snapshot = instance_backup.create(_roots(instance), tmp_path / "b", now=T0).snapshot
    manifest = _manifest(snapshot)
    assert manifest["iris_version"] == instance_backup.iris_version()
    assert manifest["iris_commit"] == "feed123"
    assert manifest["retention"] == "policy"
    pinned = instance_backup.create(
        _roots(instance), tmp_path / "b", now=T0 + timedelta(hours=1), retention="pinned"
    ).snapshot
    assert _manifest(pinned)["retention"] == "pinned"
    [first, second] = instance_backup.snapshots(tmp_path / "b")
    assert (first.iris_version, first.iris_commit, first.prunable) == (
        instance_backup.iris_version(), "feed123", True
    )
    assert second.prunable is False


def test_policy_keeps_days_weeks_and_months_and_nothing_else(tmp_path: Path) -> None:
    dest = tmp_path / "b"
    start = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
    daily = [_fake_snapshot(dest, start + timedelta(days=n), "policy") for n in range(120)]
    policy = instance_backup.RetentionPolicy(daily=3, weekly=2, monthly=3)

    removed = set(instance_backup.prune(dest, policy))
    kept = [p for p in daily if p not in removed]
    assert [p.name[12:20] for p in kept] == [
        "20260228",  # month tier: newest of February
        "20260331",  # month tier: newest of March (April's is the 30th)
        "20260426",  # week tier: newest of ISO week 17 (week 18's is the 30th)
        "20260428", "20260429", "20260430",  # day tier: last three days
    ]
    assert all(p.exists() for p in kept) and not any(p.exists() for p in removed)
    assert daily[-1] in kept


def test_only_policy_snapshots_are_ever_pruned(tmp_path: Path) -> None:
    dest = tmp_path / "b"
    start = datetime(2025, 1, 1, tzinfo=UTC)
    legacy = [_fake_snapshot(dest, start + timedelta(days=n), None) for n in range(5)]
    pinned = _fake_snapshot(dest, start + timedelta(days=10), "pinned")
    managed = [_fake_snapshot(dest, start + timedelta(days=20 + n), "policy") for n in range(5)]
    policy = instance_backup.RetentionPolicy(daily=1, weekly=0, monthly=0)

    assert instance_backup.prune(dest, policy, dry_run=True) == managed[:-1]
    assert all(p.exists() for p in managed)  # a dry run deletes nothing

    assert instance_backup.prune(dest, policy) == managed[:-1]
    assert all(p.exists() for p in legacy) and pinned.exists() and managed[-1].exists()
    assert instance_backup.prune(dest, instance_backup.RetentionPolicy(0, 0, 0)) == []


def test_pruning_keeps_bytes_that_a_kept_snapshot_still_links(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    dest = tmp_path / "b"
    old = instance_backup.create(_roots(instance), dest, now=T0).snapshot
    new = instance_backup.create(_roots(instance), dest, now=T0 + timedelta(days=1)).snapshot
    removed = instance_backup.prune(dest, instance_backup.RetentionPolicy(1, 0, 0))
    assert removed == [old] and not old.exists()
    assert instance_backup.verify(new) == []  # its hard-linked photos are intact


def test_prune_finishes_interrupted_deletions_and_ignores_incomplete_backups(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "b"
    _fake_snapshot(dest, T0, "policy")
    leftover = dest / ".pruning-iris-backup-20200101T000000Z"
    (leftover / "data").mkdir(parents=True)
    partial = dest / ".incomplete-20260923T130000Z"
    partial.mkdir()
    instance_backup.prune(dest, instance_backup.RetentionPolicy(1, 0, 0))
    assert not leftover.exists() and partial.exists()


def test_one_backup_at_a_time_per_destination(instance: dict[str, Path], tmp_path: Path) -> None:
    dest = tmp_path / "b"
    dest.mkdir()
    with instance_backup._exclusive(dest):
        with pytest.raises(BackupError, match="andamento"):
            instance_backup.create(_roots(instance), dest, now=T0)
        with pytest.raises(BackupError, match="andamento"):
            instance_backup.prune(dest, instance_backup.RetentionPolicy())
