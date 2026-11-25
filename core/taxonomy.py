from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

import faiss
import numpy as np


@dataclass(frozen=True)
class TaxonomyLabel:
    """A category assigned zero-shot from CLIP image/prompt similarity.

    ``threshold`` is the similarity that alone assigns it. ``aliases`` are
    whole words that, found in the item's text (OCR, captions), assign it from
    the lower ``alias_threshold`` -- text backs the picture up, it never stands
    in for it. Both were calibrated on a labelled sample: CLIP gives almost any
    image 0.15-0.30 against almost any sentence, so the old uniform 0.18 let
    nearly everything through, and an alias used to be a free pass matched as
    a substring ("server" tagged screenshots as a meme template).
    """

    field: str
    value: str
    prompts: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    threshold: float = 0.24
    alias_threshold: float = 0.20


@dataclass(frozen=True)
class TaxonomyMatch:
    field: str
    value: str
    score: float
    prompt: str


TAXONOMY_LABELS: tuple[TaxonomyLabel, ...] = (
    TaxonomyLabel(
        "style",
        "wojak/chudjak",
        (
            "wojak meme illustration",
            "chudjak meme character visited by parents in bedroom",
            "doomer wojak style meme",
            "soyjak and chudjak reaction meme drawing",
        ),
        ("wojak", "chudjak", "soyjak", "doomer"),
        0.26,
        0.20,
    ),
    TaxonomyLabel(
        "source_work",
        "chudjak parents visit room",
        (
            "chudjak parents visit bedroom meme template",
            "you don't understand I am only moderating meme",
            "parents visiting gamer room wojak meme",
        ),
        ("chudjak",),
        0.28,
        0.22,
    ),
    TaxonomyLabel(
        "context",
        "discord moderation",
        (
            "discord moderator meme",
            "person moderating a discord server meme",
            "gamer room with discord logos",
        ),
        ("moderador", "moderação", "moderator", "moderating"),
        0.26,
        0.20,
    ),
    TaxonomyLabel(
        "style",
        "youtube/social screenshot",
        (
            "mobile YouTube shorts screenshot meme",
            "social media screenshot meme",
            "phone screenshot with like share subscribe interface",
        ),
        ("youtube", "shorts", "inscrever-se", "subscribe", "curtidas", "respostas",
         "replies", "comentários", "comments"),
        0.205,
        0.17,
    ),
    TaxonomyLabel(
        "style",
        "anime/manga",
        (
            "anime meme screenshot",
            "manga panel meme",
            "anime character reaction meme",
        ),
        ("anime", "manga", "mangá", "gojo", "sukuna", "itadori", "makima", "denji"),
        0.20,
        0.17,
    ),
    TaxonomyLabel(
        "style",
        "comic/cartoon",
        (
            "comic strip meme",
            "cartoon drawing meme",
            "simple illustrated character meme",
        ),
        ("quadrinho", "tirinha", "comic", "cartoon"),
        0.19,
        0.17,
    ),
    TaxonomyLabel(
        "context",
        "programming/linux",
        (
            "linux programming meme",
            "computer code screenshot meme",
            "programmer setup meme",
        ),
        ("linux", "python", "java", "javascript", "public", "private", "void", "function",
         "import", "return", "class", "sudo", "bash", "printf", "println", "def", "const",
         "null", "terminal", "github", "código", "programação"),
        0.27,
        0.16,
    ),
    TaxonomyLabel(
        "context",
        "music/playlist",
        (
            "music playlist screenshot",
            "YouTube Music recap screenshot",
            "top artists and top tracks screenshot",
        ),
        ("playlist", "spotify", "youtube music", "top tracks", "top artists", "álbum"),
        0.25,
        0.20,
    ),
    TaxonomyLabel(
        "context",
        "history/politics",
        (
            "history politics meme",
            "political history screenshot meme",
            "empire war historical meme",
        ),
        ("história", "historia", "império", "imperio", "regime", "política", "politica",
         "presidente", "eleição", "eleitoral", "governo", "juiz", "deputado", "senado",
         "ditadura", "guerra"),
        0.24,
        0.18,
    ),
    TaxonomyLabel(
        "humor",
        "absurdist/shitpost",
        (
            "absurd shitpost meme",
            "surreal internet meme",
            "nonsense reaction meme",
        ),
        ("shitpost",),
        0.24,
        0.22,
    ),
    TaxonomyLabel(
        "humor",
        "reaction",
        (
            "reaction meme",
            "character reacting meme",
            "angry disappointed reaction image",
        ),
        (),
        0.24,
        0.24,
    ),
)


