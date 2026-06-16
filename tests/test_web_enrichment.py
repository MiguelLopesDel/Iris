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
    _domain_tier,
    _extract_json,
    apply_suggestion,
    build_distill_messages,
    build_distiller,
    build_reverse_image_provider,
    build_webchat_url,
    count_cached_ids,
    create_web_enrichment_tables,
    find_existing_suggestion,
    format_vocabulary,
    gather_vocabulary,
    insert_suggestion,
    list_suggestions,
    load_existing_sources,
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


def test_parse_lens_results_drops_noise_and_ranks_rich_domains() -> None:
    sources = parse_lens_results(
        [
            {"title": "Frieren clip", "url": "https://youtube.com/watch?v=x", "has_thumb": True},
            {"title": "Frieren insta", "url": "https://instagram.com/p/x", "has_thumb": True},
            {"title": "Frieren plain", "url": "https://example.com/x", "has_thumb": True},
            {"title": "Frieren KYM", "url": "https://knowyourmeme.com/memes/frieren",
             "has_thumb": True},
        ]
    )
    domains = [s.domain for s in sources]

    assert "youtube.com" not in domains and "instagram.com" not in domains
    assert domains[0] == "knowyourmeme.com"  # rich/explanatory domain ranked first


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


def test_search_path_filters_noise_and_ranks_through_full_pipeline() -> None:
    """End-to-end through search_path -> parse_lens_results: noise dropped,
    rich domains first. Guards the whole Lens result-handling pipeline."""
    def scraper(path):
        return [
            {"title": "clip", "url": "https://youtube.com/watch?v=x", "has_thumb": True},
            {"title": "plain", "url": "https://example.com/x", "has_thumb": True},
            {"title": "kym", "url": "https://knowyourmeme.com/x", "has_thumb": True},
        ]

    sources = PlaywrightLensProvider(scraper=scraper).search_path("/x.jpg")
    domains = [s.domain for s in sources]

    assert "youtube.com" not in domains  # noise dropped
    assert domains[0] == "knowyourmeme.com"  # rich domain first


class _FakeSettlePage:
    def __init__(self, selector_raises: bool) -> None:
        self.selector_raises = selector_raises
        self.timeout_waited = False

    def wait_for_selector(self, selector, timeout=0, **kwargs):
        if self.selector_raises:
            raise TimeoutError("no anchors")

    def wait_for_timeout(self, ms):
        self.timeout_waited = True


def test_settle_results_is_bounded_and_never_hangs() -> None:
    provider = PlaywrightLensProvider(scraper=lambda p: [])
    # Even when no anchor ever appears, _settle_results must return (bounded),
    # not block like wait_for_load_state('networkidle') used to.
    page = _FakeSettlePage(selector_raises=True)

    provider._settle_results(page)

    assert page.timeout_waited is True


def test_load_existing_sources_reuses_last_suggestion() -> None:
    conn = _make_conn()
    assert load_existing_sources(conn, 1) == []

    insert_suggestion(
        conn,
        "job1",
        1,
        EnrichmentSuggestion(
            provider="lens",
            summary="x",
            sources=(
                WebSource(title="Gojo", url="https://x.fandom.com", domain="x.fandom.com"),
            ),
        ),
    )

    reused = load_existing_sources(conn, 1)
    assert len(reused) == 1
    assert reused[0].domain == "x.fandom.com"


class _FakeLocator:
    def __init__(self, count: int, on_set=None) -> None:
        self._count = count
        self._on_set = on_set
        self.first = self

    def count(self) -> int:
        return self._count

    def set_input_files(self, path: str) -> None:
        if self._on_set:
            self._on_set(path)


class _FakeUploadPage:
    def __init__(self, locators: dict) -> None:
        self._locators = locators

    def wait_for_selector(self, *args, **kwargs) -> None:
        pass

    def locator(self, selector: str):
        return self._locators.get(selector, _FakeLocator(0))


def test_upload_image_falls_back_to_encoded_image_input() -> None:
    captured: dict[str, str] = {}
    provider = PlaywrightLensProvider(scraper=lambda path: [])
    page = _FakeUploadPage(
        {
            "input[name=encoded_image]": _FakeLocator(
                1, lambda p: captured.__setitem__("file", p)
            ),
        }
    )

    provider._upload_image(page, "/tmp/meme.jpg")  # no "upload a file" link -> encoded_image

    assert captured["file"] == "/tmp/meme.jpg"


