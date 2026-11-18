"""Documents are told apart from memes by look, and their kind by their text."""
from __future__ import annotations

import numpy as np

from core import documents
from core.documents import PromptMatrix, classify, kind_from_text


def test_ocr_cues_settle_the_kind():
    assert kind_from_text("REPÚBLICA FEDERATIVA DO BRASIL\\nCARTEIRA NACIONAL DE HABILITAÇÃO") == "identidade"
    assert kind_from_text("Comprovante de transferência Pix  R$ 10,00") == "comprovantes"
    assert kind_from_text("23790.12345 60000.123456 78901.234567 1 98760000010000") == "boletos"
    assert kind_from_text("DANFE NFC-e consumidor") == "notas"
    assert kind_from_text("Receituário  Dr. Fulano CRM 123456") == "saude"


def test_a_word_alone_in_a_meme_is_not_a_document():
    assert kind_from_text("quando o boleto chega antes do salário") is None
    assert kind_from_text("meu passaporte pro rolê") is None
    assert kind_from_text("") is None


def _prompts():
    # Axis 0: identity cards, axis 1: paper documents, axis 2: memes.
    vectors = np.eye(3, dtype=np.float32)
    return PromptMatrix(vectors, ("identidade", "outros", None))


def test_the_image_decides_when_there_is_no_text():
    matrix = np.array(
        [
            [0.9, 0.1, 0.0],   # an ID card
            [0.2, 0.5, 0.1],   # a paper page
            [0.3, 0.0, 0.9],   # a meme that mentions documents
            [0.1, 0.1, 0.1],   # as much like a meme as like a document: not one
        ],
        dtype=np.float32,
    )
    present = np.array([True, True, True, True])
    some_text = {i: "texto qualquer lido na imagem" for i in range(4)}

    assert classify(matrix, present, _prompts(), some_text) == {0: "identidade", 1: "outros"}


def test_a_picture_without_text_is_not_a_document():
    """A portrait or an icon can look formal; a document always has text."""
    matrix = np.array([[0.9, 0.1, 0.0], [0.9, 0.1, 0.0]], dtype=np.float32)
    texts = {1: "NOME FILIACAO DATA DE NASCIMENTO"}

    assert classify(matrix, np.array([True, True]), _prompts(), texts) == {1: "identidade"}


def test_text_wins_over_the_image_and_reaches_items_without_one():
    matrix = np.array([[0.0, 0.0, 0.9], [0.0, 0.0, 0.0]], dtype=np.float32)
    present = np.array([True, False])
    texts = {0: "Comprovante de pagamento Pix", 1: "CARTEIRA DE IDENTIDADE"}

    assert classify(matrix, present, _prompts(), texts) == {0: "comprovantes", 1: "identidade"}


def test_prompts_are_built_for_every_kind_and_contrast():
    calls = []

    def encode(text):
        calls.append(text)
        return np.ones(4, dtype=np.float32)

    built = documents.build_prompts(encode)
    assert len(built.owners) == len(calls)
    assert {o for o in built.owners if o} == {k.key for k in documents.KINDS}
    assert built.owners.count(None) == len(documents.CONTRAST_PROMPTS)
    assert np.allclose(np.linalg.norm(built.vectors, axis=1), 1.0)


def test_rows_need_not_be_normalised():
    matrix = np.array([[9.0, 1.0, 0.0]], dtype=np.float32)

    texts = {0: "texto qualquer lido na imagem"}
    assert classify(matrix, np.array([True]), _prompts(), texts) == {0: "identidade"}

