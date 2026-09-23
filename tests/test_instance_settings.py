"""Instance settings: interface > .env > default, and what a bad choice does."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core import fs_clone, instance_settings
from core.instance_settings import SettingError


@pytest.fixture
def users_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for setting in instance_settings.SETTINGS.values():
        monkeypatch.delenv(setting.env, raising=False)
    return tmp_path / "users.db"


def test_precedence_is_interface_then_env_then_default(
    users_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = instance_settings.resolve_all(users_db)["space_trash_days"]
    assert (resolved.value, resolved.source) == (30, "default")

    monkeypatch.setenv("IRIS_SPACE_TRASH_DAYS", "14")
    resolved = instance_settings.resolve_all(users_db)["space_trash_days"]
    assert (resolved.value, resolved.source, resolved.env_value) == (14, "env", 14)

    instance_settings.save(users_db, {"space_trash_days": 60}, actor_id=1)
    resolved = instance_settings.resolve_all(users_db)["space_trash_days"]
    assert (resolved.value, resolved.source) == (60, "interface")

    instance_settings.reset(users_db, "space_trash_days")
    assert instance_settings.resolve_all(users_db)["space_trash_days"].source == "env"


def test_values_are_validated(users_db: Path) -> None:
    for key, raw in (
        ("space_storage", "dedupe-magic"),
        ("space_quota_bytes", "0"),
        ("space_quota_bytes", "lots"),
        ("space_trash_days", "3651"),
        ("unknown", "1"),
    ):
        with pytest.raises(SettingError):
            instance_settings.parse(key, raw)
    assert instance_settings.parse("space_storage", " Reflink ") == "reflink"


def test_a_corrupt_stored_value_falls_back_instead_of_blocking(users_db: Path) -> None:
    instance_settings.resolve_all(users_db)  # creates the table
    with sqlite3.connect(users_db) as connection:
        connection.execute(
            "INSERT INTO instance_settings VALUES ('space_trash_days', 'x', '', NULL)"
        )
    assert instance_settings.resolve_all(users_db)["space_trash_days"].source == "default"


def _no_reflink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_clone, "probe", lambda directory: (False, True))


def test_env_typo_or_impossible_env_stops_startup(
    users_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IRIS_SPACE_STORAGE", "dedupe-magic")
    with pytest.raises(fs_clone.StorageConfigError):
        instance_settings.space_storage_policy(users_db, tmp_path / "spaces")

    _no_reflink(monkeypatch)
    monkeypatch.setenv("IRIS_SPACE_STORAGE", "reflink")
    with pytest.raises(fs_clone.StorageConfigError):
        instance_settings.space_storage_policy(users_db, tmp_path / "spaces")


def test_interface_choice_overrides_an_impossible_env(
    users_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_reflink(monkeypatch)
    monkeypatch.setenv("IRIS_SPACE_STORAGE", "reflink")
    instance_settings.save(users_db, {"space_storage": "copy"}, actor_id=1)
    policy = instance_settings.space_storage_policy(users_db, tmp_path / "spaces")
    assert policy.storage.strategy == "copy" and policy.warning is None


def test_saved_choice_that_stopped_working_degrades_with_a_warning(
    users_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance_settings.save(users_db, {"space_storage": "reflink"}, actor_id=1)
    _no_reflink(monkeypatch)  # e.g. data/ moved to ext4
    policy = instance_settings.space_storage_policy(users_db, tmp_path / "spaces")
    assert policy.storage.strategy == "copy"
    assert policy.warning is not None and "reflink" in policy.warning


def test_policy_carries_quota_and_trash_days(users_db: Path, tmp_path: Path) -> None:
    instance_settings.save(
        users_db, {"space_quota_bytes": 5 * 1024**3, "space_trash_days": 7}, actor_id=1
    )
    policy = instance_settings.space_storage_policy(users_db, tmp_path / "spaces")
    assert policy.storage.quota_bytes == 5 * 1024**3
    assert policy.storage.trash_days == 7
