"""Safe, structured operational logs for Iris.

The module deliberately exposes a small interface: configure logging once at
startup, then emit ordinary ``logging`` records. Request middleware supplies
only non-sensitive metadata (never bodies, cookies, query strings or passwords).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_STANDARD_FIELDS = set(logging.makeLogRecord({}).__dict__)
_EVENT_FIELDS = ("event", "request_id", "method", "path", "status_code", "duration_ms", "user_id")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, suitable for Docker and log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _EVENT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Configure Iris logs once.

    Environment: ``IRIS_LOG_LEVEL`` (INFO), ``IRIS_LOG_FORMAT`` (text/json),
    and optionally ``IRIS_LOG_FILE``. Docker should normally collect stdout;
    the optional rotating file is for a host-managed installation.
    """
    logger = logging.getLogger("iris")
    if getattr(logger, "_iris_configured", False):
        return
    level = getattr(logging, os.environ.get("IRIS_LOG_LEVEL", "INFO").upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = False
    formatter: logging.Formatter
    if os.environ.get("IRIS_LOG_FORMAT", "text").strip().lower() == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    log_file = os.environ.get("IRIS_LOG_FILE", "").strip()
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        maximum = max(1, int(os.environ.get("IRIS_LOG_MAX_BYTES", str(20 * 1024 * 1024))))
        backups = max(1, int(os.environ.get("IRIS_LOG_BACKUPS", "5")))
        file_handler = RotatingFileHandler(path, maxBytes=maximum, backupCount=backups, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger._iris_configured = True  # type: ignore[attr-defined]


def request_path(path: str) -> str:
    """Return a path safe to log; callers must never append query strings."""
    return path.split("?", 1)[0]
