"""Tests for encoder selection, text padding, and catalog compatibility."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from pytest import MonkeyPatch

from core.embedding_models import (
    DEFAULT_MODEL,
    is_siglip,
    max_text_tokens,
    resolve_embedding_model,
)

SIGLIP_MODEL = "google/siglip2-base-patch16-224"


def test_configured_model_uses_existing_environment_variable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIS_MODEL", SIGLIP_MODEL)
    assert resolve_embedding_model() == SIGLIP_MODEL


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_environment_variable_uses_default(
    monkeypatch: MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("IRIS_MODEL", value)
    assert resolve_embedding_model() == DEFAULT_MODEL


def test_default_model_remains_clip(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("IRIS_MODEL", raising=False)
    assert resolve_embedding_model() == DEFAULT_MODEL


def test_indexer_uses_environment_model_when_flag_is_omitted(
    monkeypatch: MonkeyPatch,
) -> None:
    from core.indexer import parse_arguments

    monkeypatch.setenv("IRIS_MODEL", SIGLIP_MODEL)
    monkeypatch.setattr("sys.argv", ["indexer", "--db", "catalog.db"])

    assert parse_arguments().model_name == SIGLIP_MODEL


def test_only_explicit_google_siglip_checkpoints_use_siglip_adapter() -> None:
    assert is_siglip(SIGLIP_MODEL)
    assert is_siglip("google/siglip2-so400m-patch14-384")
    assert not is_siglip(DEFAULT_MODEL)
    assert not is_siglip("organization/not-really-siglip-compatible")


class _FakeEncoder:
    def __init__(self, value: int | None):
        self._value = value

    def get_max_seq_length(self) -> int | None:
        return self._value


def test_text_budget_is_read_from_encoder() -> None:
    assert max_text_tokens(_FakeEncoder(77)) == 77
    assert max_text_tokens(_FakeEncoder(64)) == 64


def test_encoder_without_limit_uses_clip_ceiling() -> None:
    assert max_text_tokens(object()) == 77
    assert max_text_tokens(_FakeEncoder(None)) == 77


def test_declared_limit_takes_priority_over_getter() -> None:
    class _BothLimits:
        max_text_tokens = 64

        def get_max_seq_length(self) -> int:
            return 77

    assert max_text_tokens(_BothLimits()) == 64


@pytest.mark.skipif(
    os.environ.get("IRIS_INTEGRATION") != "1",
    reason="downloads the SigLIP 2 checkpoint; run with IRIS_INTEGRATION=1",
)
def test_siglip_text_embeddings_are_independent_of_batch_contents() -> None:
    """Protect the fixed-padding behavior that motivated the encoder seam."""
    from core.embedding_models import load_encoder

    encoder = load_encoder(SIGLIP_MODEL, device="cpu")
    query = "a photo of a person"
    alone = encoder.encode(query)
    with_long_text = encoder.encode(
        [query, "a very long sentence that makes this batch much longer than before"]
    )[0]
    normalized_alone = alone / np.linalg.norm(alone)
    normalized_with_long_text = with_long_text / np.linalg.norm(with_long_text)
    assert float(normalized_alone @ normalized_with_long_text) > 0.999


@pytest.mark.skipif(
    os.environ.get("IRIS_INTEGRATION") != "1",
    reason="downloads the SigLIP 2 checkpoint; run with IRIS_INTEGRATION=1",
)
def test_siglip_encoder_matches_pipeline_interface() -> None:
    from PIL import Image

    from core.embedding_models import SiglipEncoder, load_encoder

    encoder = load_encoder(SIGLIP_MODEL, device="cpu")
    assert isinstance(encoder, SiglipEncoder)
    assert encoder.max_text_tokens == 64
    assert encoder.tokenizer is not None
    assert encoder.encode("a sentence").shape == (768,)
    assert encoder.encode(["one", "two"]).shape == (2, 768)
    assert encoder.encode(Image.new("RGB", (224, 224), (30, 30, 30))).shape == (768,)


def _catalog_with_models(
    tmp_path: Path, model_names: str | Sequence[str]
) -> tuple[Path, Path]:
    """Create a minimal catalog through the production schema initializer."""
    from core.indexer_db import init_db

    names = [model_names] if isinstance(model_names, str) else list(model_names)
    media_root = tmp_path / "media"
    media_root.mkdir()
    db_path = tmp_path / "iris.db"
    vector = np.ones(768, dtype=np.float32)
    with init_db(db_path) as connection:
        for index, model_name in enumerate(names):
            filename = f"{index}.jpg"
            media_path = media_root / filename
            media_path.write_bytes(b"fake")
            connection.execute(
                "INSERT INTO memes (arquivo, caminho, relative_path, storage_path, "
                "texto_extraido, descricao_ia, tags, model_name, embedding, desc_embedding) "
                "VALUES (?, ?, ?, ?, '', '', '', ?, ?, ?)",
                (
                    filename,
                    str(media_path),
                    filename,
                    filename,
                    model_name,
                    vector.tobytes(),
                    vector.tobytes(),
                ),
            )
    return db_path, media_root


def test_search_rejects_clip_catalog_with_siglip_query(tmp_path: Path) -> None:
    """Model identity catches incompatible spaces with the same dimension."""
    from core.search_engine import IrisEngine

    db_path, media_root = _catalog_with_models(tmp_path, DEFAULT_MODEL)
    engine = IrisEngine(
        db_path=str(db_path),
        media_root=media_root,
        model_name=SIGLIP_MODEL,
        load_model=False,
    )
    with pytest.raises(ValueError, match="not comparable"):
        engine._validate_dimension(np.ones((1, 768), dtype=np.float32))


def test_search_rejects_catalog_mixed_with_current_model(tmp_path: Path) -> None:
    from core.search_engine import IrisEngine

    db_path, media_root = _catalog_with_models(
        tmp_path, [DEFAULT_MODEL, SIGLIP_MODEL]
    )
    engine = IrisEngine(
        db_path=str(db_path),
        media_root=media_root,
        model_name=DEFAULT_MODEL,
        load_model=False,
    )
    with pytest.raises(ValueError, match="not comparable"):
        engine._validate_dimension(np.ones((1, 768), dtype=np.float32))


def test_matching_catalog_model_passes(tmp_path: Path) -> None:
    from core.search_engine import IrisEngine

    db_path, media_root = _catalog_with_models(tmp_path, DEFAULT_MODEL)
    engine = IrisEngine(
        db_path=str(db_path),
        media_root=media_root,
        model_name=DEFAULT_MODEL,
        load_model=False,
    )
    engine._validate_dimension(np.ones((1, 768), dtype=np.float32))
