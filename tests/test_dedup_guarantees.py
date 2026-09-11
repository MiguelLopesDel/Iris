"""Observed recall of the duplicate detector, pinned per transformation.

A single recall figure is the wrong contract. Resizes and recompressions
dominate any library, so a detector that handles those and nothing else still
averages beautifully while missing every crop. These tests record the observed
rate one transformation at a time, and separate the two kinds of error, which
are not equally bad: a miss leaves a second copy on disk, a wrong match puts a
unique photo in front of a delete button.

Everything here is an *observed* rate, never a guarantee, and the word is
avoided rather than footnoted: zero failures in 120 samples puts the one-sided
95% lower bound on recall near 97.5%, not at 100%, and someone skimming in six
months will quote the heading, not the caveat.

Exactly one claim in this pipeline is a guarantee rather than a measurement:
the candidate step in ``core.duplicates``, where the pigeonhole argument rules
out false negatives within the radius. That one is a proof.

Measured with ``scripts/evaluate_dedup.py`` over 120 real photos.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageEnhance

from core.indexer import _DEDUP_PHASH_THRESHOLD, _compute_phash, _is_low_detail


def _photo(seed: int = 7, size: tuple[int, int] = (512, 384)) -> Image.Image:
    """A synthetic image with enough structure to behave like a photograph."""
    import numpy as np

    rng = np.random.default_rng(seed)
    columns = np.linspace(0, 255, size[0], dtype=np.float32)
    rows = np.linspace(0, 180, size[1], dtype=np.float32)
    field = rows[:, None] * 0.4 + columns[None, :] * 0.6
    field += rng.normal(0, 18, size[::-1])
    blocks = np.zeros(size[::-1], dtype=np.float32)
    blocks[size[1] // 4 : size[1] // 2, size[0] // 4 : size[0] // 2] = 90
    blocks[size[1] // 2 :, : size[0] // 3] = -60
    pixels = np.clip(field + blocks, 0, 255).astype("uint8")
    return Image.merge(
        "RGB",
        [Image.fromarray(pixels), Image.fromarray(pixels // 2), Image.fromarray(255 - pixels)],
    )


def _distance(left: Image.Image, right: Image.Image) -> int:
    first, second = _compute_phash(left), _compute_phash(right)
    assert first and second, "o caminho de produção não gerou hash"
    return bin(int(first, 16) ^ int(second, 16)).count("1")


def _reencode(image: Image.Image, image_format: str, **options) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **options)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


# ── Observed 100% over the sample, except where a docstring says otherwise ───


@pytest.mark.parametrize("factor", [0.25, 0.5, 2.0])
def test_a_resized_copy_is_still_recognised(factor: float):
    photo = _photo()
    resized = photo.resize(
        (int(photo.width * factor), int(photo.height * factor)), Image.LANCZOS
    )

    assert _distance(photo, resized) <= _DEDUP_PHASH_THRESHOLD


@pytest.mark.parametrize("quality", [90, 70, 40])
def test_a_recompressed_copy_is_still_recognised(quality: int):
    photo = _photo()

    assert _distance(photo, _reencode(photo, "JPEG", quality=quality)) <= _DEDUP_PHASH_THRESHOLD


@pytest.mark.parametrize("image_format,options", [("PNG", {}), ("WEBP", {"quality": 80})])
def test_a_re_encoded_copy_survives_a_format_change(image_format: str, options: dict):
    photo = _photo()

    assert _distance(photo, _reencode(photo, image_format, **options)) <= _DEDUP_PHASH_THRESHOLD


@pytest.mark.parametrize("amount", [0.85, 1.15])
def test_a_brightness_change_does_not_break_recognition(amount: float):
    photo = _photo()

    assert (
        _distance(photo, ImageEnhance.Brightness(photo).enhance(amount))
        <= _DEDUP_PHASH_THRESHOLD
    )


def test_a_grayscale_copy_is_still_recognised():
    photo = _photo()

    assert _distance(photo, photo.convert("L").convert("RGB")) <= _DEDUP_PHASH_THRESHOLD


def test_a_watermark_does_not_break_recognition():
    """99.6% over 250 real photos, which is a rate and not a promise.

    The misses are all small images: below 600px the rate drops to 94%, because
    a fixed-size overlay covers a much larger share of the frame. Stated here
    rather than implied, since the synthetic photo below is comfortably large
    and would otherwise make this look unconditional.
    """
    photo = _photo()
    marked = photo.copy()
    draw = ImageDraw.Draw(marked)
    for x in range(0, marked.width, max(marked.width // 4, 1)):
        draw.text((x, marked.height // 2), "SAMPLE", fill=(255, 255, 255))

    assert _distance(photo, marked) <= _DEDUP_PHASH_THRESHOLD


def test_an_orientation_tag_alone_does_not_change_the_hash():
    """Cameras store upright pixels plus a tag; the importer hashes the pixels."""
    photo = _photo()
    exif = photo.getexif()
    exif[274] = 6

    buffer = io.BytesIO()
    photo.save(buffer, format="JPEG", quality=95, exif=exif)
    buffer.seek(0)

    assert _distance(photo, Image.open(buffer).convert("RGB")) <= _DEDUP_PHASH_THRESHOLD


# ── Outside the observed envelope: limits recorded so they are not a surprise ─


def test_cropping_is_outside_the_observed_envelope():
    """Documented limit, not a bug to fix by loosening the threshold.

    A frequency hash describes the whole frame, so removing part of it moves the
    hash far. Measured over 120 real photos: 34% of 5% crops are still caught,
    3% of 10% crops, and none at 20%. Only the last is asserted, because a test
    that fixes a coin flip on one image is a test that fails for no reason.
    Raising the threshold far enough to catch crops would start matching
    unrelated images, which is the error that costs a photograph.
    """
    photo = _photo()
    dx, dy = int(photo.width * 0.20), int(photo.height * 0.20)
    cropped = photo.crop((dx, dy, photo.width - dx, photo.height - dy))

    assert _distance(photo, cropped) > _DEDUP_PHASH_THRESHOLD


def test_rotated_pixels_are_outside_the_observed_envelope():
    """Measured 0% recall. A rotated frame is a different image to this hash."""
    photo = _photo()

    assert _distance(photo, photo.rotate(90, expand=True)) > _DEDUP_PHASH_THRESHOLD


# ── The error that must not happen ───────────────────────────────────────────


@pytest.mark.parametrize(
    "left,right",
    [
        ((0, 0, 0), (255, 255, 255)),
        ((200, 30, 30), (30, 30, 200)),
        ((0, 0, 0), (2, 2, 2)),
        ((128, 128, 128), (200, 30, 30)),
    ],
)
def test_solid_colours_are_never_offered_as_duplicates_of_each_other(left, right):
    """Measured: every solid colour hashed within one bit of every other.

    Solid red and solid blue were zero bits apart, and so were black and white,
    because a perceptual hash describes how brightness varies and a solid colour
    has no variation. Blank screenshots and exported placeholders are common, so
    this put unrelated images in front of a delete button as a matter of routine.
    """
    first = Image.new("RGB", (256, 256), left)
    second = Image.new("RGB", (256, 256), right)

    assert _compute_phash(first) is None
    assert _compute_phash(second) is None


def test_a_real_photograph_still_gets_a_hash():
    """The uniformity guard must not swallow ordinary, low-contrast photos.

    The least detailed of 400 real photos scored 6.5 against a cut-off of 3.0.
    """
    photo = _photo()

    assert not _is_low_detail(photo)
    assert _compute_phash(photo) is not None


def test_a_nearly_black_frame_with_only_compression_noise_is_still_refused():
    import numpy as np

    rng = np.random.default_rng(3)
    noisy = Image.fromarray(
        np.clip(rng.normal(8, 1.5, (256, 256)), 0, 255).astype("uint8")
    ).convert("RGB")

    assert _compute_phash(noisy) is None


def test_embedding_similarity_no_longer_holds_an_import_back():
    """Resemblance must not keep a file out of the library.

    The grouping stopped treating CLIP as identity, but the import gate kept
    quarantining on it, which is the same mistake in a different place: a file
    was held back on the strength of looking like something else. The CLIP paper
    reports this from its own attempt to use the space as a duplicate detector --
    different objects described alike score near-perfectly, while some real
    near-duplicates score lower than expected.
    """
    from core.indexer import _EMBEDDING_QUARANTINE

    assert _EMBEDDING_QUARANTINE is False


def test_identity_signals_still_hold_an_import_back():
    """What was removed is the resemblance gate, not the duplicate gate."""
    import inspect

    from core import indexer

    source = inspect.getsource(indexer.process_images)

    assert 'detection="exact_hash"' in source
    assert 'detection="perceptual"' in source
