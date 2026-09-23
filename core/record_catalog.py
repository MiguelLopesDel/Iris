"""Compact, on-demand view of the searchable catalogue.

``IndexRecord`` is a useful interchange object: the web server, duplicate
review and scripts can all consume one without knowing where its fields came
from.  It is not, however, a good *resident* catalogue representation.  Even
with ``__slots__``, one Python object and its many pointers per row dominate
memory long before the vectors do.

``RecordCatalog`` keeps those fields in columns and materialises an
``IndexRecord`` only at the sequence seam.  Callers deliberately still see a
read-only sequence, so the storage change stays local to the search engine.
The engine itself can read the scalar columns directly on hot paths.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, overload

import numpy as np

from core.record_text import TEXT_COLUMNS, TextStore
from core.search_types import IndexRecord


@dataclass(frozen=True, slots=True)
class RecordColumns:
    """Aligned scalar columns for one searchable record per position."""

    arquivo: tuple[str, ...]
    caminho: tuple[str, ...]
    resolved_path: tuple[str | None, ...]
    relative_path: tuple[str | None, ...]
    content_hash: tuple[str, ...]
    file_size: np.ndarray
    file_mtime: np.ndarray
    library_id: np.ndarray
    storage_path: tuple[str | None, ...]
    source_path: tuple[str | None, ...]
    db_id: np.ndarray
    audio_fingerprint: tuple[str, ...]
    audio_embedding: tuple[np.ndarray | None, ...]
    perceptual_hash: tuple[str, ...]
    thumb_hash: tuple[str, ...]
    eager_text: tuple[tuple[str, ...] | None, ...]

    def __len__(self) -> int:
        return len(self.arquivo)


class RecordCatalog(Sequence[IndexRecord]):
    """Read-only sequence adapter over :class:`RecordColumns`.

    Indexing is the only materialisation point.  An ``IndexRecord`` therefore
    survives only for the request or loop iteration that needs it; no list of
    them is retained for the life of a large server process.
    """

    def __init__(
        self,
        columns: RecordColumns,
        *,
        image_matrix: np.ndarray,
        desc_matrix: np.ndarray | None,
        desc_present: np.ndarray | None,
        text_store: TextStore | None,
    ) -> None:
        size = len(columns)
        if image_matrix.shape[0] != size:
            raise ValueError("Image matrix and record columns must stay aligned.")
        if desc_matrix is not None and desc_matrix.shape[0] != size:
            raise ValueError("Description matrix and record columns must stay aligned.")
        if desc_present is not None and len(desc_present) != size:
            raise ValueError("Description presence and record columns must stay aligned.")
        self.columns = columns
        self._image_matrix = image_matrix
        self._desc_matrix = desc_matrix
        self._desc_present = desc_present
        self._text_store = text_store

    def __len__(self) -> int:
        return len(self.columns)

    @overload
    def __getitem__(self, index: int) -> IndexRecord: ...

    @overload
    def __getitem__(self, index: slice) -> list[IndexRecord]: ...

    def __getitem__(self, index: int | slice) -> IndexRecord | list[IndexRecord]:
        if isinstance(index, slice):
            return [self.record_at(position) for position in range(*index.indices(len(self)))]
        return self.record_at(index)

    def __iter__(self) -> Iterator[IndexRecord]:
        for index in range(len(self)):
            yield self.record_at(index)

    def record_at(self, index: int) -> IndexRecord:
        """Materialise one compatibility object from the compact columns."""
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError("record index out of range")

        eager_text = self.columns.eager_text[index]
        text_values: dict[str, str] = {}
        if eager_text is not None:
            text_values = dict(zip(TEXT_COLUMNS, eager_text, strict=True))
        has_desc = self._desc_present is None or bool(self._desc_present[index])
        return IndexRecord(
            index=index,
            arquivo=self.columns.arquivo[index],
            caminho=self.columns.caminho[index],
            resolved_path=self.columns.resolved_path[index],
            embedding=self._image_matrix[index],
            desc_embedding=self._desc_matrix[index] if has_desc and self._desc_matrix is not None else None,
            text_store=self._text_store,
            **text_values,
            relative_path=self.columns.relative_path[index],
            content_hash=self.columns.content_hash[index],
            file_size=_optional_int(self.columns.file_size[index]),
            file_mtime=_optional_float(self.columns.file_mtime[index]),
            library_id=_optional_int(self.columns.library_id[index]),
            storage_path=self.columns.storage_path[index],
            source_path=self.columns.source_path[index],
            db_id=int(self.columns.db_id[index]),
            audio_fingerprint=self.columns.audio_fingerprint[index],
            audio_embedding=self.columns.audio_embedding[index],
            perceptual_hash=self.columns.perceptual_hash[index],
            thumb_hash=self.columns.thumb_hash[index],
        )

    def db_id_at(self, index: int) -> int:
        return int(self.columns.db_id[index])

    def filename_at(self, index: int) -> str:
        return self.columns.arquivo[index]

    def resolved_path_at(self, index: int) -> str | None:
        return self.columns.resolved_path[index]


def _optional_int(value: np.integer[Any]) -> int | None:
    integer = int(value)
    return None if integer < 0 else integer


def _optional_float(value: np.floating[Any]) -> float | None:
    number = float(value)
    return None if np.isnan(number) else number
