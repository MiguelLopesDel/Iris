"""Instance-wide settings an administrator may change from the web interface.

Each setting has three possible sources, in precedence order: a value saved
from the interface (stored in ``users.db``), the installer's ``.env``, and the
built-in default. Removing the saved value falls back to the next source, so
the interface never has to rewrite ``.env``.

Settings here govern the instance, never the content of a library or a space:
administering them grants no access to anyone's media.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core import fs_clone
from core.fs_clone import DEFAULT_STRATEGY, STRATEGIES, StorageReport
from core.space_catalog import DEFAULT_QUOTA_BYTES, DEFAULT_TRASH_DAYS, SpaceStorage
from core.users_db import init_users_db, now_iso

_MAX_TRASH_DAYS = 3650
_MAX_KEEP = 1000


class SettingError(ValueError):
    """A value that this setting does not accept."""


def _strategy(raw: str) -> str:
    value = raw.strip().lower()
    if value not in STRATEGIES:
        raise SettingError(f"use um de: {', '.join(STRATEGIES)}")
    return value


def _choice(*allowed: str) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        value = str(raw).strip().lower()
        if value not in allowed:
            raise SettingError(f"use um de: {', '.join(allowed)}")
        return value

    return parse


def _clock_time(raw: str) -> str:
    value = str(raw).strip()
    try:
        hours, minutes = (int(part) for part in value.split(":"))
    except ValueError as exc:
        raise SettingError("use o formato HH:MM, por exemplo 03:00") from exc
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise SettingError("use um horário entre 00:00 e 23:59")
    return f"{hours:02d}:{minutes:02d}"


def _timezone(raw: str) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    value = str(raw).strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SettingError(f"fuso horário desconhecido: {value} (ex.: America/Sao_Paulo)") from exc
    return value


def _count(maximum: int) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            value = int(str(raw).strip())
        except ValueError as exc:
            raise SettingError("precisa ser um número inteiro") from exc
        if not 0 <= value <= maximum:
            raise SettingError(f"precisa ser entre 0 e {maximum}")
        return value

    return parse


def _positive_int(maximum: int | None = None) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            value = int(str(raw).strip())
        except ValueError as exc:
            raise SettingError("precisa ser um número inteiro") from exc
        if value < 1 or (maximum is not None and value > maximum):
            raise SettingError(
                "precisa ser no mínimo 1" + (f" e no máximo {maximum}" if maximum else "")
            )
        return value

    return parse


@dataclass(frozen=True)
class Setting:
    key: str
    env: str
    default: object
    parse: Callable[[str], object]


SETTINGS: dict[str, Setting] = {
    setting.key: setting
    for setting in (
        Setting("space_storage", "IRIS_SPACE_STORAGE", DEFAULT_STRATEGY, _strategy),
        Setting("space_quota_bytes", "IRIS_SPACE_QUOTA_BYTES", DEFAULT_QUOTA_BYTES,
                _positive_int()),
        Setting("space_trash_days", "IRIS_SPACE_TRASH_DAYS", DEFAULT_TRASH_DAYS,
                _positive_int(_MAX_TRASH_DAYS)),
        Setting("backup_schedule", "IRIS_BACKUP_SCHEDULE", "daily", _choice("daily", "off")),
        Setting("backup_time", "IRIS_BACKUP_TIME", "03:00", _clock_time),
        Setting("backup_timezone", "IRIS_BACKUP_TIMEZONE", "UTC", _timezone),
        Setting("backup_keep_daily", "IRIS_BACKUP_KEEP_DAILY", 7, _count(_MAX_KEEP)),
        Setting("backup_keep_weekly", "IRIS_BACKUP_KEEP_WEEKLY", 4, _count(_MAX_KEEP)),
        Setting("backup_keep_monthly", "IRIS_BACKUP_KEEP_MONTHLY", 6, _count(_MAX_KEEP)),
    )
}


@dataclass(frozen=True)
class Resolved:
    key: str
    value: object
    source: str  # "interface" | "env" | "default"
    env_value: object | None
    default: object


def _connect(path: Path) -> sqlite3.Connection:
    init_users_db(path)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS instance_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    return connection


def _saved(path: Path) -> dict[str, str]:
    connection = _connect(path)
    try:
        return {str(k): str(v) for k, v in connection.execute(
            "SELECT key, value FROM instance_settings"
        )}
    finally:
        connection.close()


def _env_value(setting: Setting) -> object | None:
    raw = os.environ.get(setting.env, "").strip()
    if not raw:
        return None
    try:
        return setting.parse(raw)
    except SettingError:
        # An invalid .env value is reported where it is consumed (startup);
        # here it simply does not count as a source.
        return None


def resolve_all(path: Path) -> dict[str, Resolved]:
    saved = _saved(path)
    resolved: dict[str, Resolved] = {}
    for key, setting in SETTINGS.items():
        env_value = _env_value(setting)
        value, source = setting.default, "default"
        if env_value is not None:
            value, source = env_value, "env"
        if key in saved:
            try:
                value, source = setting.parse(saved[key]), "interface"
            except SettingError:
                pass  # a stale stored value never blocks startup
        resolved[key] = Resolved(key, value, source, env_value, setting.default)
    return resolved


def parse(key: str, raw: object) -> object:
    setting = SETTINGS.get(key)
    if setting is None:
        raise SettingError(f"configuração desconhecida: {key}")
    return setting.parse(str(raw))


def save(path: Path, values: dict[str, object], actor_id: int) -> None:
    """Store already-validated values, all or nothing."""
    connection = _connect(path)
    try:
        with connection:
            connection.executemany(
                """
                INSERT INTO instance_settings (key, value, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                    updated_at = excluded.updated_at, updated_by = excluded.updated_by
                """,
                [(key, str(value), now_iso(), actor_id) for key, value in values.items()],
            )
    finally:
        connection.close()


def reset(path: Path, key: str) -> None:
    if key not in SETTINGS:
        raise SettingError(f"configuração desconhecida: {key}")
    connection = _connect(path)
    try:
        with connection:
            connection.execute("DELETE FROM instance_settings WHERE key = ?", (key,))
    finally:
        connection.close()


@dataclass(frozen=True)
class SpaceStoragePolicy:
    report: StorageReport
    storage: SpaceStorage
    warning: str | None  # a saved choice that could not be honoured


def space_storage_policy(path: Path, spaces_dir: Path) -> SpaceStoragePolicy:
    """Effective shared-space storage, honouring interface > .env > default.

    A bad ``.env`` is the installer's mistake and stops the server
    (:class:`StorageConfigError`). A saved interface choice that stopped
    working -- data moved to a disk without reflinks, say -- must not lock the
    administrator out of the page that fixes it, so it degrades to ``auto``
    and the reason is shown there.
    """
    raw_env = os.environ.get("IRIS_SPACE_STORAGE", "").strip()
    if raw_env:
        try:
            _strategy(raw_env)  # a typo in .env is always fatal
        except SettingError as exc:
            raise fs_clone.StorageConfigError(f"IRIS_SPACE_STORAGE={raw_env!r}: {exc}") from exc
    settings = resolve_all(path)
    chosen = settings["space_storage"]
    warning = None
    try:
        report = fs_clone.resolve(str(chosen.value), spaces_dir)
    except fs_clone.StorageConfigError as exc:
        if chosen.source != "interface":
            raise
        warning = f"{chosen.value}: {exc}"
        report = fs_clone.resolve("auto", spaces_dir)
    storage = SpaceStorage(
        strategy=report.strategy,
        quota_bytes=int(settings["space_quota_bytes"].value),
        trash_days=int(settings["space_trash_days"].value),
    )
    return SpaceStoragePolicy(report, storage, warning)
