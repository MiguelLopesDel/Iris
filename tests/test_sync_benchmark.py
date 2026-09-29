from __future__ import annotations

import hashlib

from scripts.benchmark_sync_pipeline import _is_loopback_url, _make_fixture


def test_sync_benchmark_refuses_non_loopback_targets():
    assert _is_loopback_url("http://127.0.0.1:8851")
    assert _is_loopback_url("http://localhost:8851")
    assert not _is_loopback_url("https://127.0.0.1:8851")
    assert not _is_loopback_url("http://192.168.1.10:8851")
    assert not _is_loopback_url("https://servidor.tail.example")


def test_sync_benchmark_fixture_has_declared_size_and_digest(tmp_path):
    path = tmp_path / "synthetic.jpg"
    digest = _make_fixture(path, 16 * 1024, color_seed=35)
    fixture = path.with_suffix(".jpg")
    assert fixture.stat().st_size == 16 * 1024
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == digest
