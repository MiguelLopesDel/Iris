"""The daily backup schedule, its history and its retention, with a fake clock."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from core import instance_settings
from core.backup_scheduler import BackupService
from core.indexer_db import init_db
from core.instance_backup import BackupError
from core.users_db import create_user

SP = ZoneInfo("America/Sao_Paulo")  # UTC-3, no daylight saving since 2019


class Clock:
    def __init__(self, local: datetime) -> None:
        self.now = local.astimezone(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for setting in instance_settings.SETTINGS.values():
        monkeypatch.delenv(setting.env, raising=False)
    data = tmp_path / "data"
    user = create_user(data / "users.db", data, username="ana", password_hash="x")
    (data / "secret_key").write_text("synthetic session secret")
    Image.new("RGB", (8, 8), (1, 2, 3)).save(user.media_root / "a.jpg")
    instance_settings.save(
        data / "users.db",
        {"backup_time": "03:00", "backup_timezone": "America/Sao_Paulo"},
        actor_id=1,
    )
    clock = Clock(datetime(2026, 9, 23, 2, 59, tzinfo=SP))
    service = BackupService(data / "users.db", {"data": data}, tmp_path / "backups", clock=clock)
    return service, clock, data


def test_runs_once_a_day_at_the_local_time(setup) -> None:
    service, clock, _ = setup
    assert not service.due()
    assert service.next_run() == datetime(2026, 9, 23, 3, 0, tzinfo=SP)
    clock.advance(minutes=2)
    assert service.due()
    assert service.run("schedule").status == "ok"
    assert not service.due()
    assert service.next_run() == datetime(2026, 9, 24, 3, 0, tzinfo=SP)
    clock.advance(hours=24)
    assert service.due()


def test_a_server_that_was_off_catches_up_the_same_day(setup) -> None:
    service, clock, _ = setup
    clock.advance(hours=12)  # 14:59, nothing ran at 03:00
    assert service.due()


def test_manual_backups_do_not_replace_the_scheduled_one(setup) -> None:
    service, clock, _ = setup
    clock.advance(hours=1)
    service.run("manual")
    assert service.due()


def test_failures_retry_hourly_up_to_three_times(setup, tmp_path: Path) -> None:
    service, clock, data = setup
    service.dest = data / "inside"  # refused: a backup cannot live in its own root
    clock.advance(minutes=2)
    for attempt in range(3):
        assert service.due(), attempt
        run = service.run("schedule")
        assert run.status == "failed" and "dentro da raiz" in run.message
        clock.advance(minutes=30)
        assert not service.due()
        clock.advance(minutes=30)
    assert not service.due()  # three strikes: wait for tomorrow


def test_missing_original_marks_run_failed_and_keeps_previous_snapshot(setup) -> None:
    service, clock, data = setup
    first = service.run("manual")
    assert first.status == "ok"
    previous = {path.name for path in service.dest.glob("iris-backup-*")}
    database = data / "users" / "1" / "iris.db"
    init_db(database).close()
    missing = data / "users" / "1" / "media" / "missing.jpg"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
            (missing.name, str(missing), b"\0" * 16),
        )

    clock.advance(minutes=1)
    failed = service.run("manual")
    assert failed.status == "failed" and "missing.jpg" in failed.message
    assert failed.pruned == 0
    assert {path.name for path in service.dest.glob("iris-backup-*")} == previous


def test_schedule_can_be_turned_off(setup) -> None:
    service, clock, data = setup
    instance_settings.save(data / "users.db", {"backup_schedule": "off"}, actor_id=1)
    clock.advance(hours=1)
    assert not service.due() and service.next_run() is None


def test_each_run_applies_the_retention_policy_only_to_policy_backups(setup) -> None:
    service, clock, data = setup
    instance_settings.save(
        data / "users.db",
        {"backup_keep_daily": 1, "backup_keep_weekly": 0, "backup_keep_monthly": 0},
        actor_id=1,
    )
    legacy = service.dest / "iris-backup-20200101T000000Z"
    legacy.mkdir(parents=True)
    (legacy / "manifest.json").write_text(json.dumps({
        "format": 1, "created_at": "2020-01-01T00:00:00+00:00", "roots": {}, "files": [],
    }))
    clock.advance(minutes=2)
    first = service.run("schedule")
    pinned = service.run("manual", pinned=True)
    clock.advance(days=1)
    second = service.run("schedule")

    assert second.pruned == 1
    names = sorted(p.name for p in service.dest.glob("iris-backup-*"))
    assert first.snapshot not in names
    assert {legacy.name, pinned.snapshot, second.snapshot} == set(names)


def test_one_run_at_a_time(setup) -> None:
    service, _, _ = setup
    assert service._running.acquire(blocking=False)
    try:
        with pytest.raises(BackupError):
            service.run("manual")
    finally:
        service._running.release()


def test_only_the_server_marks_an_interrupted_run_as_failed(setup) -> None:
    service, clock, data = setup
    service.runs()  # creates the history table
    with sqlite3.connect(data / "users.db") as connection:
        connection.execute(
            "INSERT INTO backup_runs (trigger, started_at, status) VALUES ('schedule', ?, 'running')",
            (clock().isoformat(),),
        )
    # A second process (the command line) must not declare it dead...
    other = BackupService(data / "users.db", {"data": data}, service.dest, clock=clock)
    assert other.runs()[0].status == "running"
    # ...but a server starting up knows the previous one died mid-run.
    other.start(startup_delay=3600)
    other.stop()
    assert other.runs()[0].status == "failed"


def test_administrator_runs_and_lists_backups_from_the_interface(tmp_path: Path) -> None:
    script = r'''
import time
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.instance_backup import iris_version
from core.users_db import create_user

data = Path("data")
for name, admin in (("root", True), ("ana", False)):
    create_user(data / "users.db", data, username=name,
                password_hash=hash_password("synthetic password 1"), is_admin=admin)

import server

with TestClient(server.app) as root, TestClient(server.app) as ana:
    for name, client in (("root", root), ("ana", ana)):
        client.post("/api/auth/login", data={"username": name, "password": "synthetic password 1"})
    assert ana.get("/api/admin/backups").status_code == 403
    assert ana.post("/api/admin/backups", json={}).status_code == 403

    state = root.get("/api/admin/backups").json()
    assert state["enabled"] is True and state["snapshots"] == [] and state["runs"] == []
    assert isinstance(state["catching_up"], bool)
    assert state["same_disk_as_data"] is True

    assert root.post("/api/admin/backups", json={"pin": True}).status_code == 202
    for _ in range(100):
        state = root.get("/api/admin/backups").json()
        if not state["running"] and state["runs"]:
            break
        time.sleep(0.1)
    run = state["runs"][0]
    assert (run["status"], run["trigger"], run["pinned"]) == ("ok", "manual", True), run
    [snapshot] = state["snapshots"]
    assert snapshot["iris_version"] == iris_version() and snapshot["iris_commit"] == "cafe123"
    assert snapshot["retention_label"] == "guardado para sempre"

    saved = root.put("/api/admin/settings", json={"backup_schedule": "off"})
    assert saved.status_code == 200
    assert root.get("/api/admin/backups").json()["next_run"] is None
    assert root.put("/api/admin/settings", json={"backup_time": "25:00"}).status_code == 422
    assert root.put("/api/admin/settings", json={"backup_timezone": "Mars/Base"}).status_code == 422
'''
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_LOAD_MODEL="0",
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
        IRIS_COMMIT="cafe123",
        IRIS_BACKUP_DEST=str(tmp_path / "backups"),
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_housekeeping_runs_at_most_hourly_and_never_stops_backups(tmp_path: Path) -> None:
    data = tmp_path / "data"
    create_user(data / "users.db", data, username="ana", password_hash="x")
    calls: list[datetime] = []
    clock = Clock(datetime(2026, 9, 23, 10, 0, tzinfo=SP))

    def maintenance() -> None:
        calls.append(clock())
        raise RuntimeError("a failing purge")  # must be contained

    service = BackupService(
        data / "users.db", {"data": data}, tmp_path / "backups",
        clock=clock, maintenance=maintenance,
    )
    service.maintain()
    clock.advance(minutes=59)
    service.maintain()
    clock.advance(minutes=2)
    service.maintain()
    assert len(calls) == 2
