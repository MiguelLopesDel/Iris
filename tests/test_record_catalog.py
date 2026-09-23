from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from core.record_catalog import RecordCatalog, RecordColumns


def _columns() -> RecordColumns:
    return RecordColumns(
        arquivo=("one.jpg", "two.mp4"),
        caminho=("one.jpg", "two.mp4"),
        resolved_path=("/media/one.jpg", "/media/two.mp4"),
        relative_path=("one.jpg", "two.mp4"),
        content_hash=("a", "b"),
        file_size=np.asarray([12, -1], dtype=np.int64),
        file_mtime=np.asarray([1.5, np.nan], dtype=np.float64),
        library_id=np.asarray([7, -1], dtype=np.int64),
        storage_path=("one.jpg", None),
        source_path=(None, None),
        db_id=np.asarray([101, 102], dtype=np.int64),
        audio_fingerprint=("", "fingerprint"),
        audio_embedding=(None, np.asarray([0.25, 0.75], dtype=np.float32)),
        perceptual_hash=("p1", "p2"),
        thumb_hash=("t1", "t2"),
        eager_text=(("ocr", "description", "tags", "", "", "", "", "", "{}"), None),
    )


class RecordCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.images = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        self.descriptions = np.asarray([[0.5, 0.5], [0.0, 0.0]], dtype=np.float32)
        self.catalog = RecordCatalog(
            _columns(),
            image_matrix=self.images,
            desc_matrix=self.descriptions,
            desc_present=np.asarray([True, False]),
            text_store=None,
        )

    def test_materialises_compatibility_records_only_at_the_sequence_seam(self) -> None:
        first = self.catalog[0]
        same_position_again = self.catalog[0]

        self.assertIsNot(first, same_position_again)
        self.assertEqual(first.index, 0)
        self.assertEqual(first.db_id, 101)
        self.assertEqual(first.tags, "tags")
        self.assertIs(first.embedding.base, self.images)
        self.assertIs(first.desc_embedding.base, self.descriptions)

    def test_columns_keep_optional_values_and_vector_alignment(self) -> None:
        second = self.catalog[-1]

        self.assertEqual(second.index, 1)
        self.assertEqual(second.file_size, None)
        self.assertEqual(second.file_mtime, None)
        self.assertEqual(second.library_id, None)
        self.assertIsNone(second.desc_embedding)
        self.assertTrue(np.array_equal(second.embedding, self.images[1]))
        self.assertEqual(self.catalog.db_id_at(1), 102)
        self.assertEqual(self.catalog.filename_at(1), "two.mp4")

    def test_slice_remains_a_short_lived_list_for_legacy_callers(self) -> None:
        records = self.catalog[:]

        self.assertEqual([record.arquivo for record in records], ["one.jpg", "two.mp4"])
        self.assertIsInstance(records, list)

    def test_audio_index_reads_compact_audio_column_without_materialising_records(self) -> None:
        from core.vector_store import VectorStore

        class TrackingCatalog(RecordCatalog):
            materialisations = 0

            def record_at(self, index: int):
                self.materialisations += 1
                return super().record_at(index)

        catalog = TrackingCatalog(
            _columns(),
            image_matrix=self.images,
            desc_matrix=self.descriptions,
            desc_present=np.asarray([True, False]),
            text_store=None,
        )
        store = VectorStore(Path("/tmp") / self._testMethodName)  # no sidecar exists at this path

        matrix, indices = store.build_audio_index(catalog)

        self.assertEqual(indices, [1])
        self.assertIsNotNone(matrix)
        self.assertAlmostEqual(float(np.linalg.norm(matrix[0])), 1.0, places=6)
        self.assertEqual(catalog.materialisations, 0)


if __name__ == "__main__":
    unittest.main()
