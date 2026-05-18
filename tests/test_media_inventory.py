from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.media_inventory import inventory_media, read_manifest, sample_media, write_manifest


class MediaInventoryTests(unittest.TestCase):
    def test_sample_manifest_is_deterministic_and_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "media"
            media.mkdir()
            for name, size in [
                ("a.jpg", (64, 64)),
                ("b.jpg", (128, 64)),
                ("c.png", (64, 128)),
                ("d.webp", (90, 90)),
            ]:
                Image.new("RGB", size, "white").save(media / name)

            items = inventory_media(media)
            first = sample_media(items, sample_size=3, seed=7)
            second = sample_media(items, sample_size=3, seed=7)
            self.assertEqual([item.relative_path for item in first], [item.relative_path for item in second])

            manifest = root / "sample.json"
            write_manifest(manifest, media, first, seed=7, sample_size=3)
            loaded_media, loaded = read_manifest(manifest)

            self.assertEqual(loaded_media.resolve(), media.resolve())
            self.assertEqual(len(loaded), 3)
            self.assertEqual(len(list(media.iterdir())), 4)


if __name__ == "__main__":
    unittest.main()
