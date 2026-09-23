"""Whole-instance backup: consistent, incremental, verifiable, restorable."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from core import instance_backup
from core.auth import hash_password
from core.indexer_db import init_db
from core.instance_backup import BackupError
from core.shared_spaces import add_member, create_space
from core.space_catalog import add_item, space_root
from core.users_db import create_user

PASSWORD = "synthetic backup password"
T0 = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


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


def test_snapshot_holds_the_recovery_unit_and_nothing_rebuildable(
    instance: dict[str, Path], tmp_path: Path
) -> None:
    summary = instance_backup.create(_roots(instance), tmp_path / "backups", now=T0)
    assert summary.snapshot.name == "iris-backup-20260923T120000Z"
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
    summary = instance_backup.create(
        {"data": instance["data"]}, tmp_path / "backups", now=T0
    )
    assert any("legacy.jpg" in warning for warning in summary.warnings)


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
    assert manifest["iris_version"] == "0.3.0"
    assert manifest["iris_commit"] == "feed123"
    assert manifest["retention"] == "policy"
    pinned = instance_backup.create(
        _roots(instance), tmp_path / "b", now=T0 + timedelta(hours=1), retention="pinned"
    ).snapshot
    assert _manifest(pinned)["retention"] == "pinned"
    [first, second] = instance_backup.snapshots(tmp_path / "b")
    assert (first.iris_version, first.iris_commit, first.prunable) == ("0.3.0", "feed123", True)
    assert second.prunable is False


def test_policy_keeps_days_weeks_and_months_and_nothing_else(tmp_path: Path) -> None:
    dest = tmp_path / "b"
    start = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
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
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
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
