"""Thumbnails are built by the request that displays them, not by metadata.

Opening a collection used to generate every missing thumbnail inline, one at a
time, before the JSON response was sent. Measured on the real library that is
about 74 ms per image, so a 125-item collection stalled for roughly 9 seconds.
These tests pin the behaviour that removed the stall.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import server
from core.search_types import IndexRecord


def _record(tmp_path: Path, index: int = 0) -> IndexRecord:
    source = tmp_path / "photo.jpg"
    Image.new("RGB", (900, 600), (40, 90, 160)).save(source)
    return _index_record(index, index + 1, "photo.jpg", str(source))


def _index_record(index: int, db_id: int, arquivo: str, caminho: str) -> IndexRecord:
    return IndexRecord(
        index=index,
        db_id=db_id,
        arquivo=arquivo,
        caminho=caminho,
        texto_extraido="",
        descricao_ia="",
        tags="",
        embedding=None,
        desc_embedding=None,
        resolved_path=caminho,
    )


@pytest.fixture
def thumb_dir(tmp_path, monkeypatch):
    destination = tmp_path / "thumbnails"
    destination.mkdir()
    monkeypatch.setattr(server, "_thumbnail_dir", lambda: destination)
    return destination


def test_building_the_url_does_not_decode_the_image(tmp_path, thumb_dir):
    """The metadata path must not touch pixels: that was the whole stall."""
    record = _record(tmp_path)

    url = server._thumbnail_url(record)

    assert url.startswith("/thumbs/0/")
    assert list(thumb_dir.iterdir()) == [], "a miniatura foi gerada na montagem do JSON"


def test_a_missing_thumbnail_is_generated_when_it_is_requested(tmp_path, thumb_dir):
    record = _record(tmp_path)
    url = server._thumbnail_url(record)
    server._backend = None
    backend = type("Backend", (), {"get_record": staticmethod(lambda idx: record)})()

    import server as module

    module._backend = backend
    try:
        response = _get(url)
    finally:
        module._backend = None

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert len(list(thumb_dir.iterdir())) == 1


def test_a_stale_index_is_refused_instead_of_serving_another_photo(tmp_path, thumb_dir):
    """The key comes from the source bytes, so it catches a shifted index.

    Record indexes are positional: reindexing moves them. Without this check a
    bookmarked URL would quietly return whatever photo now sits at that slot.
    """
    record = _record(tmp_path)
    other = tmp_path / "other.jpg"
    Image.new("RGB", (100, 100), (10, 10, 10)).save(other)
    outra = _index_record(0, 99, "other.jpg", str(other))
    url = server._thumbnail_url(record)

    import server as module

    module._backend = type("Backend", (), {"get_record": staticmethod(lambda idx: outra)})()
    try:
        response = _get(url)
    finally:
        module._backend = None

    assert response.status_code == 404
    assert list(thumb_dir.iterdir()) == []


def test_generation_is_atomic_so_a_reader_never_sees_a_partial_file(tmp_path, thumb_dir):
    """Concurrent requests for the same missing thumbnail must not tear.

    On-demand generation makes this race ordinary: a cold collection fires many
    parallel requests. Writing straight to the destination would let a reader
    open a half-written JPEG.
    """
    source = tmp_path / "photo.jpg"
    Image.new("RGB", (900, 600), (200, 40, 40)).save(source)
    destination = thumb_dir / "abc.jpg"
    visto: list[bytes] = []

    original = server._generate_image_thumbnail

    def espiar(image_path, thumb_path):
        original(image_path, thumb_path)
        # Enquanto o arquivo temporário existe, o destino ainda não pode existir.
        visto.append(b"partial" if destination.exists() else b"clean")

    server._generate_image_thumbnail = espiar
    try:
        server._generate_thumbnail(str(source), destination)
    finally:
        server._generate_image_thumbnail = original

    assert visto == [b"clean"], "o destino apareceu antes de a imagem estar completa"
    assert destination.is_file()
    assert Image.open(destination).size[0] <= 300
    assert not any(p.name.startswith(".") for p in thumb_dir.iterdir()), "sobrou temporário"


def _get(path: str):
    import asyncio

    import httpx

    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(run())
