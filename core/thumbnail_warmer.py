"""Builds missing thumbnails in the background, newest photos first.

Thumbnails are a cache: backups leave them out and the server builds each
one the first time something asks for it. After a restore, or on a library
imported without them, that means every gallery page waits for the server
to decode originals. This worker builds the missing ones ahead of time, at
low priority and with a pause between images, so requests still come first.
"""
from __future__ import annotations

import logging
import math
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("iris.thumbnails")


@dataclass(frozen=True)
class Library:
    """One account's catalogue as the warmer sees it."""

    label: str
    thumb_dir: Path
    records: Callable[[], Sequence[Any]]  # objects with resolved_path and file_mtime


def missing_thumbnails(
    records: Iterable[Any], thumb_dir: Path, key: Callable[[str], str]
) -> list[tuple[str, Path]]:
    """(source, thumbnail) pairs still to build, newest first; each thumbnail once."""
    def newest_first(record: Any) -> float:
        mtime = getattr(record, "file_mtime", None)
        return -mtime if mtime is not None and not math.isnan(mtime) else math.inf

    pending: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for record in sorted(records, key=newest_first):
        source = getattr(record, "resolved_path", None) or ""
        name = key(source) if source else ""
        if not name:
            continue  # no file to read: the gallery shows it as missing anyway
        target = thumb_dir / f"{name}.jpg"
        if target in seen or target.exists():
            continue
        seen.add(target)
        pending.append((source, target))
    return pending


class ThumbnailWarmer:
    def __init__(
        self,
        libraries: Callable[[], Iterable[Library]],
        key: Callable[[str], str],
        generate: Callable[[str, Path], None],
        *,
        pause: float = 0.05,
    ):
        self._libraries = libraries
        self._key = key
        self._generate = generate
        self._pause = pause
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> int:
        """Build every missing thumbnail once; returns how many were built."""
        built = 0
        for library in self._libraries():
            if self._stop.is_set():
                break
            pending = missing_thumbnails(library.records(), library.thumb_dir, self._key)
            if not pending:
                continue
            library.thumb_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            logger.info(
                "thumbnail_warmup_started",
                extra={"event": "thumbnail_warmup_started", "item_count": len(pending)},
            )
            done = 0
            for source, target in pending:
                if self._stop.wait(self._pause):
                    break
                if target.exists():  # a request built it meanwhile
                    continue
                try:
                    self._generate(source, target)
                except Exception as exc:
                    # One unreadable original must not stop the others.
                    logger.warning("thumbnail warm-up failed for one item: %s", type(exc).__name__)
                    continue
                if target.exists():
                    done += 1
            built += done
            logger.info(
                "thumbnail_warmup_finished",
                extra={"event": "thumbnail_warmup_finished", "item_count": done},
            )
        return built

    def start(self, startup_delay: float = 60.0) -> None:
        def run() -> None:
            try:
                # Below the request threads: a gallery page outranks the warm-up.
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
            except (AttributeError, OSError):
                pass
            if self._stop.wait(startup_delay):
                return
            try:
                self.run_once()
            except Exception:
                logger.exception("thumbnail warm-up stopped")

        self._thread = threading.Thread(target=run, name="iris-thumbnail-warmup", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
