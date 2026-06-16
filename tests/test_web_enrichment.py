from __future__ import annotations

import sqlite3

import pytest

from core.concepts import create_concept_tables
from core.web_enrichment import (
    EnrichmentSuggestion,
    GeminiAPIBackend,
    HeuristicDistiller,
    LLMDistiller,
    OpenAICompatBackend,
    PlaywrightLensProvider,
    SerpApiLensProvider,
    WebChatBackend,
    WebSource,
    _extract_json,
    apply_suggestion,
    build_distill_messages,
    build_distiller,
    build_reverse_image_provider,
    build_webchat_url,
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
            {"title": "Gojo Satoru", "url": "https://jujutsu-kaisen.fandom.com/wiki/Gojo",
             "has_thumb": True},
            {"title": "Gojo Satoru", "url": "https://jujutsu-kaisen.fandom.com/wiki/Gojo",
             "has_thumb": True},
            {"title": "", "url": ""},
            {"title": "Discussion", "url": "https://reddit.com/r/x"},
        ]
    )

    assert len(sources) == 2
    assert sources[0].domain == "jujutsu-kaisen.fandom.com"
    assert sources[0].match_type == "lens_visual_match"


def test_parse_lens_results_ranks_thumbnail_matches_first() -> None:
    sources = parse_lens_results(
        [
            {"title": "Related search", "url": "https://example.com/related"},
            {"title": "Frieren staring", "url": "https://knowyourmeme.com/x",
             "has_thumb": True},
        ]
    )

    # The real visual match (with thumbnail) must outrank the plain link.
    assert sources[0].domain == "knowyourmeme.com"
    assert sources[0].match_type == "lens_visual_match"
    assert sources[1].match_type == "lens_link"


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


class _FakePage:
    """Minimal page stub for exercising PlaywrightLensProvider._await_results."""

    def __init__(self, url: str, solved_url: str | None = None) -> None:
        self.url = url
        self._solved_url = solved_url
        self.waited_for_solve = False

    def wait_for_url(self, matcher, timeout: int = 0) -> None:
        if callable(matcher):
            # Simulates the human-solve wait on the reCAPTCHA page.
            self.waited_for_solve = True
            if self._solved_url is None:
                raise TimeoutError("captcha not solved")
            self.url = self._solved_url


def test_await_results_headless_captcha_raises() -> None:
    provider = PlaywrightLensProvider(scraper=lambda path: [])  # headless default
    page = _FakePage("https://www.google.com/sorry/index?continue=...search")

    with pytest.raises(RuntimeError, match="IRIS_LENS_HEADLESS=0"):
        provider._await_results(page)

    assert page.waited_for_solve is False


def test_await_results_headed_waits_for_manual_solve() -> None:
    provider = PlaywrightLensProvider(
        headless=False, profile_dir="/tmp/iris_lens_profile", solve_timeout_ms=1000
    )
    page = _FakePage(
        "https://www.google.com/sorry/index?continue=...search",
        solved_url="https://www.google.com/search?vsrid=abc&udm=26",
    )

    provider._await_results(page)  # must not raise once "solved"

    assert page.waited_for_solve is True
    assert "/search" in page.url


def test_extract_json_tolerates_code_fences_and_prose() -> None:
    assert _extract_json('```json\n{"character": "Frieren"}\n```')["character"] == "Frieren"
    assert _extract_json('Claro! {"character": "Gojo"} pronto')["character"] == "Gojo"


def test_build_distill_messages_sends_only_clean_text() -> None:
    sources = [WebSource(title="Frieren staring meme", url="https://knowyourmeme.com/x",
                         domain="knowyourmeme.com", match_type="lens_visual_match")]
    system, user = build_distill_messages(sources)

    assert "JSON" in system
    assert "knowyourmeme.com" in user
    assert "<" not in user  # no raw HTML, only titles + domains


