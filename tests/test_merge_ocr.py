"""Two OCR readings kept together, so search finds what either engine read."""
from __future__ import annotations

from core.indexer import merge_ocr


def test_a_reading_that_adds_words_is_kept_after_the_first():
    easy = "Bondia Que a paz do Senhor invada a sua manhã e 0 seU dia Deus te aBençoe"
    florence = "Bom dia\nQue a paz\ndo Senhor invada a sua manhã e o seu dia.\nDeus te abençoe"

    merged = merge_ocr(easy, florence)

    assert merged.startswith(easy)
    assert "Bom dia Que a paz do Senhor invada a sua manhã e o seu dia. Deus te abençoe" in merged


def test_a_reading_with_nothing_new_is_not_repeated():
    easy = "EU NÃO SOU RACISTA."

    assert merge_ocr(easy, "eu não sou racista") == easy


def test_a_reading_that_only_drops_accents_adds_nothing():
    # Search already ignores accents, so "NAO" is the same word as "NÃO".
    assert merge_ocr("EU NÃO SOU RACISTA.", "EU NAO SOU\nRACISTA.") == "EU NÃO SOU RACISTA."


def test_an_empty_second_reading_changes_nothing():
    assert merge_ocr("texto", "") == "texto"
    assert merge_ocr("texto", "   \n ") == "texto"


def test_single_letters_alone_do_not_count_as_new_words():
    # Florence-2 often adds a lone "X" for a close button in screenshots.
    assert merge_ocr("Aviso não teremos aula", "X\nAviso\nnão teremos aula") == "Aviso não teremos aula"
