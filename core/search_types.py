"""Search data types and text utilities — dataclasses, normalization, query parsing."""
from __future__ import annotations

import string
import unicodedata
from dataclasses import dataclass
from typing import Any

import numpy as np

# ``normalize_text`` is called for every lexical-search candidate.  Asking
# ``unicodedata.category`` once per character made a cold query spend most of
# its time crossing the Python/C boundary.  ``str.translate`` performs the
# same removal in C.  Build the map from the interpreter's own Unicode table so
# its behaviour stays exactly aligned with the previous category-based rule.
_NORMALIZATION_TRANSLATION = {ord(char): None for char in string.punctuation}
_NORMALIZATION_TRANSLATION.update(
    {
        codepoint: None
        for codepoint in range(0x110000)
        if unicodedata.category(chr(codepoint)) == "Mn"
    }
)


@dataclass
class SearchOptions:
    top_k: int = 50
    threshold: float = 0.15
    balance: float = 0.5
    text_bonus: float = 1.0
    lexical_weight: float = 0.25
    translate: bool = True
    candidate_pool: int = 3000
    collection_ids: frozenset[int] = frozenset()
    concept_ids: frozenset[int] = frozenset()
    media_type: str = "all"
    excluded_db_ids: frozenset[int] = frozenset()


class IndexRecord:
    """One catalogued item, holding only what the hot path reads.

    ``__slots__`` rather than a dataclass: an instance with a ``__dict__``
    carries a hash table per item, which at tens of millions of items is not a
    detail. The nine text columns are not attributes at all -- they are
    properties that ask a :class:`~core.record_text.TextStore`, so the string
    exists while an item is being scored or serialised and not for the lifetime
    of the process.

    A record built without a store (a detached one, from a search result) keeps
    whatever text it was given, so nothing that constructs one by hand needs to
    know a store exists.
    """

    __slots__ = (
        "index",
        "arquivo",
        "caminho",
        "resolved_path",
        "embedding",
        "desc_embedding",
        "relative_path",
        "content_hash",
        "file_size",
        "file_mtime",
        "library_id",
        "storage_path",
        "source_path",
        "db_id",
        "audio_fingerprint",
        "audio_embedding",
        "perceptual_hash",
        "thumb_hash",
        "_text",
        "_normal",
        "_detail",
        "_text_store",
    )

    def __init__(
        self,
        *,
        index: int,
        arquivo: str,
        caminho: str,
        resolved_path: str | None,
        embedding: np.ndarray,
        desc_embedding: np.ndarray | None,
        texto_extraido: str | None = None,
        descricao_ia: str | None = None,
        tags: str | None = None,
        objects: str | None = None,
        style: str | None = None,
        source_work: str | None = None,
        humor: str | None = None,
        context: str | None = None,
        visual_json: str | None = None,
        text_store: Any | None = None,
        relative_path: str | None = None,
        content_hash: str = "",
        file_size: int | None = None,
        file_mtime: float | None = None,
        library_id: int | None = None,
        storage_path: str | None = None,
        source_path: str | None = None,
        db_id: int = 0,
        audio_fingerprint: str = "",
        audio_embedding: np.ndarray | None = None,
        perceptual_hash: str = "",
        thumb_hash: str = "",
    ):
        self.index = index
        self.arquivo = arquivo
        self.caminho = caminho
        self.resolved_path = resolved_path
        self.embedding = embedding
        self.desc_embedding = desc_embedding
        self.relative_path = relative_path
        self.content_hash = content_hash
        self.file_size = file_size
        self.file_mtime = file_mtime
        self.library_id = library_id
        self.storage_path = storage_path
        self.source_path = source_path
        self.db_id = db_id
        self.audio_fingerprint = audio_fingerprint
        self.audio_embedding = audio_embedding
        self.perceptual_hash = perceptual_hash
        self.thumb_hash = thumb_hash

        given = (
            texto_extraido,
            descricao_ia,
            tags,
            objects,
            style,
            source_work,
            humor,
            context,
        )
        self._text_store = text_store
        # Text passed in wins over the store, and a record with neither reads as
        # empty rather than as an error: absence of text is a normal state.
        lazy = text_store is not None
        self._text: tuple[str, ...] | None = (
            None if lazy and all(value is None for value in given)
            else tuple(value or "" for value in given)
        )
        self._detail: str | None = (
            None if lazy and visual_json is None else (visual_json or "")
        )
        self._normal: tuple[tuple[str, ...], str] | None = None

    def _text_values(self) -> tuple[str, ...]:
        values = self._text
        if values is None:
            values = self._text_store.fetch(self.db_id)
            # Not memoised on the record: holding it here would rebuild the
            # resident catalogue one search at a time. The store's bounded cache
            # is what makes the repeated reads within one request cheap.
        return values

    @property
    def texto_extraido(self) -> str:
        return self._text_values()[0]

    @property
    def descricao_ia(self) -> str:
        return self._text_values()[1]

    @property
    def tags(self) -> str:
        return self._text_values()[2]

    @property
    def objects(self) -> str:
        return self._text_values()[3]

    @property
    def style(self) -> str:
        return self._text_values()[4]

    @property
    def source_work(self) -> str:
        return self._text_values()[5]

    @property
    def humor(self) -> str:
        return self._text_values()[6]

    @property
    def context(self) -> str:
        return self._text_values()[7]

    @property
    def visual_json(self) -> str:
        """Read on its own: nothing ranks by it, and it is half the text bytes."""
        if self._detail is None:
            return self._text_store.fetch_detail(self.db_id)
        return self._detail

    def normalized_text(self) -> tuple[tuple[str, ...], str]:
        """The ranking columns folded for matching, and all of them joined.

        Served by the store when there is one, so the folding is shared between
        the records of a catalogue instead of repeated per query. A detached
        record folds its own text and keeps it: it lives for one response.
        """
        if self._text_store is not None and self._text is None:
            return self._text_store.fetch_normalized(self.db_id)
        if self._normal is None:
            from core.record_text import normalize_fields

            self._normal = normalize_fields(self._text_values())
        return self._normal

    def text_fields(self) -> tuple[str, ...]:
        """Every text column at once, for callers that need more than one."""
        return self._text_values()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"IndexRecord(index={self.index}, db_id={self.db_id}, arquivo={self.arquivo!r})"


@dataclass(frozen=True)
class SearchResult:
    score: float
    index: int
    arquivo: str
    caminho: str
    resolved_path: str | None
    texto_extraido: str
    descricao_ia: str
    tags: str
    embedding: np.ndarray
    score_details: dict[str, float | str]


STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "da", "de", "do", "dos", "das",
    "e", "em", "for", "in", "is", "na", "no", "of", "on", "or",
    "para", "the", "to", "um", "uma", "with",
}


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFD", text.lower()).translate(_NORMALIZATION_TRANSLATION).strip()


def parse_query_terms(query: str) -> tuple[str, list[str]]:
    positive: list[str] = []
    negative: list[str] = []
    for word in query.split():
        if word.startswith("-") and len(word) > 1:
            negative.append(normalize_text(word[1:]))
        else:
            positive.append(word)
    return " ".join(positive).strip(), [term for term in negative if term]
