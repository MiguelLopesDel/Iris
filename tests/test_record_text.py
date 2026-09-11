"""O texto de um item vem do SQLite quando alguém olha para ele.

Segurar as nove colunas de texto em memória custava 3,1 KB por item — mais da
metade do que sobrava no processo depois que os embeddings viraram arquivo
mapeado. Estes testes fixam as três coisas que tornam a troca honesta: o texto
continua chegando, ele não volta a ser residente, e ler mil candidatos não custa
mil consultas.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from core.record_text import SCORING_COLUMNS, TextStore, normalize_fields
from core.search_types import IndexRecord, normalize_text

COLUNAS = (*SCORING_COLUMNS, "visual_json")


class _Contador:
    """Um DatabaseManager mínimo que conta os SELECTs que recebe."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.consultas = 0

    def get_connection(self) -> sqlite3.Connection:
        outer = self

        class _Proxy:
            def execute(self, sql, parameters=()):
                outer.consultas += 1
                return outer._conn.execute(sql, parameters)

        return _Proxy()


@pytest.fixture
def catalogo():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        f"CREATE TABLE memes (id INTEGER PRIMARY KEY, {', '.join(f'{c} TEXT' for c in COLUNAS)})"
    )
    for item in range(1, 1001):
        conn.execute(
            f"INSERT INTO memes (id, {', '.join(COLUNAS)}) VALUES ({', '.join('?' * (len(COLUNAS) + 1))})",
            (
                item,
                f"OCR do item {item}",
                f"descrição {item}",
                "gato, meme",
                "gato",
                "wojak",
                "Frieren",
                "ironia",
                "sala",
                '{"cores": ["azul"]}',
            ),
        )
    conn.commit()
    return _Contador(conn)


def _registro(store: TextStore, db_id: int) -> IndexRecord:
    return IndexRecord(
        index=db_id - 1,
        arquivo=f"{db_id}.jpg",
        caminho=f"/{db_id}.jpg",
        resolved_path=None,
        embedding=np.zeros(1, dtype=np.float32),
        desc_embedding=None,
        db_id=db_id,
        text_store=store,
    )


def test_o_texto_chega_mesmo_sem_estar_no_registro(catalogo):
    store = TextStore(catalogo, COLUNAS)
    registro = _registro(store, 7)

    assert registro.texto_extraido == "OCR do item 7"
    assert registro.tags == "gato, meme"
    assert registro.source_work == "Frieren"
    assert registro.visual_json == '{"cores": ["azul"]}'


def test_ler_mil_candidatos_nao_custa_mil_consultas(catalogo):
    store = TextStore(catalogo, COLUNAS)
    ids = list(range(1, 1001))

    store.prefetch(ids)
    consultas_do_prefetch = catalogo.consultas
    for db_id in ids:
        _registro(store, db_id).descricao_ia
    # Uma por lote, e nada depois: o que foi aquecido é servido do cache.
    assert consultas_do_prefetch <= 4
    assert catalogo.consultas == consultas_do_prefetch


def test_o_cache_e_limitado_e_nao_vira_o_acervo_residente(catalogo):
    store = TextStore(catalogo, COLUNAS, cache_size=64)

    for db_id in range(1, 1001):
        _registro(store, db_id).descricao_ia

    assert len(store._scoring._cache) <= 64
    assert len(store._normalized) <= 64


def test_o_visual_json_nao_viaja_com_o_texto_de_ranqueamento(catalogo):
    store = TextStore(catalogo, COLUNAS)

    store.prefetch([1, 2, 3])
    aquecido = catalogo.consultas
    _registro(store, 1).tags  # ranqueamento: já está em memória
    assert catalogo.consultas == aquecido

    _registro(store, 1).visual_json  # detalhe: só agora é lido
    assert catalogo.consultas == aquecido + 1


def test_uma_linha_que_sumiu_le_como_vazia_e_nao_e_perguntada_de_novo(catalogo):
    store = TextStore(catalogo, COLUNAS)
    registro = _registro(store, 99999)

    assert registro.texto_extraido == ""
    depois = catalogo.consultas
    assert registro.descricao_ia == ""
    assert catalogo.consultas == depois


def test_texto_passado_a_mao_vence_o_acervo(catalogo):
    store = TextStore(catalogo, COLUNAS)
    registro = IndexRecord(
        index=0,
        arquivo="a.jpg",
        caminho="/a.jpg",
        resolved_path=None,
        embedding=np.zeros(1, dtype=np.float32),
        desc_embedding=None,
        db_id=7,
        text_store=store,
        texto_extraido="escrito à mão",
    )

    assert registro.texto_extraido == "escrito à mão"
    assert catalogo.consultas == 0


def test_um_acervo_sem_as_colunas_responde_vazio_em_vez_de_estourar():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, arquivo TEXT)")
    store = TextStore(_Contador(conn), ["arquivo"])

    assert store.fetch(1) == ("",) * len(SCORING_COLUMNS)
    assert store.fetch_detail(1) == ""


def test_dobrar_campo_a_campo_da_o_mesmo_texto_que_dobrar_tudo_junto():
    campos = ("Olá, MUNDO!", "ação", "", "gato-meme")

    dobrados, junto = normalize_fields(campos)

    assert dobrados == tuple(normalize_text(campo) for campo in campos)
    assert junto == normalize_text(" ".join(campo for campo in campos if campo))
