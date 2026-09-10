"""A consulta de busca sai da máquina — isso precisa ser desligável e declarado.

O README promete que nada sai do aparelho. Isso é verdade para a mídia, mas a
busca semântica traduz o texto digitado antes de codificá-lo, e essa tradução é
uma chamada a um serviço externo. Enquanto o modelo de embedding for treinado
só em inglês, a troca existe; o que não pode existir é ela ser obrigatória e
não declarada — o interruptor na interface era ``checked hidden``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.search_engine import queries_may_leave_the_machine

RAIZ = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("valor", ["0", "false", "no", "off", "OFF", " 0 "])
def test_translation_can_be_switched_off(monkeypatch, valor):
    monkeypatch.setenv("IRIS_TRANSLATE_QUERIES", valor)

    assert queries_may_leave_the_machine() is False


@pytest.mark.parametrize("valor", ["1", "true", "sim-qualquer-coisa"])
def test_anything_else_keeps_the_current_behaviour(monkeypatch, valor):
    # Mudar o padrão silenciosamente degradaria a busca de quem já usa.
    monkeypatch.setenv("IRIS_TRANSLATE_QUERIES", valor)

    assert queries_may_leave_the_machine() is True


def test_default_is_unchanged_when_unset(monkeypatch):
    monkeypatch.delenv("IRIS_TRANSLATE_QUERIES", raising=False)

    assert queries_may_leave_the_machine() is True


def test_the_readme_does_not_overclaim():
    """A frase de abertura não pode dizer que nada sai do aparelho."""
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")

    assert "no data leaving your device" not in readme, (
        "o README volta a prometer que nada sai; a consulta de busca sai"
    )
    assert "IRIS_TRANSLATE_QUERIES" in readme, (
        "a ressalva sumiu do README junto com a forma de desligar"
    )


def test_the_switch_is_visible_in_the_interface():
    html = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")

    linha = next(
        (linha for linha in html.splitlines() if 'id="search-translate"' in linha),
        "",
    )
    assert linha, "o interruptor de tradução sumiu da interface"
    assert "hidden" not in linha, (
        "o interruptor voltou a ficar escondido: o usuário não consegue impedir "
        "que a consulta saia da máquina"
    )
