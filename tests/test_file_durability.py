"""Concurrent requests share fsync barriers; nothing is acknowledged before its sync."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

import core.file_durability as durability_module
from core.file_durability import FileDurabilityBusy, FileDurabilityService
from core.ingest_policy import IngestPolicy


def _files(root: Path, count: int, prefix: str = "f") -> list[Path]:
    paths = []
    for index in range(count):
        path = root / f"{prefix}{index}.bin"
        path.write_bytes(b"x" * 100)
        paths.append(path)
    return paths


def test_requests_arriving_together_share_one_group(tmp_path: Path, monkeypatch):
    synced_files, synced_dirs = [], []
    release, syncing = threading.Event(), threading.Event()
    real_fsync_file = durability_module._fsync_file

    def slow_first(path):
        syncing.set()
        release.wait(timeout=5)  # the first group is still syncing
        synced_files.append(path)
        real_fsync_file(path)

    monkeypatch.setattr(durability_module, "_fsync_file", slow_first)
    monkeypatch.setattr(durability_module, "fsync_directory", synced_dirs.append)
    service = FileDurabilityService(IngestPolicy(durability_window_s=0))
    try:
        first = service.flush(_files(tmp_path, 1, "a"), [tmp_path])
        assert syncing.wait(timeout=5)
        # Queued while the first group is syncing: they form the next group.
        later = [service.flush(_files(tmp_path, 2, f"b{n}"), [tmp_path]) for n in range(3)]
        release.set()
        first.result(timeout=5)
        for future in later:
            future.result(timeout=5)
    finally:
        service.stop()

    assert len(synced_files) == 7
    # One directory sync per group: the first alone, then the three together.
    assert synced_dirs == [tmp_path, tmp_path]


def test_a_failed_sync_fails_every_request_of_its_group(tmp_path: Path, monkeypatch):
    def broken(path):
        raise OSError("disk error")

    monkeypatch.setattr(durability_module, "_fsync_file", broken)
    service = FileDurabilityService(IngestPolicy(durability_window_s=0.05))
    try:
        futures = [service.flush(_files(tmp_path, 1, f"c{n}")) for n in range(2)]
        for future in futures:
            with pytest.raises(OSError):
                future.result(timeout=5)
    finally:
        service.stop()


def test_a_full_queue_refuses_instead_of_growing(tmp_path: Path, monkeypatch):
    hold, syncing = threading.Event(), threading.Event()

    def blocked(path):
        syncing.set()
        hold.wait(timeout=5)

    monkeypatch.setattr(durability_module, "_fsync_file", blocked)
    service = FileDurabilityService(IngestPolicy(durability_window_s=0), max_pending=1)
    files = _files(tmp_path, 3)
    try:
        service.flush(files[:1])
        assert syncing.wait(timeout=5)  # taken by the coordinator, blocked syncing
        accepted = 0
        with pytest.raises(FileDurabilityBusy):
            for path in files[1:]:
                service.flush([path])
                accepted += 1
        assert accepted == 1
    finally:
        hold.set()
        service.stop()


def test_nothing_to_sync_completes_at_once():
    service = FileDurabilityService()
    try:
        assert service.flush([], []).result(timeout=1) is None
    finally:
        service.stop()
