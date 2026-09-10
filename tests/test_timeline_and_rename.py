"""Timeline do acervo e renomeação de mídia.

O rename mexe em disco e banco ao mesmo tempo: a resolução de caminho usa
``storage_path``, ``relative_path`` e ``caminho``, então atualizar só uma delas
deixa a mídia inalcançável. Estes testes existem para travar esse contrato.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from core.indexer_db import init_db


class _AsgiClient:
    def __init__(self, app):
        self.app = app

    def _run(self, method: str, path: str, **kw):
        async def go():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.request(method, path, **kw)

        return asyncio.run(go())

    def get(self, path: str, **kw):
        return self._run("GET", path, **kw)

    def post(self, path: str, **kw):
        return self._run("POST", path, **kw)


def _seed(root: Path) -> Path:
    """Catálogo com meses distintos, para a timeline ter mais de um balde."""
    media = root / "media"
    media.mkdir(parents=True, exist_ok=True)
    db_path = root / "catalog.db"
    init_db(db_path).close()

    embedding = np.ones(768, dtype=np.float32)
    # (nome, epoch)  — junho/2026, junho/2026, maio/2026, vídeo em abril/2026
    linhas = [
        ("foto-a.png", 1_781_000_000.0, "image"),
        ("foto-b.png", 1_780_900_000.0, "image"),
        ("foto-c.png", 1_778_000_000.0, "image"),
        ("clipe.mp4", 1_775_000_000.0, "video"),
    ]
    conn = sqlite3.connect(db_path)
    try:
        for nome, mtime, _kind in linhas:
            caminho = media / nome
            if caminho.suffix == ".png":
                Image.new("RGB", (32, 32), (10, 20, 30)).save(caminho)
            else:
                caminho.write_bytes(b"nao-e-video-de-verdade")
            conn.execute(
                "INSERT INTO memes (arquivo, caminho, relative_path, texto_extraido,"
                " descricao_ia, tags, file_mtime, embedding, desc_embedding)"
                " VALUES (?, ?, ?, '', '', '', ?, ?, ?)",
                (nome, str(caminho), nome, mtime, embedding.tobytes(), embedding.tobytes()),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def client(tmp_path):
    import server
    from core.backend import create_backend

    db_path = _seed(tmp_path)
    anterior = getattr(server, "_backend", None)
    server._backend = create_backend(
        db_path=str(db_path), media_root=str(tmp_path / "media"), load_model=False
    )
    server._invalidate_view_caches()
    try:
        yield _AsgiClient(server.app)
    finally:
        server._backend = anterior
        server._invalidate_view_caches()


# ── Timeline ─────────────────────────────────────────────────────────────────


def test_timeline_groups_by_month_newest_first(client):
    body = client.get("/api/records/timeline").json()

    assert body["total"] == 4
    meses = [b["month"] for b in body["buckets"]]
    assert meses == sorted(meses, reverse=True), "a timeline tem de seguir a galeria"
    assert sum(b["count"] for b in body["buckets"]) == body["total"]


def test_timeline_offsets_line_up_with_the_gallery(client):
    """O offset é a promessa do scrubber: saltar para o mês sem baixar o resto."""
    timeline = client.get("/api/records/timeline").json()
    pagina = client.get("/api/records?page=1&per_page=100&sort_by=data").json()

    for bucket in timeline["buckets"]:
        registro = pagina["records"][bucket["offset"]]
        assert registro["file_mtime"], "registro sem data no offset anunciado"


def test_timeline_respects_the_media_type_filter(client):
    todos = client.get("/api/records/timeline").json()
    videos = client.get("/api/records/timeline?media_type=video").json()

    assert todos["total"] == 4
    assert videos["total"] == 1


# ── Rename ───────────────────────────────────────────────────────────────────


def test_rename_moves_the_file_and_updates_every_path_column(client, tmp_path):
    antes = client.get("/api/records?page=1&per_page=100&sort_by=data").json()["records"]
    alvo = next(r for r in antes if r["arquivo"] == "foto-a.png")

    resposta = client.post(f"/api/records/{alvo['index']}/rename", data={"name": "praia"})
    assert resposta.status_code == 200, resposta.text

    assert (tmp_path / "media" / "praia.png").is_file()
    assert not (tmp_path / "media" / "foto-a.png").exists()

    depois = client.get("/api/records?page=1&per_page=100&sort_by=data").json()["records"]
    renomeado = next(r for r in depois if r["db_id"] == alvo["db_id"])
    assert renomeado["arquivo"] == "praia.png"
    # O que importa de verdade: a mídia continua alcançável depois do rename.
    assert renomeado["resolved_path"], "o registro ficou sem caminho resolvível"
    assert Path(renomeado["resolved_path"]).is_file()


def test_rename_keeps_the_original_extension(client, tmp_path):
    registros = client.get("/api/records?page=1&per_page=100&sort_by=data").json()["records"]
    video = next(r for r in registros if r["arquivo"] == "clipe.mp4")

    client.post(f"/api/records/{video['index']}/rename", data={"name": "ferias.png"})

    # Deixar o usuário trocar a extensão transformaria o vídeo em algo que a
    # galeria classifica como imagem.
    assert (tmp_path / "media" / "ferias.mp4").is_file()
    depois = client.get("/api/records?page=1&per_page=100&sort_by=data").json()["records"]
    assert next(r for r in depois if r["db_id"] == video["db_id"])["media_type"] == "video"


@pytest.mark.parametrize("nome", ["../fuga", "pasta/foto", ".."])
def test_rename_refuses_anything_that_is_a_path(client, nome):
    registros = client.get("/api/records?page=1&per_page=100&sort_by=data").json()["records"]
    alvo = registros[0]

    resposta = client.post(f"/api/records/{alvo['index']}/rename", data={"name": nome})

    assert resposta.status_code == 400


def test_rename_refuses_an_empty_name(client):
    registros = client.get("/api/records?page=1&per_page=100&sort_by=data").json()["records"]

    resposta = client.post(f"/api/records/{registros[0]['index']}/rename", data={"name": "   "})

    assert resposta.status_code in (400, 422)


def test_rename_refuses_to_overwrite_another_file(client, tmp_path):
    registros = client.get("/api/records?page=1&per_page=100&sort_by=data").json()["records"]
    alvo = next(r for r in registros if r["arquivo"] == "foto-a.png")

    resposta = client.post(f"/api/records/{alvo['index']}/rename", data={"name": "foto-b"})

    assert resposta.status_code == 409
    assert (tmp_path / "media" / "foto-a.png").is_file(), "não podia ter movido nada"


def test_rename_on_a_missing_record_is_a_404(client):
    assert client.post("/api/records/99999/rename", data={"name": "x"}).status_code == 404
