"""Testes do placeholder inline servido junto com o JSON do registro.

O formato é contrato com o cliente Android: ele deriva o lado do quadrado a
partir do tamanho em bytes, então mudar GRID_SIDE não pode quebrar decodificação.
"""

from __future__ import annotations

import base64
import math

from PIL import Image

from core.thumb_hash import GRID_SIDE, encode_thumb_hash


def _decode(encoded: str) -> tuple[int, bytes]:
    """Decodifica como o cliente faz: lado derivado do tamanho, não combinado."""
    raw = base64.b64decode(encoded)
    side = int(math.isqrt(len(raw) // 3))
    return side, raw


def test_encodes_square_rgb_grid():
    image = Image.new("RGB", (640, 480), (255, 0, 0))

    side, raw = _decode(encode_thumb_hash(image))

    assert side == GRID_SIDE
    assert len(raw) == GRID_SIDE * GRID_SIDE * 3


def test_preserves_dominant_colour():
    image = Image.new("RGB", (200, 200), (12, 200, 60))

    _side, raw = _decode(encode_thumb_hash(image))

    # Um preenchimento sólido tem que sobreviver ao downscale em todo pixel.
    assert raw[0:3] == bytes((12, 200, 60))
    assert raw[-3:] == bytes((12, 200, 60))


def test_distinguishes_different_images():
    dark = encode_thumb_hash(Image.new("RGB", (100, 100), (0, 0, 0)))
    light = encode_thumb_hash(Image.new("RGB", (100, 100), (255, 255, 255)))

    assert dark != light


def test_stays_small_enough_to_inline():
    encoded = encode_thumb_hash(Image.new("RGB", (4000, 3000), (30, 40, 50)))

    # Vai junto em cada registro de uma página de 24 — precisa ser desprezível.
    assert len(encoded) <= 256


def test_converts_non_rgb_modes():
    grayscale = Image.new("L", (50, 50), 128)

    side, raw = _decode(encode_thumb_hash(grayscale))

    assert side == GRID_SIDE
    assert raw[0:3] == bytes((128, 128, 128))


def test_missing_image_degrades_to_empty_string():
    # Um preview ausente nunca pode derrubar uma importação.
    assert encode_thumb_hash(None) == ""


def test_broken_image_degrades_to_empty_string():
    class Unreadable:
        def convert(self, _mode):
            raise OSError("imagem truncada")

    assert encode_thumb_hash(Unreadable()) == ""
