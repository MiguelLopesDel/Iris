"""Know when a newer release exists, and ask the host to install it.

Two halves, kept apart on purpose:

* ``UpdateChecker`` asks GitHub for the project's tags every few hours and
  remembers the newest stable one (exactly ``vX.Y.Z``: the same rule
  ``scripts/server.sh`` follows, so a pre-release is never offered).
* The container cannot replace itself, and it is never given the Docker socket.
  An administrator's "update now" only writes ``updater/request.json`` in the
  data directory. A systemd path unit on the host (``server.sh updater install``)
  notices the file and runs ``server.sh update`` -- backup, new release, health
  check -- writing its progress to ``updater/status.json``. The request carries
  no command or version: the host always updates to what its own configuration
  selects, so a forged request can at most trigger a normal update.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib import request as urlrequest

logger = logging.getLogger("iris.updates")

DEFAULT_REPOSITORY = "MiguelLopesDel/Iris"
CHECK_EVERY_SECONDS = 6 * 3600
_STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_TIMEOUT_SECONDS = 15

UPDATER_DIR = "updater"
REQUEST_FILE = "request.json"
STATUS_FILE = "status.json"
AGENT_FILE = "agent.json"


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """``0.9.0`` or ``v0.9.0`` as a comparable tuple; anything else is None."""
    if not text:
        return None
    match = _STABLE_TAG.match(text if text.startswith("v") else f"v{text}")
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def newest_stable(tags: list[str]) -> str | None:
    """The highest ``vX.Y.Z`` tag, without the ``v``; pre-releases are ignored."""
    versions = [version for version in map(parse_version, tags) if version is not None]
    return ".".join(map(str, max(versions))) if versions else None


def fetch_tags(repository: str, *, opener: Callable[..., object] = urlrequest.urlopen) -> list[str]:
    """Tag names from the GitHub API (public repository, no token)."""
    url = f"https://api.github.com/repos/{repository}/tags?per_page=100"
    req = urlrequest.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "iris-update-check"}
    )
    with opener(req, timeout=_TIMEOUT_SECONDS) as response:  # type: ignore[attr-defined]
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("unexpected answer from the tags API")
    return [str(item.get("name", "")) for item in payload if isinstance(item, dict)]


@dataclass(frozen=True)
class UpdateInfo:
    current: str | None
    latest: str | None
    available: bool
    checked_at: str | None
    error: str | None = None
    enabled: bool = True


def checks_enabled() -> bool:
    return os.environ.get("IRIS_UPDATE_CHECK", "1").strip().lower() not in {"0", "false", "no", "off"}


class UpdateChecker:
    """Remembers the newest release; refreshed by a background thread."""

    def __init__(
        self,
        current: str | None,
        *,
        repository: str | None = None,
        fetch: Callable[[str], list[str]] = fetch_tags,
        clock: Callable[[], float] = time.time,
        enabled: bool | None = None,
    ) -> None:
        self.current = current
        self.repository = repository or os.environ.get("IRIS_UPDATE_REPOSITORY", DEFAULT_REPOSITORY)
        self._fetch = fetch
        self._clock = clock
        self.enabled = checks_enabled() if enabled is None else enabled
        self._lock = threading.Lock()
        self._latest: str | None = None
        self._checked_at: float | None = None
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> UpdateInfo:
        """Ask GitHub now; a failure keeps the previous answer and records why."""
        if not self.enabled:
            return self.info()
        try:
            latest = newest_stable(self._fetch(self.repository))
            error = None
        except Exception as exc:  # network down, rate limit, bad JSON: try again later
            logger.info("update_check_failed error_type=%s", type(exc).__name__)
            latest, error = None, "Não foi possível consultar as versões publicadas."
        with self._lock:
            if latest is not None:
                self._latest = latest
            self._error = error
            self._checked_at = self._clock()
        return self.info()

    def info(self) -> UpdateInfo:
        with self._lock:
            latest, checked, error = self._latest, self._checked_at, self._error
        current, newest = parse_version(self.current), parse_version(latest)
        return UpdateInfo(
            current=self.current,
            latest=latest,
            available=bool(current and newest and newest > current),
            checked_at=datetime.fromtimestamp(checked, UTC).isoformat() if checked is not None else None,
            error=error,
            enabled=self.enabled,
        )

    def start(self, startup_delay: float = 60.0, every: float = CHECK_EVERY_SECONDS) -> None:
        if self._thread is not None or not self.enabled:
            return

        def loop() -> None:
            if self._stop.wait(startup_delay):
                return
            while not self._stop.is_set():
                self.check()
                self._stop.wait(every)

        self._thread = threading.Thread(target=loop, name="iris-update-check", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# -- the request channel to the host ---------------------------------------------


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class UpdaterState:
    installed: bool
    pending: bool
    status: dict | None


def updater_state(data_dir: Path) -> UpdaterState:
    folder = Path(data_dir) / UPDATER_DIR
    return UpdaterState(
        installed=(folder / AGENT_FILE).is_file(),
        pending=(folder / REQUEST_FILE).exists(),
        status=_read_json(folder / STATUS_FILE),
    )


class UpdateRequestError(RuntimeError):
    pass


def request_update(data_dir: Path, *, requested_by: str, clock: Callable[[], float] = time.time) -> dict:
    """Leave a request for the host agent; refuse when none is installed or one is under way."""
    state = updater_state(data_dir)
    if not state.installed:
        raise UpdateRequestError(
            "A atualização pelo navegador não está ativada neste servidor. "
            "Rode ./scripts/server.sh updater install no servidor."
        )
    if state.pending or (state.status or {}).get("state") == "running":
        raise UpdateRequestError("Já há uma atualização em andamento.")
    folder = Path(data_dir) / UPDATER_DIR
    body = {
        "requested_by": requested_by,
        "requested_at": datetime.fromtimestamp(clock(), UTC).isoformat(),
    }
    # Written beside and renamed, so the path unit never sees half a file.
    temporary = folder / f".{REQUEST_FILE}.tmp"
    temporary.write_text(json.dumps(body), encoding="utf-8")
    os.replace(temporary, folder / REQUEST_FILE)
    return body


def info_dict(checker: UpdateChecker, data_dir: Path) -> dict:
    state = updater_state(data_dir)
    return {**asdict(checker.info()), "updater": asdict(state)}
