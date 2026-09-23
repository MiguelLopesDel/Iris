"""Scheduled whole-instance backups, run by the Iris server itself.

Once a day, at the administrator's chosen local time, the server writes a
snapshot (see :mod:`core.instance_backup`) and then applies the retention
policy. A server that was off at that time catches up when it next runs. A
failed attempt is retried an hour later, at most three times a day.

Settings are read from :mod:`core.instance_settings` on every check, so a
change made in the interface applies without a restart. Every run, scheduled
or manual, is recorded in ``users.db`` for the interface to show.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core import instance_backup, instance_settings
from core.instance_backup import BackupError, RetentionPolicy
from core.users_db import init_users_db, now_iso

_CHECK_SECONDS = 30
_RETRY_AFTER = timedelta(hours=1)
_MAX_DAILY_FAILURES = 3
_MAINTENANCE_EVERY = timedelta(hours=1)


@dataclass(frozen=True)
class BackupSettings:
    enabled: bool
    at: str  # "HH:MM"
    zone: ZoneInfo
    policy: RetentionPolicy


@dataclass(frozen=True)
class Run:
    id: int
    trigger: str  # "schedule" | "manual"
    pinned: bool
    started_at: str
    finished_at: str | None
    status: str  # "running" | "ok" | "failed"
    snapshot: str | None
    pruned: int
    message: str


def _connect(path: Path) -> sqlite3.Connection:
    init_users_db(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            snapshot TEXT,
            pruned INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT ''
        )
        """
    )
    return connection


def _run_from_row(row: sqlite3.Row) -> Run:
    return Run(
        id=int(row["id"]),
        trigger=str(row["trigger"]),
        pinned=bool(row["pinned"]),
        started_at=str(row["started_at"]),
        finished_at=row["finished_at"],
        status=str(row["status"]),
        snapshot=row["snapshot"],
        pruned=int(row["pruned"]),
        message=str(row["message"]),
    )


