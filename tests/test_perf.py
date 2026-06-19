"""Testes do tracer de performance — no-op por padrão, ring buffer quando ligado.

Regressão para o vazamento: antes, `_events` (lista global) crescia a cada `trace()`
em todo endpoint e só era descarregada no shutdown.
"""
from __future__ import annotations

import core.perf as perf


def test_trace_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(perf, "ENABLED", False)
    perf.reset()
    for _ in range(200):
        with perf.trace("x"):
            pass
    assert len(perf._events) == 0


def test_trace_records_when_enabled(monkeypatch):
    monkeypatch.setattr(perf, "ENABLED", True)
    perf.reset()
    with perf.trace("a"):
        pass
    assert len(perf._events) == 1
    perf.reset()


def test_ring_buffer_caps_memory(monkeypatch):
    monkeypatch.setattr(perf, "ENABLED", True)
    perf.reset()
    for _ in range(perf._MAX_EVENTS + 500):
        with perf.trace("x"):
            pass
    # Não cresce sem limite — o ring buffer trava no teto.
    assert len(perf._events) == perf._MAX_EVENTS
    perf.reset()


def test_dump_tolerates_ring_buffer(tmp_path, monkeypatch):
    monkeypatch.setattr(perf, "ENABLED", True)
    monkeypatch.setattr(perf, "_LOG_PATH", tmp_path / "perf.log")
    perf.reset()
    with perf.trace("a"):
        pass
    perf.dump()
    assert (tmp_path / "perf.log").exists()
    assert "a" in (tmp_path / "perf.log").read_text()
    perf.reset()


def test_nested_trace_disabled_has_zero_overhead_path(monkeypatch):
    # Mesmo aninhado, desligado não grava nada.
    monkeypatch.setattr(perf, "ENABLED", False)
    perf.reset()
    with perf.trace("outer"):
        with perf.trace("inner"):
            pass
    assert len(perf._events) == 0