def test_upload_image_raises_when_no_upload_field() -> None:
    provider = PlaywrightLensProvider(scraper=lambda path: [])
    page = _FakeUploadPage({})  # nothing on the page

    with pytest.raises(RuntimeError, match="campo de upload"):
        provider._upload_image(page, "/tmp/meme.jpg")


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

        def complete(self, system: str, user: str, sources=None, vocabulary=None) -> str:
            captured["user"] = user
            captured["vocabulary"] = vocabulary
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


def test_domain_tier_classifies_noise_rich_and_neutral() -> None:
    assert _domain_tier("youtube.com") < 0
    assert _domain_tier("www.instagram.com") < 0
    assert _domain_tier("knowyourmeme.com") > 0
    assert _domain_tier("frieren.fandom.com") > 0
    assert _domain_tier("example.com") == 0


class _FakeChatPage:
    """Fake page for WebChatBackend._ensure_ready: composer becomes ready only
    on the Nth wait_for_selector call (simulating a login/Cloudflare delay)."""

    def __init__(self, ready_on_call: int) -> None:
        self.calls = 0
        self.ready_on_call = ready_on_call

    def wait_for_selector(self, selector, timeout=0, **kwargs):
        self.calls += 1
        if self.calls < self.ready_on_call:
            raise TimeoutError("composer not ready")


def test_webchat_ensure_ready_raises_in_headless_when_not_logged_in() -> None:
    backend = WebChatBackend(headless=True)
    page = _FakeChatPage(ready_on_call=99)  # never ready

    with pytest.raises(RuntimeError, match="login"):
        backend._ensure_ready(page)


def test_webchat_ensure_ready_waits_for_login_when_headed() -> None:
    backend = WebChatBackend(headless=False)
    page = _FakeChatPage(ready_on_call=2)  # not ready first, ready after "login"

    backend._ensure_ready(page)  # must not raise once the composer appears

    assert page.calls == 2  # initial check + the login wait


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


def test_gather_vocabulary_buckets_concepts_by_category() -> None:
    conn = _make_conn()
    conn.execute("UPDATE memes SET style = 'anime', tags = 'frieren, olhar, anime' WHERE id = 1")
    for name, category in [
        ("Frieren", "personagem"),
        ("Sousou no Frieren", "obra"),
        ("staring", "arquetipo"),
        ("Algo Antigo", "outro"),  # legacy concept -> generic bucket
    ]:
        conn.execute(
            "INSERT INTO concepts (name, category, description, search_terms, auto_threshold, "
            "created_at) VALUES (?, ?, '', '', 0.2, '2026-01-01')",
            (name, category),
        )
    conn.commit()

    vocab = gather_vocabulary(conn)

    assert vocab["characters"] == ["Frieren"]
    assert vocab["source_works"] == ["Sousou no Frieren"]
    assert vocab["meme_archetypes"] == ["staring"]
    assert vocab["categories"] == ["Algo Antigo"]  # legacy 'outro' falls back here
    assert "anime" in vocab["styles"]
    assert "frieren" in vocab["tags"] and "web-enriched" not in vocab["tags"]


def test_format_vocabulary_instructs_reuse_and_is_empty_when_blank() -> None:
    assert format_vocabulary(None) == ""
    assert format_vocabulary({"tags": [], "characters": []}) == ""

    text = format_vocabulary({"characters": ["Frieren"], "tags": ["olhar", "anime"]})
    assert "reutilizar" in text.lower()
    assert "Frieren" in text and "olhar" in text


def test_build_messages_and_url_include_existing_vocabulary() -> None:
    sources = [WebSource(title="Frieren", url="https://knowyourmeme.com/x", domain="knowyourmeme.com")]
    vocab = {"characters": ["Frieren"], "tags": ["olhar"]}

    _, user = build_distill_messages(sources, vocab)
    assert "Frieren" in user and "olhar" in user

    url = build_webchat_url(sources, vocabulary=vocab)
    assert "Frieren" in url  # vocabulary travels in the deep link too


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