def normalize_embeddings(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    matrix = matrix.copy()
    faiss.normalize_L2(matrix)
    return matrix


def build_taxonomy_prompt_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for label in TAXONOMY_LABELS:
        for prompt in label.prompts:
            rows.append({"field": label.field, "value": label.value, "prompt": prompt})
    return rows


def _has_word(words: tuple[str, ...], text: str) -> bool:
    """Whether any of ``words`` occurs in ``text`` as a whole word or phrase."""
    return any(re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", text) for word in words)


def classify_embedding(
    image_embedding: np.ndarray,
    prompt_embeddings: np.ndarray,
    prompt_rows: list[dict[str, str]],
    *,
    text_content: str = "",
    max_per_field: int = 2,
) -> list[TaxonomyMatch]:
    image_embedding = normalize_embeddings(image_embedding)
    prompt_embeddings = normalize_embeddings(prompt_embeddings)
    scores = (image_embedding @ prompt_embeddings.T)[0]
    best_by_label: dict[tuple[str, str], TaxonomyMatch] = {}
    for idx, score in enumerate(scores.tolist()):
        row = prompt_rows[idx]
        key = (row["field"], row["value"])
        current = best_by_label.get(key)
        if current is None or score > current.score:
            best_by_label[key] = TaxonomyMatch(
                field=row["field"],
                value=row["value"],
                score=float(score),
                prompt=row["prompt"],
            )

    matches: list[TaxonomyMatch] = []
    normalized_text = text_content.lower()
    label_lookup = {(label.field, label.value): label for label in TAXONOMY_LABELS}
    for key, match in best_by_label.items():
        label = label_lookup[key]
        if match.score >= label.threshold:
            matches.append(match)
        elif match.score >= label.alias_threshold and _has_word(label.aliases, normalized_text):
            matches.append(TaxonomyMatch(
                field=match.field,
                value=match.value,
                score=label.threshold,
                prompt=f"alias:{label.value}",
            ))

    matches.sort(key=lambda item: item.score, reverse=True)
    field_counts: dict[str, int] = {}
    filtered: list[TaxonomyMatch] = []
    for match in matches:
        count = field_counts.get(match.field, 0)
        if count >= max_per_field:
            continue
        filtered.append(match)
        field_counts[match.field] = count + 1
    return filtered


def merge_taxonomy_into_profile(
    current_json: str | None,
    matches: list[TaxonomyMatch],
) -> dict[str, Any]:
    try:
        profile = json.loads(current_json or "{}")
        if not isinstance(profile, dict):
            profile = {}
    except json.JSONDecodeError:
        profile = {}
    profile["taxonomy_matches"] = [asdict(match) for match in matches]
    for match in matches:
        current = str(profile.get(match.field, "") or "")
        values = [part.strip() for part in current.split(",") if part.strip()]
        if match.value not in values:
            values.append(match.value)
        profile[match.field] = ", ".join(values)
    return profile


def values_for_field(matches: list[TaxonomyMatch], field: str, existing: str = "") -> str:
    values = [part.strip() for part in existing.split(",") if part.strip() and part != "unknown"]
    for match in matches:
        if match.field == field and match.value not in values:
            values.append(match.value)
    return ", ".join(values) if values else existing


TAXONOMY_FIELDS = ("style", "source_work", "context", "humor")
_TAXONOMY_VALUES = {label.value for label in TAXONOMY_LABELS}


def _without_taxonomy(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",")
            if part.strip() and part.strip() not in _TAXONOMY_VALUES]


def retag_columns(columns: dict[str, str], matches: list[TaxonomyMatch]) -> dict[str, str]:
    """An item's taxonomy columns with old taxonomy values replaced by ``matches``.

    Whatever did not come from the taxonomy (the visual heuristics' "vertical-
    phone", detector tags) is kept; only the taxonomy's own values are swapped,
    as the indexer would have written them under the current rules.
    """
    out: dict[str, str] = {}
    for field in TAXONOMY_FIELDS:
        values = _without_taxonomy(columns.get(field, ""))
        values += [m.value for m in matches if m.field == field and m.value not in values]
        out[field] = ", ".join(values) if values else "unknown"
    tags = _without_taxonomy(columns.get("tags", ""))
    tags += [m.value for m in matches
             if m.field in ("source_work", "context") and m.value not in tags]
    out["tags"] = ", ".join(tags)
    try:
        profile = json.loads(columns.get("visual_json") or "{}")
        if not isinstance(profile, dict):
            profile = {}
    except json.JSONDecodeError:
        profile = {}
    for field in TAXONOMY_FIELDS:
        if field in profile:
            profile[field] = ", ".join(_without_taxonomy(str(profile[field] or "")))
    out["visual_json"] = json.dumps(merge_taxonomy_into_profile(json.dumps(profile), matches),
                                    ensure_ascii=False)
    return out
