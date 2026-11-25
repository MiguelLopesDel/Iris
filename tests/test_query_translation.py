"""A multilingual model searches with the query as typed plus a local translation."""
from __future__ import annotations

import numpy as np

from core import query_translation, search_engine
from core.vector_store import VectorStore


class _Encoder:
    """Maps each text to a fixed unit vector so the combination is checkable."""

    vectors = {"cachorro": np.array([1.0, 0.0, 0.0]), "dog": np.array([0.0, 1.0, 0.0])}

    def __init__(self):
        self.calls: list[str] = []

    def encode(self, text):
        self.calls.append(text)
        return self.vectors.get(text, np.array([0.0, 0.0, 1.0]))


def _engine(model_name: str) -> search_engine.IrisEngine:
    engine = search_engine.IrisEngine.__new__(search_engine.IrisEngine)
    engine.model = _Encoder()
    engine.model_name = model_name
    engine.vector_store = VectorStore.__new__(VectorStore)
    return engine


def test_siglip_averages_the_typed_query_and_its_local_translation(monkeypatch):
    monkeypatch.setattr(query_translation, "to_english", lambda text: "dog")
    engine = _engine("google/siglip2-base-patch16-224")

    vector, shown = engine.encode_text("cachorro")

    assert engine.model.calls == ["cachorro", "dog"]
    assert np.allclose(vector[0][:2], [2 ** -0.5, 2 ** -0.5])
    assert shown == "dog"


def test_without_a_translation_the_typed_query_is_used_alone(monkeypatch):
    monkeypatch.setattr(query_translation, "to_english", lambda text: "")
    engine = _engine("google/siglip2-base-patch16-224")

    vector, shown = engine.encode_text("cachorro")

    assert engine.model.calls == ["cachorro"]
    assert np.allclose(vector[0][:2], [1.0, 0.0])
    assert shown == "cachorro"


def test_siglip_never_calls_the_web_translator(monkeypatch):
    monkeypatch.setattr(query_translation, "to_english", lambda text: "")

    def web(*_a, **_k):
        raise AssertionError("the query must not leave the machine")

    monkeypatch.setattr(search_engine, "GoogleTranslator", web)
    _engine("google/siglip2-base-patch16-224").encode_text("cachorro")


def test_clip_keeps_its_english_translation_path(monkeypatch):
    calls = []

    class Web:
        def __init__(self, **_k):
            pass

        def translate(self, text):
            calls.append(text)
            return "dog"

    monkeypatch.setattr(search_engine, "GoogleTranslator", Web)
    monkeypatch.setenv("IRIS_TRANSLATE_QUERIES", "1")
    engine = _engine("sentence-transformers/clip-ViT-L-14")

    _vector, shown = engine.encode_text("cachorro")

    assert calls == ["cachorro"] and shown == "dog"


def test_local_translation_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("IRIS_LOCAL_QUERY_TRANSLATION", "0")
    query_translation.to_english.cache_clear()

    assert query_translation.to_english("cachorro") == ""
