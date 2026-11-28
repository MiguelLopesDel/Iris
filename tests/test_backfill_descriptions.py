"""scripts/backfill_descriptions.py: what counts as a caption when repairing.

The repair keeps the Florence-2 caption already stored after "Visual:" and
rebuilds the rest. Treating a description made only of tags as the caption
wrote "Visual: Tags: N/A, anime/manga" on about 2,000 items of a real library:
a fake caption that search and the AI worker's queue took for a real one, so
those items would never get theirs.
"""
from __future__ import annotations

import pytest

from scripts.backfill_descriptions import _limpar_tags, _separar_descricao


@pytest.mark.parametrize("descricao, esperado", [
    ("Tags: cat. Visual: a cat on a sofa", ("cat", "a cat on a sofa")),
    ("Tags: VQA><loc_0>. Visual: a dog in the park", ("VQA><loc_0>", "a dog in the park")),
    # Only tags: no caption at all.
    ("Tags: N/A, anime/manga", ("N/A, anime/manga", "")),
    ("Tags: N/A. Visual: N/A", ("N/A", "")),
    # The fake caption an earlier run wrote is not a caption either.
    ("Tags: N/A, history/politics. Visual: Tags: N/A, history/politics", ("N/A, history/politics", "")),
    ("", ("", "")),
    (None, ("", "")),
    # A plain caption from an older format is still a caption.
    ("a cat on a sofa", ("", "a cat on a sofa")),
])
def test_only_a_real_caption_is_kept(descricao, esperado):
    assert _separar_descricao(descricao) == esperado


@pytest.mark.parametrize("tags, esperado", [
    ("N/A, youtube/social screenshot", "youtube/social screenshot"),
    ("N/A", ""),
    ("cat, sofa", "cat, sofa"),
    ("VQA>What is this meme (reaction) photo, art)? List 5 keywords", ""),
    ("", ""),
])
def test_tags_lose_the_placeholder_and_the_prompt(tags, esperado):
    assert _limpar_tags(tags) == esperado
