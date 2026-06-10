"""Search data types and text utilities — dataclasses, normalization, query parsing."""
from __future__ import annotations

import string
import unicodedata
from dataclasses import dataclass

import numpy as np


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


@dataclass(frozen=True)
class IndexRecord:
    index: int
    arquivo: str
    caminho: str
    resolved_path: str | None
    texto_extraido: str
    descricao_ia: str
    tags: str
    embedding: np.ndarray
    desc_embedding: np.ndarray | None
    relative_path: str | None = None
    visual_json: str = ""
    objects: str = ""
    style: str = ""
    source_work: str = ""
    humor: str = ""
    context: str = ""
    content_hash: str = ""
    file_size: int | None = None
    file_mtime: float | None = None
    library_id: int | None = None
    storage_path: str | None = None
    source_path: str | None = None
    db_id: int = 0
    audio_fingerprint: str = ""
    audio_embedding: np.ndarray | None = None
    perceptual_hash: str = ""


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
    text = text.lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return text.translate(str.maketrans("", "", string.punctuation)).strip()


def parse_query_terms(query: str) -> tuple[str, list[str]]:
    positive: list[str] = []
    negative: list[str] = []
    for word in query.split():
        if word.startswith("-") and len(word) > 1:
            negative.append(normalize_text(word[1:]))
        else:
            positive.append(word)
    return " ".join(positive).strip(), [term for term in negative if term]
