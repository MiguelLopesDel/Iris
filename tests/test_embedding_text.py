"""O texto que vira desc_embedding tem de caber no encoder.

O encoder de texto do CLIP corta em 77 tokens sem avisar. O formato anterior
punha a legenda por último, então ela era a primeira coisa descartada — medido
no acervo real: mediana de 130 tokens, ~40% do texto nunca chegava ao modelo.
"""

from __future__ import annotations

import pytest

from core.indexer import _readable_florence_output, build_embedding_text

LIMITE = 77


class _FakeTokenizer:
    """Aproxima o BPE do CLIP: uma palavra ≈ um token, pontuação à parte."""

    def __call__(self, texto: str):
        return {"input_ids": [0] + texto.replace(".", " . ").split() + [0]}


class _FakeModel:
    tokenizer = _FakeTokenizer()


def contar(texto: str) -> int:
    return len(_FakeTokenizer()(texto)["input_ids"])


# ── build_embedding_text ─────────────────────────────────────────────────────


def test_stays_within_the_encoder_limit_even_with_long_input():
    texto = build_embedding_text(
        visual="palavra " * 400,
        ocr="texto " * 400,
        tags="etiqueta " * 400,
        model=_FakeModel(),
    )

    assert contar(texto) <= LIMITE


def test_a_long_caption_cannot_crowd_out_the_ocr():
    """O caso que mais dói em captura de tela: o texto da imagem é o que se busca."""
    texto = build_embedding_text(
        visual="uma descrição interminável " * 100,
        ocr="CONTRATO DE ALUGUEL ASSINADO",
        tags="",
        model=_FakeModel(),
    )

    assert "CONTRATO" in texto


def test_a_long_ocr_cannot_crowd_out_the_caption():
    texto = build_embedding_text(
        visual="gato preto sentado na calçada",
        ocr="palavra " * 300,
        tags="",
        model=_FakeModel(),
    )

    assert "gato preto" in texto


def test_the_caption_comes_first():
    """Quem for cortado no fim tem de ser o menos informativo, não o mais."""
    texto = build_embedding_text(
        visual="LEGENDA", ocr="OCR", tags="TAG", model=_FakeModel()
    )

    assert texto.index("LEGENDA") < texto.index("OCR") < texto.index("TAG")


def test_empty_and_placeholder_parts_are_dropped():
    # "N/A" é o que describe_image devolve quando o Florence não está carregado;
    # indexá-lo seria enfiar ruído constante em milhares de registros.
    texto = build_embedding_text(visual="N/A", ocr="", tags="gato", model=_FakeModel())

    assert texto == "gato"


def test_no_boilerplate_labels():
    """Os rótulos antigos gastavam orçamento sem significar nada."""
    texto = build_embedding_text(
        visual="gato", ocr="texto", tags="tag", model=_FakeModel()
    )

    for rotulo in ("Meme Category", "Context:", "Text:"):
        assert rotulo not in texto


def test_works_without_a_tokenizer():
    class SemTokenizer:
        pass

    texto = build_embedding_text(
        visual="a" * 5000, ocr="", tags="", model=SemTokenizer()
    )

    assert 0 < len(texto) <= 40 * 4


# ── _readable_florence_output ────────────────────────────────────────────────


def test_object_detection_output_becomes_tags():
    saida = {"labels": ["cat", "pavement", "cat"], "bboxes": [[0, 0, 1, 1]] * 3}

    # Rótulos repetidos não acrescentam; caixas não significam nada para busca.
    assert _readable_florence_output(saida) == "cat, pavement"


@pytest.mark.parametrize(
    "saida",
    [
        "VQA>What is this meme (reaction<loc_14><loc_276>",
        "<OD>algo",
        "",
        None,
        123,
    ],
)
def test_unusable_output_becomes_empty_rather_than_garbage(saida):
    """Era isto que contaminava o catálogo: o caminho de erro devolvia o cru."""
    resultado = _readable_florence_output(saida)

    assert "<loc_" not in resultado
    assert "VQA>" not in resultado


def test_a_real_caption_passes_through():
    legenda = "The image shows a black cat sitting on a concrete pavement."

    assert _readable_florence_output(legenda) == legenda
