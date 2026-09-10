"""Gera as fixtures JSON que os testes unitários do Android decodificam.

Por que geradas e não escritas à mão: já existia um teste decodificando uma
resposta de pessoas (``IrisClientTest``), com a fixture ``{"id": 1, "name":
"Ash Ketchum"}`` — inventada por quem escreveu o modelo Kotlin, carregando a
mesma suposição do código de produção: que nome é sempre string. O servidor
devolve ``"name": null`` para pessoa sem nome, a aba Pessoas quebrava inteira,
e o teste passava. Uma fixture escrita à mão só prova que o app concorda
consigo mesmo.

Aqui os *dados* são sintéticos, mas a *forma* sai do código real do servidor:
banco SQLite de verdade -> backend de verdade -> as rotas de verdade. O catálogo
é semeado com os casos-limite que mordem (pessoa sem nome, mídia sem
placeholder, vídeo) justamente para que eles apareçam nas fixtures.

Rodando normalmente, este teste falha se o servidor mudar de forma sem que as
fixtures sejam atualizadas — assim a mudança aparece no diff do PR, do lado do
Android, antes de virar bug em produção. Para atualizar:

    IRIS_UPDATE_FIXTURES=1 pytest tests/test_android_contract_fixtures.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from core.faces import create_face_tables
from core.indexer_db import init_db
from core.thumb_hash import encode_thumb_hash

FIXTURE_DIR = Path(__file__).resolve().parent.parent / (
    "android/app/src/test/resources/fixtures"
)

# Cada arquivo é a resposta crua de uma rota. As primeiras são as que o cliente
# Android decodifica; as demais existem para travar o formato das outras rotas
# de leitura — é essa comparação que prova que declarar response_model no
# FastAPI não descartou nenhum campo silenciosamente.
ENDPOINTS: dict[str, str] = {
    "records.json": "/api/records?page=1&per_page=12",
    "persons.json": "/api/persons",
    "collections.json": "/api/collections",
    "concepts.json": "/api/concepts",
    "info.json": "/api/info",
    "healthz.json": "/healthz",
    "record_detail.json": "/api/records/0",
    "record_metadata.json": "/api/records/0/metadata",
    "collection_members.json": "/api/collections/1/members",
    "person_media.json": "/api/persons/1/media",
    "record_faces.json": "/api/records/0/faces",
    "collections_filter.json": "/api/collections/filter?ids=1",
    "concepts_filter.json": "/api/concepts/filter?ids=1",
    "import_status.json": "/api/import/status",
    "import_review.json": "/api/import/review",
    "import_suggestions.json": "/api/import/suggestions",
    "backup_config.json": "/api/backup/config",
    "timeline.json": "/api/records/timeline",
}

# Campos que dependem da máquina onde o teste roda: caminho absoluto, mtime,
# conteúdo do disco local. Não fazem parte do contrato, variam a cada execução,
# e — mais importante — mascará-los impede que a fixture leve para um repositório
# público o home do desenvolvedor, a lista dos seus bancos locais ou uma mensagem
# de erro do sistema de arquivos dele. Já aconteceu: sem mascarar `backup_dir` a
# fixture capturou "/home/<usuário>/Iris_Backup" e o erro de permissão junto.
_VOLATILE_KEYS = {
    "resolved_path",
    "caminho",
    "thumbnail_url",
    "db_path",
    "media_root",
    "backup_dir",
    "media_originals_root",
    "error",
}

# Listas cujo conteúdo é uma varredura do disco local.
_VOLATILE_LISTS = {"databases", "warnings"}

# Números calculados a partir de embeddings. Os últimos bits dependem da CPU e
# da versão de BLAS, então comparar o valor faz a fixture falhar entre máquinas
# — foi o que quebrou a CI. Zerados, mas mantidos como número: o cliente Kotlin
# decodifica estes arquivos e mudar o tipo quebraria a leitura.
_VOLATILE_NUMBERS = {"score"}

# Dicionários de diagnóstico, com os mesmos floats instáveis dentro.
_VOLATILE_DICTS = {"score_details"}


def _mask_volatile(value):
    if isinstance(value, dict):
        masked = {}
        for k, v in value.items():
            # Incondicional de propósito: mascarar só quando há valor deixaria
            # a fixture diferente entre uma máquina com dados locais e uma sem,
            # e o teste golden falharia dependendo de onde roda.
            if k in _VOLATILE_LISTS:
                masked[k] = []
            elif k in _VOLATILE_DICTS:
                masked[k] = {}
            elif k in _VOLATILE_NUMBERS:
                masked[k] = 0.0
            elif k in _VOLATILE_KEYS:
                masked[k] = "<masked>"
            else:
                masked[k] = _mask_volatile(v)
        return masked
    if isinstance(value, list):
        return [_mask_volatile(v) for v in value]
    return value


class _AsgiClient:
    """Cliente ASGI mínimo — evita acoplar este arquivo a outro de teste."""

    def __init__(self, app):
        self.app = app

    def get(self, path: str):
        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get(path)

        return asyncio.run(run())


def _seed_catalog(root: Path) -> Path:
    """Catálogo pequeno mas com os casos-limite que já quebraram o cliente."""
    media = root / "media"
    media.mkdir(parents=True, exist_ok=True)
    db_path = root / "contract.db"
    init_db(db_path).close()

    image_path = media / "meme.png"
    Image.new("RGB", (64, 64), (200, 30, 90)).save(image_path)
    thumb_hash = encode_thumb_hash(Image.open(image_path))

    embedding = np.ones(768, dtype=np.float32)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """
            INSERT INTO memes (arquivo, caminho, texto_extraido, descricao_ia, tags,
                               file_size, file_mtime, embedding, desc_embedding, thumb_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("meme.png", str(image_path), "texto no meme", "descrição automática",
             "engraçado,gato", 1234, 1_700_000_000.0, embedding.tobytes(),
             embedding.tobytes(), thumb_hash),
        )
        # Mídia sem placeholder: catálogo antigo, ainda sem backfill.
        conn.execute(
            """
            INSERT INTO memes (arquivo, caminho, texto_extraido, descricao_ia, tags,
                               file_size, file_mtime, embedding, desc_embedding, thumb_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("clipe.mp4", str(media / "clipe.mp4"), "", "", "", 999,
             1_700_000_100.0, embedding.tobytes(), embedding.tobytes(), ""),
        )

        create_face_tables(conn)
        # Uma pessoa sem nome (o caso que derrubava a tela) e uma nomeada.
        conn.execute(
            "INSERT INTO persons (id, name, cover_face_id, created_at, updated_at) "
            "VALUES (1, NULL, NULL, '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO persons (id, name, cover_face_id, created_at, updated_at) "
            "VALUES (2, 'Fulano de Tal', NULL, '2026-01-01', '2026-01-01')"
        )
        for person_id in (1, 2):
            conn.execute(
                "INSERT INTO faces (meme_id, person_id, bbox, det_score, embedding, created_at) "
                "VALUES (1, ?, '[0,0,10,10]', 0.9, ?, '2026-01-01')",
                (person_id, np.ones(512, dtype=np.float32).tobytes()),
            )

        # collections.name é NOT NULL no schema, então aqui só cabe variar o
        # conteúdo — acentuação, que já quebrou serialização em outros projetos.
        conn.execute(
            "INSERT INTO collections (id, name, created_at) VALUES (1, 'Abril 2026', '2026-04-01')"
        )
        conn.execute(
            "INSERT INTO collections (id, name, created_at) "
            "VALUES (2, 'Memórias de férias', '2026-04-02')"
        )
        conn.execute(
            "INSERT INTO media_collections (meme_id, collection_id, added_at) "
            "VALUES (1, 1, '2026-04-01')"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def contract_client(tmp_path):
    import server
    from core.backend import create_backend

    db_path = _seed_catalog(tmp_path)
    previous = getattr(server, "_backend", None)
    # load_model=False: a forma do JSON não depende do CLIP, e carregá-lo
    # transformaria um teste de contrato de milissegundos em um de minutos.
    server._backend = create_backend(
        db_path=str(db_path), media_root=str(tmp_path / "media"), load_model=False
    )
    try:
        yield _AsgiClient(server.app)
    finally:
        server._backend = previous


def test_android_fixtures_are_current(contract_client):
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    updating = os.environ.get("IRIS_UPDATE_FIXTURES") == "1"

    stale: list[str] = []
    for filename, url in ENDPOINTS.items():
        response = contract_client.get(url)
        assert response.status_code == 200, f"{url} devolveu {response.status_code}"

        served = json.dumps(_mask_volatile(response.json()), indent=2, ensure_ascii=False)
        target = FIXTURE_DIR / filename

        if updating or not target.exists():
            target.write_text(served + "\n", encoding="utf-8")
            continue
        if target.read_text(encoding="utf-8").strip() != served.strip():
            stale.append(filename)

    assert not stale, (
        "O formato de resposta do servidor mudou e estas fixtures ficaram para trás: "
        f"{', '.join(stale)}.\nOs testes Android decodificam esses arquivos — atualize com:\n"
        "  IRIS_UPDATE_FIXTURES=1 pytest tests/test_android_contract_fixtures.py"
    )


def test_seeded_catalog_actually_exercises_the_edge_cases(contract_client):
    """Guarda o valor deste arquivo: sem os casos-limite, as fixtures são inúteis."""
    persons = contract_client.get("/api/persons").json()["persons"]
    assert any(p["name"] is None for p in persons), (
        "Nenhuma pessoa sem nome nas fixtures — era exatamente esse payload que "
        "derrubava a aba Pessoas."
    )

    records = contract_client.get("/api/records?page=1&per_page=12").json()["records"]
    assert any(r["thumb_hash"] for r in records), "Nenhum placeholder nas fixtures."
    assert any(not r["thumb_hash"] for r in records), (
        "Nenhuma mídia sem placeholder — o cliente precisa aguentar catálogo "
        "ainda não backfillado."
    )


def test_fixtures_never_leak_local_paths():
    """Uma fixture vai para um repositório público — não pode levar o disco junto."""
    suspeitos = ("/home/", "/Users/", "/root/", "C:\\\\")
    for arquivo in sorted(FIXTURE_DIR.glob("*.json")):
        conteudo = arquivo.read_text(encoding="utf-8")
        for trecho in suspeitos:
            assert trecho not in conteudo, (
                f"{arquivo.name} contém um caminho local ({trecho!r}). "
                "Acrescente o campo a _VOLATILE_KEYS/_VOLATILE_LISTS e regenere."
            )


def test_fixtures_hold_no_computed_floats():
    """Pega a classe de campo que já quebrou a CI duas vezes.

    Um float com muitas casas decimais quase sempre veio de um cálculo — uma
    similaridade, uma distância — e os últimos bits dependem da CPU e da versão
    de BLAS da máquina. Comparar isso entre o seu computador e o runner falha
    sem que ninguém tenha mudado o servidor. Se este teste acusar, o campo
    provavelmente pertence a _VOLATILE_NUMBERS.
    """
    def suspeitos(valor, caminho=""):
        if isinstance(valor, dict):
            for k, v in valor.items():
                yield from suspeitos(v, f"{caminho}.{k}" if caminho else k)
        elif isinstance(valor, list):
            for i, v in enumerate(valor):
                yield from suspeitos(v, f"{caminho}[{i}]")
        elif isinstance(valor, float):
            casas = len(repr(valor).partition(".")[2].rstrip("0"))
            if casas > 6:
                yield f"{caminho} = {valor!r}"

    for arquivo in sorted(FIXTURE_DIR.glob("*.json")):
        achados = list(suspeitos(json.loads(arquivo.read_text(encoding="utf-8"))))
        assert not achados, (
            f"{arquivo.name} guarda valor calculado, que difere entre máquinas: "
            + ", ".join(achados)
        )
