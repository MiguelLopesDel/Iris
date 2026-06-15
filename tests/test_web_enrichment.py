from __future__ import annotations

import sqlite3

from core.concepts import create_concept_tables
from core.web_enrichment import (
    EnrichmentSuggestion,
    HeuristicDistiller,
    PlaywrightLensProvider,
    SerpApiLensProvider,
    WebSource,
    apply_suggestion,
    build_reverse_image_provider,
    count_cached_ids,
    create_web_enrichment_tables,
    find_existing_suggestion,
    insert_suggestion,
    list_suggestions,
    normalize_serpapi_sources,
    parse_lens_results,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT,
            caminho TEXT,
            tags TEXT DEFAULT '',
            descricao_ia TEXT DEFAULT '',
            style TEXT DEFAULT '',
            source_work TEXT DEFAULT '',
            context TEXT DEFAULT ''
        )
        """
    )
    create_concept_tables(conn)
    create_web_enrichment_tables(conn)
    conn.execute(
        "INSERT INTO memes (id, arquivo, caminho, tags, descricao_ia) VALUES (1, 'x.jpg', '/x.jpg', 'old', '')"
    )
    conn.commit()
    return conn


def test_normalize_serpapi_sources_dedupes_visual_matches() -> None:
    payload = {
        "visual_matches": [
            {
                "title": "Doomer Wojak Meme",
                "link": "https://example.com/a",
                "source": "https://knowyourmeme.com/memes/doomer",
            },
            {
                "title": "Doomer Wojak Meme",
                "link": "https://example.com/a",
                "source": "https://knowyourmeme.com/memes/doomer",
            },
        ]
    }

    sources = normalize_serpapi_sources(payload)

    assert len(sources) == 1
    assert sources[0].match_type == "visual_matches"
    assert sources[0].domain == "knowyourmeme.com"


def test_heuristic_distiller_extracts_meme_archetype_and_style() -> None:
    suggestion = HeuristicDistiller().distill(
        [
            WebSource(
                title="Doomer Wojak Meme - Know Your Meme",
                url="https://example.com",
                source_url="https://knowyourmeme.com/memes/doomer",
                domain="knowyourmeme.com",
            )
        ]
    )

    assert suggestion.meme_archetype == "doomer"
    assert suggestion.style == "wojak"
    assert "Doomer" in suggestion.character
    assert suggestion.confidence > 0


def test_apply_suggestion_updates_metadata_and_creates_concepts() -> None:
    conn = _make_conn()
    suggestion_id = insert_suggestion(
        conn,
        "job1",
        1,
        EnrichmentSuggestion(
            provider="test",
            character="Doomer",
            source_work="Wojak",
            style="wojak",
            meme_archetype="doomer",
            context="reaction meme",
            tags="doomer, wojak",
            summary="Possivel Doomer Wojak.",
            confidence=0.8,
            sources=(WebSource(title="Doomer Wojak", url="https://example.com"),),
        ),
    )

    result = apply_suggestion(conn, suggestion_id, [])

    assert result["ok"] is True
    row = conn.execute("SELECT * FROM memes WHERE id = 1").fetchone()
    assert row["style"] == "wojak"
    assert row["source_work"] == "Wojak"
    assert "doomer" in row["tags"]
    assert "Possivel Doomer Wojak" in row["descricao_ia"]
    concepts = conn.execute("SELECT name FROM concepts ORDER BY name").fetchall()
    assert [row["name"] for row in concepts] == ["Doomer", "Wojak"]
    media = conn.execute("SELECT COUNT(*) FROM concept_media WHERE meme_id = 1").fetchone()[0]
    assert media == 2
    assert list_suggestions(conn, "pending") == []


def test_parse_lens_results_dedupes_and_skips_empty() -> None:
    sources = parse_lens_results(
        [
            {"title": "Gojo Satoru", "url": "https://jujutsu-kaisen.fandom.com/wiki/Gojo"},
            {"title": "Gojo Satoru", "url": "https://jujutsu-kaisen.fandom.com/wiki/Gojo"},
            {"title": "", "url": ""},
            {"title": "Discussion", "url": "https://reddit.com/r/x"},
        ]
    )

    assert len(sources) == 2
    assert sources[0].domain == "jujutsu-kaisen.fandom.com"
    assert sources[0].match_type == "lens_visual_match"


def test_playwright_provider_uses_injected_scraper() -> None:
    captured: list[str] = []

    def fake_scraper(path: str) -> list[dict[str, str]]:
        captured.append(str(path))
        return [{"title": "Pepe the Frog", "url": "https://knowyourmeme.com/memes/pepe"}]

    provider = PlaywrightLensProvider(scraper=fake_scraper)

    assert provider.missing_config() == []
    sources = provider.search_path("/tmp/meme.jpg")

    assert captured == ["/tmp/meme.jpg"]
    assert sources[0].domain == "knowyourmeme.com"


def test_build_provider_selects_by_env(monkeypatch) -> None:
    monkeypatch.setenv("IRIS_ENRICHMENT_PROVIDER", "playwright")
    assert isinstance(build_reverse_image_provider(), PlaywrightLensProvider)
    monkeypatch.setenv("IRIS_ENRICHMENT_PROVIDER", "serpapi")
    assert isinstance(build_reverse_image_provider(), SerpApiLensProvider)


def test_cache_guard_detects_existing_suggestion() -> None:
    conn = _make_conn()
    assert find_existing_suggestion(conn, 1) is None
    assert count_cached_ids(conn, [1]) == 0

    insert_suggestion(
        conn, "job1", 1, EnrichmentSuggestion(provider="test", summary="x", confidence=0.5)
    )

    assert find_existing_suggestion(conn, 1) is not None
    assert count_cached_ids(conn, [1, 2]) == 1
