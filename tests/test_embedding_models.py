"""O seam de encoder e a razão de ele existir.

O teste que importa aqui é o de independência de lote: ele é a prova medida de
por que o SigLIP não pode passar pelo ``sentence-transformers``. Ele baixa o
checkpoint, então fica atrás de ``IRIS_INTEGRATION=1`` como os outros testes que
carregam modelo de verdade.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from core.embedding_models import (
    DEFAULT_MODEL,
    is_siglip,
    max_text_tokens,
    resolve_embedding_model,
)

MODELO_SIGLIP = "google/siglip2-base-patch16-224"


def test_the_configured_model_comes_from_the_variable_the_project_already_uses(monkeypatch):
    # IRIS_MODEL já é lido pelo server.py e pelo docker-compose; uma segunda
    # variável para a mesma decisão só criaria duas fontes de verdade.
    monkeypatch.setenv("IRIS_MODEL", MODELO_SIGLIP)

    assert resolve_embedding_model() == MODELO_SIGLIP


@pytest.mark.parametrize("valor", ["", "   "])
def test_an_empty_variable_falls_back_to_the_default(monkeypatch, valor):
    monkeypatch.setenv("IRIS_MODEL", valor)

    assert resolve_embedding_model() == DEFAULT_MODEL


def test_the_default_is_still_clip(monkeypatch):
    # O acervo do usuário está indexado com CLIP; mudar o padrão o quebraria.
    monkeypatch.delenv("IRIS_MODEL", raising=False)

    assert resolve_embedding_model() == DEFAULT_MODEL


def test_siglip_checkpoints_are_recognised():
    assert is_siglip(MODELO_SIGLIP)
    assert is_siglip("google/siglip2-so400m-patch14-384")
    assert not is_siglip(DEFAULT_MODEL)


class _EncoderFalso:
    def __init__(self, valor):
        self._valor = valor

    def get_max_seq_length(self):
        return self._valor


def test_the_text_budget_is_asked_of_the_encoder():
    assert max_text_tokens(_EncoderFalso(77)) == 77
    assert max_text_tokens(_EncoderFalso(64)) == 64


def test_an_encoder_that_will_not_say_gets_the_clip_ceiling():
    # Melhor orçar pelo teto conhecido do CLIP do que estourar em silêncio.
    assert max_text_tokens(object()) == 77
    assert max_text_tokens(_EncoderFalso(None)) == 77


def test_a_declared_limit_wins_over_the_getter():
    class Ambos:
        max_text_tokens = 64

        def get_max_seq_length(self):
            return 77

    assert max_text_tokens(Ambos()) == 64


@pytest.mark.skipif(
    os.environ.get("IRIS_INTEGRATION") != "1",
    reason="baixa o checkpoint SigLIP 2; rode com IRIS_INTEGRATION=1",
)
def test_siglip_text_embeddings_do_not_depend_on_the_rest_of_the_batch():
    """A regressão que motivou o módulo inteiro.

    Pelo ``sentence-transformers`` esta mesma comparação dá cosseno ~0,76: o
    vetor da consulta muda conforme o que mais está no lote, porque o
    preenchimento vai até a maior sequência e a torre de texto do SigLIP não é
    invariante a isso. Um índice construído assim depende da ordem dos arquivos.
    """
    from core.embedding_models import load_encoder

    encoder = load_encoder(MODELO_SIGLIP, device="cpu")
    consulta = "a photo of a person"

    sozinha = encoder.encode(consulta)
    acompanhada = encoder.encode(
        [consulta, "a very long sentence that makes this batch much longer than before"]
    )[0]

    normalizar = lambda v: v / np.linalg.norm(v)  # noqa: E731
    assert float(normalizar(sozinha) @ normalizar(acompanhada)) > 0.999


@pytest.mark.skipif(
    os.environ.get("IRIS_INTEGRATION") != "1",
    reason="baixa o checkpoint SigLIP 2; rode com IRIS_INTEGRATION=1",
)
def test_siglip_encoder_matches_the_surface_the_pipeline_expects():
    from PIL import Image

    from core.embedding_models import load_encoder

    encoder = load_encoder(MODELO_SIGLIP, device="cpu")

    assert encoder.max_text_tokens == 64  # menor que os 77 do CLIP
    assert encoder.tokenizer is not None  # build_embedding_text depende disso
    assert encoder.encode("uma frase").shape == (768,)
    assert encoder.encode(["uma", "duas"]).shape == (2, 768)
    assert encoder.encode(Image.new("RGB", (224, 224), (30, 30, 30))).shape == (768,)


def _catalogo_com_modelo(tmp_path, modelo: str):
    """Catálogo mínimo cujos vetores declaram o modelo que os produziu."""
    import sqlite3

    import numpy as np

    media = tmp_path / "media"
    media.mkdir()
    db_path = tmp_path / "iris.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT UNIQUE, caminho TEXT, relative_path TEXT, storage_path TEXT,
            library_id INTEGER, texto_extraido TEXT, descricao_ia TEXT, tags TEXT,
            model_name TEXT, embedding BLOB, desc_embedding BLOB
        )
        """
    )
    vetor = np.ones(768, dtype=np.float32)
    (media / "a.jpg").write_bytes(b"fake")
    conn.execute(
        "INSERT INTO memes (arquivo, caminho, relative_path, storage_path, library_id,"
        " texto_extraido, descricao_ia, tags, model_name, embedding, desc_embedding)"
        " VALUES (?, ?, ?, ?, 1, '', '', '', ?, ?, ?)",
        ("a.jpg", str(media / "a.jpg"), "a.jpg", "a.jpg", modelo, vetor.tobytes(), vetor.tobytes()),
    )
    conn.commit()
    conn.close()
    return db_path, media


def test_searching_a_clip_catalog_with_siglip_is_refused(tmp_path):
    """Mesma dimensão, espaços diferentes — a checagem de forma não pega isso.

    ``siglip2-base-patch16-224`` também dá 768 dimensões, então sem esta
    comparação por nome a busca passaria e devolveria ranking aleatório.
    """
    import numpy as np

    from core.search_engine import IrisEngine

    db_path, media = _catalogo_com_modelo(tmp_path, DEFAULT_MODEL)
    engine = IrisEngine(
        db_path=str(db_path), media_root=media, model_name=MODELO_SIGLIP, load_model=False
    )

    with pytest.raises(ValueError, match="não são comparáveis|nao sao comparaveis"):
        engine._validate_dimension(np.ones((1, 768), dtype=np.float32))


def test_the_matching_model_passes(tmp_path):
    import numpy as np

    from core.search_engine import IrisEngine

    db_path, media = _catalogo_com_modelo(tmp_path, DEFAULT_MODEL)
    engine = IrisEngine(
        db_path=str(db_path), media_root=media, model_name=DEFAULT_MODEL, load_model=False
    )

    engine._validate_dimension(np.ones((1, 768), dtype=np.float32))
