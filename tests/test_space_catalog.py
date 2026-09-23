"""The catalogue of one shared space, without the HTTP layer."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import fs_clone, space_catalog
from core.space_catalog import (
    SpaceCatalogVersionError,
    SpaceItemConflict,
    SpaceItemNotFound,
    SpaceItemPermissionDenied,
    SpaceQuotaExceeded,
    SpaceStorage,
)


def _file(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / "private" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_item_is_a_copy_independent_of_its_source(tmp_path: Path) -> None:
    root = tmp_path / "space"
    source = _file(tmp_path, "praia.jpg", b"synthetic bytes")
    item, created = space_catalog.add_item(root, source, "praia.jpg", added_by=7)
    assert created
    assert item.mime_type == "image/jpeg"
    assert item.size_bytes == len(b"synthetic bytes")

    source.rename(tmp_path / "moved-away.jpg")  # the private copy is gone
    _, stored = space_catalog.item_original(root, item.id)
    assert stored.read_bytes() == b"synthetic bytes"
    assert stored.is_relative_to(root)


def test_same_content_is_one_item_and_one_file(tmp_path: Path) -> None:
    root = tmp_path / "space"
    first, created = space_catalog.add_item(
        root, _file(tmp_path, "a.jpg", b"same"), "a.jpg", added_by=1
    )
    second, created_again = space_catalog.add_item(
        root, _file(tmp_path, "b.jpg", b"same"), "b.jpg", added_by=2
    )
    assert created and not created_again
    assert second.id == first.id and second.added_by == 1
    stored = [path for path in (root / "media").rglob("*") if path.is_file()]
    assert len(stored) == 1
    assert list((root / "incoming").iterdir()) == []


def test_pages_are_newest_first_and_contiguous(tmp_path: Path) -> None:
    root = tmp_path / "space"
    ids = [
        space_catalog.add_item(
            root, _file(tmp_path, f"{n}.png", bytes([n])), f"{n}.png", added_by=1
        )[0].id
        for n in range(5)
    ]
    first = space_catalog.list_items(root, limit=2)
    second = space_catalog.list_items(root, limit=2, before=first[-1].id)
    third = space_catalog.list_items(root, limit=2, before=second[-1].id)
    assert [item.id for item in first + second + third] == ids[::-1]


def test_removal_follows_role_and_keeps_bytes(tmp_path: Path) -> None:
    root = tmp_path / "space"
    item, _ = space_catalog.add_item(
        root, _file(tmp_path, "x.jpg", b"x"), "x.jpg", added_by=10
    )
    _, stored = space_catalog.item_original(root, item.id)

    with pytest.raises(SpaceItemPermissionDenied):
        space_catalog.remove_item(root, item.id, actor_id=10, role="viewer")
    with pytest.raises(SpaceItemPermissionDenied):
        space_catalog.remove_item(root, item.id, actor_id=11, role="contributor")
    space_catalog.remove_item(root, item.id, actor_id=10, role="contributor")

    with pytest.raises(SpaceItemNotFound):
        space_catalog.get_item(root, item.id)
    assert space_catalog.list_items(root, limit=10) == []
    assert stored.read_bytes() == b"x"  # recoverable, not unlinked

    again, created = space_catalog.add_item(
        root, _file(tmp_path, "y.jpg", b"x"), "y.jpg", added_by=12
    )
    assert created and again.id != item.id


def test_manager_removes_any_item(tmp_path: Path) -> None:
    root = tmp_path / "space"
    item, _ = space_catalog.add_item(
        root, _file(tmp_path, "m.jpg", b"m"), "m.jpg", added_by=1
    )
    space_catalog.remove_item(root, item.id, actor_id=2, role="manager")
    with pytest.raises(SpaceItemNotFound):
        space_catalog.item_original(root, item.id)


def test_unknown_newer_schema_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "space"
    space_catalog.list_items(root, limit=1)
    with sqlite3.connect(root / "space.db") as connection:
        connection.execute(f"PRAGMA user_version = {space_catalog.SCHEMA_VERSION + 1}")
    with pytest.raises(SpaceCatalogVersionError):
        space_catalog.list_items(root, limit=1)


def test_storage_name_ignores_unsafe_suffixes(tmp_path: Path) -> None:
    root = tmp_path / "space"
    item, _ = space_catalog.add_item(
        root, _file(tmp_path, "evil", b"e"), "../../evil.j/pg", added_by=1
    )
    _, stored = space_catalog.item_original(root, item.id)
    assert stored.is_relative_to(root / "media")
    assert item.original_name == "pg"


# -- storage strategies ----------------------------------------------------

def _supports(tmp_path: Path) -> tuple[bool, bool]:
    return fs_clone.probe(tmp_path / "probe")


def test_reflink_copy_is_independent_of_later_edits(tmp_path: Path) -> None:
    if not _supports(tmp_path)[0]:
        pytest.skip("filesystem without reflink")
    root = tmp_path / "space"
    source = _file(tmp_path, "r.jpg", b"original bytes")
    item, _ = space_catalog.add_item(
        root, source, "r.jpg", 1, SpaceStorage(strategy="reflink")
    )
    assert item.storage_method == "reflink"
    with source.open("r+b") as handle:  # an in-place edit of the private copy
        handle.write(b"EDITED")
    _, stored = space_catalog.item_original(root, item.id)
    assert stored.read_bytes() == b"original bytes"


def test_hardlink_shares_the_inode_which_is_why_it_is_opt_in(tmp_path: Path) -> None:
    if not _supports(tmp_path)[1]:
        pytest.skip("filesystem without hard links")
    root = tmp_path / "space"
    source = _file(tmp_path, "h.jpg", b"original bytes")
    item, _ = space_catalog.add_item(
        root, source, "h.jpg", 1, SpaceStorage(strategy="hardlink")
    )
    assert item.storage_method == "hardlink"
    _, stored = space_catalog.item_original(root, item.id)
    assert stored.stat().st_ino == source.stat().st_ino
    source.rename(tmp_path / "trashed.jpg")  # removing a name keeps the other
    assert stored.read_bytes() == b"original bytes"


def test_reflink_across_filesystems_falls_back_to_a_copy(tmp_path: Path) -> None:
    shm = Path("/dev/shm")
    if not shm.is_dir() or shm.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("no second filesystem to copy from")
    source = shm / f"iris-test-{tmp_path.name}.jpg"
    source.write_bytes(b"elsewhere")
    try:
        destination = tmp_path / "copy.jpg"
        assert fs_clone.clone_file(source, destination, "reflink") == "copy"
        assert destination.read_bytes() == b"elsewhere"
    finally:
        source.unlink()


def test_resolve_refuses_unknown_or_impossible_strategies(tmp_path: Path) -> None:
    with pytest.raises(fs_clone.StorageConfigError):
        fs_clone.resolve("dedupe-magic", tmp_path)
    report = fs_clone.resolve("auto", tmp_path)
    assert report.strategy == ("reflink" if report.reflink else "copy")
    assert report.filesystem != ""
    assert fs_clone.resolve("copy", tmp_path).strategy == "copy"


def test_same_bytes_under_another_extension_reuse_the_stored_file(tmp_path: Path) -> None:
    root = tmp_path / "space"
    first, _ = space_catalog.add_item(root, _file(tmp_path, "a.jpg", b"z"), "a.jpg", 1)
    space_catalog.remove_item(root, first.id, 1, "manager")
    second, created = space_catalog.add_item(root, _file(tmp_path, "a.png", b"z"), "a.png", 1)
    assert created
    stored = [path for path in (root / "media").rglob("*") if path.is_file()]
    assert len(stored) == 1
    assert space_catalog.usage_bytes(root) == 1


# -- quota -----------------------------------------------------------------

def test_quota_counts_distinct_bytes_and_refuses_cleanly(tmp_path: Path) -> None:
    root = tmp_path / "space"
    storage = SpaceStorage(quota_bytes=10)
    space_catalog.add_item(root, _file(tmp_path, "a", b"12345678"), "a.jpg", 1, storage)
    with pytest.raises(SpaceQuotaExceeded):
        space_catalog.add_item(root, _file(tmp_path, "b", b"abcdefgh"), "b.jpg", 1, storage)
    # Content already held costs nothing, so it still fits.
    _, created = space_catalog.add_item(
        root, _file(tmp_path, "c", b"12345678"), "c.jpg", 2, storage
    )
    assert not created
    assert space_catalog.usage_bytes(root) == 8
    assert len([p for p in (root / "media").rglob("*") if p.is_file()]) == 1
    assert list((root / "incoming").iterdir()) == []


def test_trashed_bytes_still_count_until_purged(tmp_path: Path) -> None:
    root = tmp_path / "space"
    storage = SpaceStorage(quota_bytes=10, trash_days=30)
    item, _ = space_catalog.add_item(root, _file(tmp_path, "a", b"12345678"), "a.jpg", 1, storage)
    space_catalog.remove_item(root, item.id, 1, "manager")
    with pytest.raises(SpaceQuotaExceeded):
        space_catalog.add_item(root, _file(tmp_path, "b", b"abcdefgh"), "b.jpg", 1, storage)
    space_catalog.purge_expired(root, 30, now=datetime.now(timezone.utc) + timedelta(days=31))
    space_catalog.add_item(root, _file(tmp_path, "b", b"abcdefgh"), "b.jpg", 1, storage)


# -- trash -----------------------------------------------------------------

def test_trash_is_scoped_by_role_and_restorable(tmp_path: Path) -> None:
    root = tmp_path / "space"
    mine, _ = space_catalog.add_item(root, _file(tmp_path, "m", b"m"), "m.jpg", added_by=1)
    theirs, _ = space_catalog.add_item(root, _file(tmp_path, "t", b"t"), "t.jpg", added_by=2)
    space_catalog.remove_item(root, mine.id, 3, "manager")
    space_catalog.remove_item(root, theirs.id, 3, "manager")

    assert {i.id for i in space_catalog.list_trash(root, 3, "manager", 10)} == {mine.id, theirs.id}
    assert [i.id for i in space_catalog.list_trash(root, 1, "contributor", 10)] == [mine.id]
    assert space_catalog.list_trash(root, 1, "viewer", 10) == []

    with pytest.raises(SpaceItemPermissionDenied):
        space_catalog.restore_item(root, theirs.id, 1, "contributor")
    restored = space_catalog.restore_item(root, mine.id, 1, "contributor")
    assert restored.removed_at is None
    assert space_catalog.get_item(root, mine.id).id == mine.id
    with pytest.raises(SpaceItemNotFound):
        space_catalog.restore_item(root, mine.id, 1, "contributor")


def test_restore_conflicts_with_the_same_content_added_again(tmp_path: Path) -> None:
    root = tmp_path / "space"
    old, _ = space_catalog.add_item(root, _file(tmp_path, "o", b"same"), "o.jpg", 1)
    space_catalog.remove_item(root, old.id, 1, "manager")
    space_catalog.add_item(root, _file(tmp_path, "n", b"same"), "n.jpg", 1)
    with pytest.raises(SpaceItemConflict):
        space_catalog.restore_item(root, old.id, 1, "manager")


def test_purge_waits_for_retention_and_keeps_shared_bytes(tmp_path: Path) -> None:
    root = tmp_path / "space"
    now = datetime.now(timezone.utc)
    gone, _ = space_catalog.add_item(root, _file(tmp_path, "g", b"gone"), "g.jpg", 1)
    old, _ = space_catalog.add_item(root, _file(tmp_path, "s", b"shared"), "s.jpg", 1)
    _, gone_path = space_catalog.item_original(root, gone.id)
    _, shared_path = space_catalog.item_original(root, old.id)
    (root / "thumbnails").mkdir()
    (root / "thumbnails" / f"{gone.sha256}.jpg").write_bytes(b"thumb")
    space_catalog.remove_item(root, gone.id, 1, "manager")
    space_catalog.remove_item(root, old.id, 1, "manager")
    again, _ = space_catalog.add_item(root, _file(tmp_path, "s2", b"shared"), "s.jpg", 1)

    assert space_catalog.purge_expired(root, 30, now=now + timedelta(days=29)) == 0
    assert gone_path.exists()
    assert space_catalog.purge_expired(root, 30, now=now + timedelta(days=31)) == 2

    assert not gone_path.exists()
    assert not (root / "thumbnails" / f"{gone.sha256}.jpg").exists()
    assert shared_path.exists()  # still used by the re-added item
    assert space_catalog.item_original(root, again.id)[1] == shared_path
    assert space_catalog.list_trash(root, 1, "manager", 10) == []
    assert space_catalog.usage_bytes(root) == len(b"shared")


# -- schema ----------------------------------------------------------------

def test_v1_catalogue_is_migrated_in_place(tmp_path: Path) -> None:
    root = tmp_path / "space"
    root.mkdir()
    with sqlite3.connect(root / "space.db") as connection:
        space_catalog._create_v1(connection)
        connection.executemany(
            """INSERT INTO items (sha256, storage_name, original_name, mime_type,
                                  size_bytes, added_by, added_at, removed_at)
               VALUES (?, ?, ?, 'image/jpeg', ?, 1, '2026-01-01T00:00:00+00:00', ?)""",
            [
                ("a" * 64, "aa/a.jpg", "a.jpg", 5, None),
                ("b" * 64, "bb/b.jpg", "b.jpg", 7, "2026-01-02T00:00:00+00:00"),
            ],
        )
        connection.execute("PRAGMA user_version = 1")

    items = space_catalog.list_items(root, limit=10)
    assert [(i.original_name, i.storage_method) for i in items] == [("a.jpg", "copy")]
    assert space_catalog.usage_bytes(root) == 12
    with sqlite3.connect(root / "space.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