def test_llm_distiller_uses_backend_and_parses_json() -> None:
    captured: dict[str, str] = {}

    class FakeBackend:
        name = "fake"

        def available(self) -> bool:
            return True

        def complete(self, system: str, user: str, sources=None) -> str:
            captured["user"] = user
            return '{"character": "Frieren", "source_work": "Sousou no Frieren", ' \
                   '"meme_archetype": "staring", "tags": "frieren, olhar", "confidence": 0.8}'

    sources = [WebSource(title="Frieren", url="https://x.fandom.com", domain="x.fandom.com")]
    suggestion = LLMDistiller(FakeBackend()).distill(sources)

    assert suggestion.character == "Frieren"
    assert suggestion.source_work == "Sousou no Frieren"
    assert suggestion.provider == "llm:fake"
    assert "fandom.com" in captured["user"]


def test_llm_distiller_falls_back_when_backend_unavailable() -> None:
    class DeadBackend:
        name = "dead"

        def available(self) -> bool:
            return False

        def complete(self, system: str, user: str) -> str:  # pragma: no cover
            raise AssertionError("must not be called")

    sources = [WebSource(title="Pepe", url="https://knowyourmeme.com/pepe",
                         domain="knowyourmeme.com")]
    suggestion = LLMDistiller(DeadBackend()).distill(sources)

    assert suggestion.provider == "heuristic"


def test_webchat_backend_uses_injected_completer_with_deeplink() -> None:
    seen: dict[str, str] = {}

    def completer(url: str) -> str:
        seen["url"] = url
        return '{"character": "Gojo"}'

    backend = WebChatBackend(completer=completer)
    assert backend.available() is True
    sources = [WebSource(title="Gojo", url="https://jujutsu-kaisen.fandom.com/wiki/Gojo")]
    out = backend.complete("SYS", "USER", sources)

    assert seen["url"].startswith("https://chatgpt.com/?")
    assert "jujutsu-kaisen.fandom.com" in seen["url"]
    assert _extract_json(out)["character"] == "Gojo"


def test_build_webchat_url_includes_temporary_and_match_urls() -> None:
    sources = [
        WebSource(title="Frieren staring", url="https://knowyourmeme.com/memes/frieren"),
        WebSource(title="Frieren wiki", url="https://frieren.fandom.com/wiki/Frieren"),
    ]
    url = build_webchat_url(sources, temporary=True)

    assert url.startswith("https://chatgpt.com/?")
    assert "temporary-chat=true" in url
    assert "hints=search" in url
    assert "knowyourmeme.com" in url  # match URLs are sent, not just domains

    plain = build_webchat_url(sources, temporary=False)
    assert "temporary-chat" not in plain


def test_build_webchat_url_trims_matches_to_stay_under_limit() -> None:
    sources = [
        WebSource(title="m" * 200, url="https://example.com/" + "p" * 200)
        for _ in range(40)
    ]
    url = build_webchat_url(sources, temporary=True, max_chars=2000)
    # Trimmed to respect the limit, but keeps at least one match.
    assert len(url) <= 2000 or url.count("https://example.com/") == 1


def test_build_distiller_selects_backend_by_env(monkeypatch) -> None:
    monkeypatch.setenv("IRIS_LLM_BACKEND", "gemini")
    monkeypatch.setenv("IRIS_LLM_API_KEY", "k")
    distiller = build_distiller()
    assert isinstance(distiller, LLMDistiller)
    assert isinstance(distiller.backend, GeminiAPIBackend)

    monkeypatch.setenv("IRIS_LLM_BACKEND", "openai")
    assert isinstance(build_distiller().backend, OpenAICompatBackend)

    monkeypatch.setenv("IRIS_LLM_BACKEND", "webchat")
    assert isinstance(build_distiller().backend, WebChatBackend)

    # UI override wins over env.
    monkeypatch.setenv("IRIS_LLM_BACKEND", "openai")
    assert isinstance(build_distiller({"backend": "gemini"}).backend, GeminiAPIBackend)


def test_build_distiller_defaults_to_heuristic(monkeypatch) -> None:
    for key in ("IRIS_LLM_BACKEND", "IRIS_LLM_ENDPOINT", "IRIS_LLM_API_KEY", "IRIS_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert isinstance(build_distiller(), HeuristicDistiller)


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