class BackupService:
    """Owns the destination, the run history and the daily schedule."""

    def __init__(
        self,
        users_db: Path,
        roots: dict[str, Path],
        dest: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        maintenance: Callable[[], None] | None = None,
    ) -> None:
        self.users_db = users_db
        self.roots = roots
        self.dest = dest
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._running = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Hourly housekeeping sharing this loop, e.g. purging expired trash.
        self._maintenance = maintenance
        self._last_maintenance: datetime | None = None

    # -- settings and history ---------------------------------------------------

    def settings(self) -> BackupSettings:
        values = {k: v.value for k, v in instance_settings.resolve_all(self.users_db).items()}
        return BackupSettings(
            enabled=values["backup_schedule"] == "daily",
            at=str(values["backup_time"]),
            zone=ZoneInfo(str(values["backup_timezone"])),
            policy=RetentionPolicy(
                daily=int(values["backup_keep_daily"]),
                weekly=int(values["backup_keep_weekly"]),
                monthly=int(values["backup_keep_monthly"]),
            ),
        )

    def runs(self, limit: int = 10) -> list[Run]:
        connection = _connect(self.users_db)
        try:
            rows = connection.execute(
                "SELECT * FROM backup_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            connection.close()
        return [_run_from_row(row) for row in rows]

    def _recover_interrupted(self) -> None:
        """A run still 'running' at startup died with the previous process."""
        connection = _connect(self.users_db)
        try:
            with connection:
                connection.execute(
                    "UPDATE backup_runs SET status = 'failed', finished_at = ?, "
                    "message = 'interrompido: o servidor parou durante o backup' "
                    "WHERE status = 'running'",
                    (now_iso(),),
                )
        finally:
            connection.close()

    # -- schedule ---------------------------------------------------------------

    def scheduled_for(self, settings: BackupSettings, now: datetime) -> datetime:
        """Today's scheduled moment, in UTC, for the configured local time."""
        local = now.astimezone(settings.zone)
        hours, minutes = (int(part) for part in settings.at.split(":"))
        moment = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        return moment.astimezone(timezone.utc)

    def next_run(self, now: datetime | None = None) -> datetime | None:
        settings = self.settings()
        if not settings.enabled:
            return None
        now = now or self._clock()
        today = self.scheduled_for(settings, now)
        return today if self._due_today(today, now) or now < today else today + timedelta(days=1)

    def _due_today(self, scheduled: datetime, now: datetime) -> bool:
        connection = _connect(self.users_db)
        try:
            rows = connection.execute(
                "SELECT status, started_at FROM backup_runs "
                "WHERE trigger = 'schedule' AND started_at >= ? ORDER BY id",
                (scheduled.isoformat(),),
            ).fetchall()
        finally:
            connection.close()
        if any(row["status"] in ("ok", "running") for row in rows):
            return False
        failures = [datetime.fromisoformat(row["started_at"]) for row in rows]
        if len(failures) >= _MAX_DAILY_FAILURES:
            return False
        return not failures or now - failures[-1] >= _RETRY_AFTER

    def due(self, now: datetime | None = None) -> bool:
        settings = self.settings()
        if not settings.enabled:
            return False
        now = now or self._clock()
        scheduled = self.scheduled_for(settings, now)
        return now >= scheduled and self._due_today(scheduled, now)

    # -- running ----------------------------------------------------------------

    def clock(self) -> datetime:
        return self._clock()

    @property
    def running(self) -> bool:
        return self._running.locked()

    def run(self, trigger: str, *, pinned: bool = False) -> Run:
        """Back up, then prune; always leaves a finished row in the history."""
        if not self._running.acquire(blocking=False):
            raise BackupError("já existe um backup em andamento")
        try:
            return self._run_locked(trigger, pinned)
        finally:
            self._running.release()

    def _run_locked(self, trigger: str, pinned: bool) -> Run:
        connection = _connect(self.users_db)
        try:
            with connection:
                run_id = int(connection.execute(
                    "INSERT INTO backup_runs (trigger, pinned, started_at, status) "
                    "VALUES (?, ?, ?, 'running')",
                    (trigger, int(pinned), self._clock().isoformat()),
                ).lastrowid)
            status, snapshot, pruned, message = "ok", None, 0, ""
            try:
                settings = self.settings()
                summary = instance_backup.create(
                    self.roots, self.dest, now=self._clock(),
                    retention=instance_backup.PINNED if pinned else instance_backup.POLICY,
                )
                snapshot = summary.snapshot.name
                message = "; ".join(summary.warnings)
                pruned = len(instance_backup.prune(self.dest, settings.policy, zone=settings.zone))
            except Exception as exc:  # recorded, shown in the interface
                status, message = "failed", str(exc)[:1000]
            with connection:
                connection.execute(
                    "UPDATE backup_runs SET status = ?, finished_at = ?, snapshot = ?, "
                    "pruned = ?, message = ? WHERE id = ?",
                    (status, self._clock().isoformat(), snapshot, pruned, message, run_id),
                )
            row = connection.execute("SELECT * FROM backup_runs WHERE id = ?", (run_id,)).fetchone()
        finally:
            connection.close()
        return _run_from_row(row)

    def run_in_background(self, trigger: str, *, pinned: bool = False) -> None:
        if self.running:
            raise BackupError("já existe um backup em andamento")
        threading.Thread(
            target=self._quietly, args=(trigger, pinned), name="iris-backup", daemon=True
        ).start()

    def _quietly(self, trigger: str, pinned: bool) -> None:
        try:
            self.run(trigger, pinned=pinned)
        except BackupError:
            pass  # another run started first; nothing to record twice

    # -- background loop ----------------------------------------------------------

    def start(self, startup_delay: float = 300.0) -> None:
        """Check the schedule every 30 s, after letting the server settle."""
        if self._thread is not None:
            return
        # Only the server owns the schedule: a command-line run in another
        # process must not mark the server's run in progress as dead.
        self._recover_interrupted()

        def loop() -> None:
            if self._stop.wait(startup_delay):
                return
            while not self._stop.is_set():
                try:
                    if self.due():
                        self.run("schedule")
                except Exception:
                    pass  # a broken setting must not kill the loop; runs record errors
                self.maintain()
                self._stop.wait(_CHECK_SECONDS)

        self._thread = threading.Thread(target=loop, name="iris-backup-schedule", daemon=True)
        self._thread.start()

    def maintain(self) -> None:
        """Run the housekeeping callable at most once per hour."""
        if self._maintenance is None:
            return
        now = self._clock()
        if self._last_maintenance and now - self._last_maintenance < _MAINTENANCE_EVERY:
            return
        self._last_maintenance = now
        try:
            self._maintenance()
        except Exception:
            pass  # housekeeping retries next hour; it must not stop backups

    def stop(self) -> None:
        self._stop.set()

    # -- destination ----------------------------------------------------------------

    def same_disk_as_data(self) -> bool:
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
            return os.stat(self.dest).st_dev == os.stat(self.roots["data"]).st_dev
        except OSError:
            return False


def from_environment() -> BackupService:
    """The service as the server builds it, for the command line."""
    data = Path(os.environ.get("IRIS_DATA_DIR", "data"))
    media = Path(os.environ.get("IRIS_MEDIA_DIR", "media"))
    return BackupService(
        data / "users.db",
        {"data": data, **({"media": media} if media.is_dir() else {})},
        Path(os.environ.get("IRIS_BACKUP_DEST", "backups")),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m core.backup_scheduler")
    commands = parser.add_subparsers(dest="command", required=True)
    now = commands.add_parser("run", help="backup agora, com a mesma retenção do agendado")
    now.add_argument("--pin", action="store_true", help="guardar este para sempre")
    args = parser.parse_args(argv)

    service = from_environment()
    if args.command == "run":
        # Recorded as manual: it never counts as the day's scheduled backup.
        run = service.run("manual", pinned=args.pin)
        print(f"{run.status}: {run.snapshot or '-'}"
              f" ({run.pruned} backup(s) antigos apagados pela política)")
        if run.message:
            print(run.message)
        return 0 if run.status == "ok" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
