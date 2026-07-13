from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.media_metadata import (
    _gps_from_ifd,
    _json_safe,
    _parse_exif_datetime,
    _parse_iso6709,
    extract_full_metadata,
    extract_metadata,
)


def _write_jpeg(path: Path, ifd0: dict) -> None:
    """Write a tiny JPEG with IFD0 EXIF tags (Make/Model/Software/DateTime)."""
    exif = Image.Exif()
    for tag, value in ifd0.items():
        exif[tag] = value
    Image.new("RGB", (16, 16), (120, 120, 120)).save(path, exif=exif)


class ImageMetadataTests(unittest.TestCase):
    def test_reads_date_device_and_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.jpg"
            _write_jpeg(path, {
                271: "Apple",            # Make
                272: "iPhone 13",        # Model
                305: "WhatsApp",         # Software
                306: "2026:06:18 14:30:00",  # DateTime
            })
            meta = extract_metadata(path)
            self.assertEqual(meta["captured_at"], "2026-06-18T14:30:00")
            self.assertEqual(meta["device"], "Apple iPhone 13")
            self.assertEqual(meta["source_app"], "WhatsApp")

    def test_no_exif_falls_back_to_filename_app_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Screenshot_2026-06-18.png"
            Image.new("RGB", (8, 8), (0, 0, 0)).save(path)
            meta = extract_metadata(path)
            self.assertEqual(meta["source_app"], "Captura de tela")
            self.assertEqual(meta["captured_at"], "")

    def test_whatsapp_filename_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IMG-20230101-WA0001.jpg"
            Image.new("RGB", (8, 8), (10, 10, 10)).save(path)
            self.assertEqual(extract_metadata(path)["source_app"], "WhatsApp")

    def test_unknown_extension_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("hello")
            meta = extract_metadata(path)
            self.assertEqual(meta["source_app"], "")
            self.assertIsNone(meta["gps"])


class FullMetadataTests(unittest.TestCase):
    def test_image_full_metadata_has_dimensions_and_named_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.jpg"
            _write_jpeg(path, {271: "Apple", 272: "iPhone 13", 305: "WhatsApp"})
            full = extract_full_metadata(path)
            self.assertEqual(full["kind"], "image")
            self.assertEqual(full["format"], "JPEG")
            self.assertEqual(full["width"], 16)
            self.assertEqual(full["height"], 16)
            self.assertEqual(full["exif"]["Make"], "Apple")
            self.assertEqual(full["exif"]["Model"], "iPhone 13")
            json.dumps(full)  # must be JSON-serializable end to end

    def test_missing_or_unknown_file_yields_empty(self) -> None:
        self.assertEqual(extract_full_metadata("/nope/void.jpg"), {})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("hello")
            self.assertEqual(extract_full_metadata(path), {})

    def test_json_safe_sanitizes_exotic_values(self) -> None:
        self.assertEqual(_json_safe(b"abc\x00"), "abc")
        self.assertEqual(_json_safe(b"\x00\x01\x02"), "<3 bytes>")  # binary blob placeholder
        self.assertEqual(_json_safe((1, 2)), [1, 2])
        self.assertEqual(_json_safe(float("inf")), "inf")
        self.assertEqual(_json_safe("x" * 500), "x" * 200)

        class Rational:
            def __float__(self) -> float:
                return 0.5

        self.assertEqual(_json_safe(Rational()), 0.5)


class GpsAndHelperTests(unittest.TestCase):
    def test_gps_from_ifd_converts_dms_to_decimal(self) -> None:
        # PIL yields float-able IFDRational values; tuples of (deg, min, sec).
        gps = _gps_from_ifd({1: "N", 2: (38.0, 43.0, 0.0), 3: "W", 4: (9.0, 8.0, 0.0)})
        self.assertIsNotNone(gps)
        self.assertAlmostEqual(gps["lat"], 38.716667, places=4)
        self.assertAlmostEqual(gps["lon"], -9.133333, places=4)

    def test_gps_from_ifd_missing_fields(self) -> None:
        self.assertIsNone(_gps_from_ifd({1: "N"}))
        self.assertIsNone(_gps_from_ifd({}))

    def test_exif_datetime_parsing(self) -> None:
        self.assertEqual(_parse_exif_datetime("2026:06:18 14:30:00"), "2026-06-18T14:30:00")
        self.assertEqual(_parse_exif_datetime(""), "")
        self.assertEqual(_parse_exif_datetime("lixo"), "")

    def test_iso6709_parsing(self) -> None:
        gps = _parse_iso6709("+38.7197-009.1376+010.000/")
        self.assertAlmostEqual(gps["lat"], 38.7197, places=4)
        self.assertAlmostEqual(gps["lon"], -9.1376, places=4)
        self.assertIsNone(_parse_iso6709(""))
        self.assertIsNone(_parse_iso6709("garbage"))


if __name__ == "__main__":
    unittest.main()
