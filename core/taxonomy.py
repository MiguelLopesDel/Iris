from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

import faiss
import numpy as np


@dataclass(frozen=True)
class TaxonomyLabel:
    field: str
    value: str
    prompts: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    threshold: float = 0.18


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
        ("wojak", "chudjak", "chud", "soyjak", "doomer"),
        0.18,
    ),
    TaxonomyLabel(
        "source_work",
        "chudjak parents visit room",
        (
            "chudjak parents visit bedroom meme template",
            "you don't understand I am only moderating meme",
            "parents visiting gamer room wojak meme",
        ),
        ("parents visit", "moderating", "server", "discord moderator"),
        0.19,
    ),
    TaxonomyLabel(
        "context",
        "discord moderation",
        (
            "discord moderator meme",
            "person moderating a discord server meme",
            "gamer room with discord logos",
        ),
        ("discord", "moderador", "moderação", "servidor"),
        0.18,
    ),
    TaxonomyLabel(
        "style",
        "youtube/social screenshot",
        (
            "mobile YouTube shorts screenshot meme",
            "social media screenshot meme",
            "phone screenshot with like share subscribe interface",
        ),
        ("youtube", "shorts", "subscribe", "dislike", "share"),
        0.18,
    ),
    TaxonomyLabel(
        "style",
        "anime/manga",
        (
            "anime meme screenshot",
            "manga panel meme",
            "anime character reaction meme",
        ),
        ("anime", "manga", "gojo", "sukuna", "itadori", "makima", "denji"),
        0.18,
    ),
    TaxonomyLabel(
        "style",
        "comic/cartoon",
        (
            "comic strip meme",
            "cartoon drawing meme",
            "simple illustrated character meme",
        ),
        ("quadrinho", "comic", "cartoon"),
        0.18,
    ),
    TaxonomyLabel(
        "context",
        "programming/linux",
        (
            "linux programming meme",
            "computer code screenshot meme",
            "programmer setup meme",
        ),
        ("linux", "programa", "codigo", "fork", "printf", "chatgpt"),
        0.18,
    ),
    TaxonomyLabel(
        "context",
        "music/playlist",
        (
            "music playlist screenshot",
            "YouTube Music recap screenshot",
            "top artists and top tracks screenshot",
        ),
        ("playlist", "music", "top tracks", "top artists", "youtube music"),
        0.18,
    ),
    TaxonomyLabel(
        "context",
        "history/politics",
        (
            "history politics meme",
            "political history screenshot meme",
            "empire war historical meme",
        ),
        ("historia", "imperio", "regime", "brasil", "paraguai", "politica"),
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
        ("kkkk", "shitpost", "absurdo"),
        0.18,
    ),
    TaxonomyLabel(
        "humor",
        "reaction",
        (
            "reaction meme",
            "character reacting meme",
            "angry disappointed reaction image",
        ),
        ("reaction", "reação", "vendo que"),
        0.18,
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
        alias_hit = any(alias.lower() in normalized_text for alias in label.aliases)
        if match.score >= label.threshold or alias_hit:
            if alias_hit and match.score < label.threshold:
                match = TaxonomyMatch(
                    field=match.field,
                    value=match.value,
                    score=max(match.score, label.threshold),
                    prompt=f"alias:{label.value}",
                )
            matches.append(match)

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
