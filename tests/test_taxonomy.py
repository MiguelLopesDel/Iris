"""Zero-shot taxonomy: calibrated thresholds, and text that backs a picture up."""
from __future__ import annotations

import numpy as np

from core.taxonomy import TAXONOMY_LABELS, build_taxonomy_prompt_rows, classify_embedding

ROWS = build_taxonomy_prompt_rows()
LABEL = next(label for label in TAXONOMY_LABELS if label.value == "programming/linux")


def _classify(score_for_label: float, text: str = "") -> set[str]:
    """An image whose similarity is ``score_for_label`` to the label's prompts, ~0 to the rest."""
    dim = len(ROWS) + 1
    prompts = np.eye(len(ROWS), dim, dtype=np.float32)
    image = np.zeros((1, dim), dtype=np.float32)
    first = next(k for k, row in enumerate(ROWS) if row["value"] == LABEL.value)
    image[0, first] = score_for_label
    image[0, -1] = np.sqrt(max(0.0, 1 - score_for_label ** 2))  # keep it a unit vector
    return {m.value for m in classify_embedding(image, prompts, ROWS, text_content=text)}


def test_a_clear_match_is_assigned_from_the_picture_alone():
    assert LABEL.value in _classify(LABEL.threshold + 0.01)


def test_a_weak_match_without_supporting_text_is_not_assigned():
    # The old uniform 0.18 let almost every picture through.
    assert LABEL.value not in _classify(LABEL.threshold - 0.02)


def test_a_whole_word_in_the_text_backs_up_a_weaker_match():
    assert LABEL.value in _classify(LABEL.alias_threshold + 0.01, "public static void main")


def test_text_alone_does_not_assign_a_category():
    assert LABEL.value not in _classify(LABEL.alias_threshold - 0.05, "public static void main")


def test_a_word_inside_another_word_is_not_a_match():
    # "class" inside "classificados" must not tag a picture as code.
    assert LABEL.value not in _classify(LABEL.alias_threshold + 0.01, "classificados do jornal")


def test_every_label_is_stricter_with_the_picture_alone_than_with_text():
    assert all(label.alias_threshold <= label.threshold for label in TAXONOMY_LABELS)


def test_retagging_swaps_only_the_taxonomy_values():
    import json

    from core.taxonomy import TaxonomyMatch, retag_columns

    columns = {
        "style": "vertical-phone, youtube/social screenshot, anime/manga",
        "source_work": "chudjak parents visit room",
        "context": "discord moderation, history/politics",
        "humor": "reaction",
        "tags": "N/A, chudjak parents visit room, discord moderation",
        "visual_json": json.dumps({"style": "vertical-phone, anime/manga", "ocr_translated": "x"}),
    }
    matches = [TaxonomyMatch("style", "youtube/social screenshot", 0.3, "p")]

    out = retag_columns(columns, matches)

    # The visual heuristics' "vertical-phone" and the detector's "N/A" stay.
    assert out["style"] == "vertical-phone, youtube/social screenshot"
    assert out["source_work"] == "unknown"
    assert out["context"] == "unknown"
    assert out["humor"] == "unknown"
    assert out["tags"] == "N/A"
    profile = json.loads(out["visual_json"])
    assert profile["style"] == "vertical-phone, youtube/social screenshot"
    assert profile["ocr_translated"] == "x"
