"""A lock file that says whether a server is using the data folder.

The server holds a shared lock on ``data/.iris-server.lock`` for as long as it
runs. Maintenance that must not race a running server (attaching a library
to an account, say) takes the exclusive lock: it fails while any server runs,
and a server started meanwhile waits until the maintenance ends. The kernel
drops the lock when its process dies, so a crash never leaves it stale, and it
works across containers that bind-mount the same data folder.
"""
from __future__ import annotations

import fcntl
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

logger = logging.getLogger("iris")

LOCK_NAME = ".iris-server.lock"


class ServerRunning(RuntimeError):
    """A server is using the data folder."""


def _open(data_dir: Path) -> IO[bytes]:
    data_dir.mkdir(parents=True, exist_ok=True)
    return open(data_dir / LOCK_NAME, "a+b")  # held open for the lock's lifetime


def hold_for_server(data_dir: Path) -> IO[bytes]:
    """Take the server's shared lock, waiting out maintenance in progress."""
    handle = _open(data_dir)
    try:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.warning("server_waiting_for_maintenance lock=%s", data_dir / LOCK_NAME)
        print("[iris] Manutenção em andamento na pasta de dados; aguardando terminar…")
        fcntl.flock(handle, fcntl.LOCK_SH)
    return handle


def release(handle: IO[bytes]) -> None:
    fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


def server_running(data_dir: Path) -> bool:
    """Whether a server holds the data folder right now (a hint; maintenance re-checks)."""
    try:
        with exclusive_maintenance(data_dir):
            return False
    except ServerRunning:
        return True


@contextmanager
def exclusive_maintenance(data_dir: Path) -> Iterator[None]:
    """Hold the data folder for maintenance; raise :class:`ServerRunning` if a server has it."""
    handle = _open(data_dir)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ServerRunning(
                "O servidor do Iris está rodando com esta pasta de dados: pare-o "
                "(docker compose stop iris) e rode de novo."
            ) from exc
        yield
    finally:
        handle.close()
