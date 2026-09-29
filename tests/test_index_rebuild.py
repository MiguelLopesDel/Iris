from __future__ import annotations

import sys
from types import ModuleType

import pytest

from core.index_rebuild import rebuild_indexes


def test_rebuild_indexes_uses_indexer_and_invalidates_afterward(tmp_path, monkeypatch) -> None:
    calls: list[object] = []
    fake_indexer = ModuleType("core.indexer")
    fake_indexer.create_faiss_indices = lambda db_path, model_name: calls.append(
        (db_path, model_name)
    )
    monkeypatch.setitem(sys.modules, "core.indexer", fake_indexer)

    rebuild_indexes(
        db_path=tmp_path / "library.db",
        model_name="model-v1",
        on_finished=lambda: calls.append("finished"),
    )

    assert calls == [(tmp_path / "library.db", "model-v1"), "finished"]


def test_rebuild_indexes_invalidates_even_when_indexer_fails(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    fake_indexer = ModuleType("core.indexer")

    def fail_rebuild(_db_path, _model_name):
        raise RuntimeError("index rebuild failed")

    fake_indexer.create_faiss_indices = fail_rebuild
    monkeypatch.setitem(sys.modules, "core.indexer", fake_indexer)

    with pytest.raises(RuntimeError, match="index rebuild failed"):
        rebuild_indexes(
            db_path=tmp_path / "library.db",
            model_name="model-v1",
            on_finished=lambda: calls.append("finished"),
        )

    assert calls == ["finished"]
