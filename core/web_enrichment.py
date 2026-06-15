"""Web enrichment pipeline for externally identifying indexed images."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib import parse, request


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_web_enrichment_tables(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_enrichment_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_enrichment_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            meme_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            provider TEXT NOT NULL DEFAULT '',
            character TEXT NOT NULL DEFAULT '',
            source_work TEXT NOT NULL DEFAULT '',
            style TEXT NOT NULL DEFAULT '',
            meme_archetype TEXT NOT NULL DEFAULT '',
            context TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_enrichment_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            match_type TEXT NOT NULL DEFAULT '',
            score REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (suggestion_id) REFERENCES web_enrichment_suggestions(id)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_enrichment_suggestions_status "
        "ON web_enrichment_suggestions(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_enrichment_sources_suggestion "
        "ON web_enrichment_sources(suggestion_id)"
    )
    conn.commit()


@dataclass(frozen=True)
class WebSource:
    title: str
    url: str
    source_url: str = ""
    domain: str = ""
    match_type: str = ""
    score: float = 0.0


@dataclass(frozen=True)
class EnrichmentSuggestion:
    provider: str
    character: str = ""
    source_work: str = ""
    style: str = ""
    meme_archetype: str = ""
    context: str = ""
    tags: str = ""
    summary: str = ""
    confidence: float = 0.0
    sources: tuple[WebSource, ...] = ()
    error_message: str = ""


@dataclass(frozen=True)
class S3Config:
    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str
    prefix: str = "iris-enrichment/"
    region: str = "auto"

    @classmethod
    def from_env(cls) -> S3Config:
        return cls(
            endpoint_url=os.environ.get("IRIS_S3_ENDPOINT_URL", "").rstrip("/"),
            bucket=os.environ.get("IRIS_S3_BUCKET", ""),
            access_key_id=os.environ.get("IRIS_S3_ACCESS_KEY_ID", ""),
            secret_access_key=os.environ.get("IRIS_S3_SECRET_ACCESS_KEY", ""),
            public_base_url=os.environ.get("IRIS_S3_PUBLIC_BASE_URL", "").rstrip("/"),
            prefix=os.environ.get("IRIS_S3_PREFIX", "iris-enrichment/").strip("/").strip() + "/",
            region=os.environ.get("IRIS_S3_REGION", "auto"),
        )

    def missing(self) -> list[str]:
        required = {
            "IRIS_S3_ENDPOINT_URL": self.endpoint_url,
            "IRIS_S3_BUCKET": self.bucket,
            "IRIS_S3_ACCESS_KEY_ID": self.access_key_id,
            "IRIS_S3_SECRET_ACCESS_KEY": self.secret_access_key,
            "IRIS_S3_PUBLIC_BASE_URL": self.public_base_url,
        }
        return [key for key, value in required.items() if not value]


class S3TemporaryImagePublisher:
    def __init__(self, config: S3Config | None = None):
        self.config = config or S3Config.from_env()

    def missing_config(self) -> list[str]:
        return self.config.missing()

    def publish(self, path: str | os.PathLike[str]) -> str:
        missing = self.missing_config()
        if missing:
            raise RuntimeError("Configuracao S3 ausente: " + ", ".join(missing))
        file_path = Path(path)
        data = file_path.read_bytes()
        suffix = file_path.suffix.lower() or ".jpg"
        key = f"{self.config.prefix}{uuid.uuid4().hex}{suffix}"
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self._put_object(key, data, content_type)
        return self.config.public_base_url + "/" + parse.quote(key)

    def _put_object(self, key: str, data: bytes, content_type: str) -> None:
        cfg = self.config
        parsed = parse.urlparse(cfg.endpoint_url)
        if not parsed.scheme or not parsed.netloc:
            raise RuntimeError("IRIS_S3_ENDPOINT_URL invalido")
        encoded_key = "/".join(parse.quote(part) for part in key.split("/"))
        canonical_uri = f"/{parse.quote(cfg.bucket)}/{encoded_key}"
        endpoint = cfg.endpoint_url + canonical_uri
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(data).hexdigest()
        host = parsed.netloc
        headers = {
            "content-type": content_type,
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical_request = "\n".join(
            ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
        )
        scope = f"{date_stamp}/{cfg.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _aws_signing_key(cfg.secret_access_key, date_stamp, cfg.region, "s3")
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers["authorization"] = (
            "AWS4-HMAC-SHA256 "
            f"Credential={cfg.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        req = request.Request(endpoint, data=data, method="PUT", headers=headers)
        with request.urlopen(req, timeout=30) as response:
            if response.status >= 300:
                raise RuntimeError(f"S3 upload falhou: HTTP {response.status}")


def _aws_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key_date = hmac.new(("AWS4" + secret_key).encode(), date_stamp.encode(), hashlib.sha256).digest()
    key_region = hmac.new(key_date, region.encode(), hashlib.sha256).digest()
    key_service = hmac.new(key_region, service.encode(), hashlib.sha256).digest()
    return hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()


def _llm_content(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        if isinstance(message.get("content"), str):
            return message["content"]
    output = data.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            for content in item.get("content", []):
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    raise ValueError("Resposta LLM sem texto")


@runtime_checkable
class ReverseImageProvider(Protocol):
    """Common contract for every reverse-image-search backend.

    A provider receives a *local file path* and returns web sources. How it
    reaches the search engine (uploading to S3 + REST API, or driving a browser
    locally) is an internal detail, keeping ``WebEnrichmentService`` decoupled
    from any specific vendor.
    """

    provider_name: str

    def missing_config(self) -> list[str]: ...

    def search_path(self, path: str | os.PathLike[str]) -> list[WebSource]: ...


class SerpApiLensProvider:
    """Reverse search via SerpApi's Google Lens engine.

    SerpApi only accepts a *public image URL*, so this provider owns an image
    publisher (S3 by default) used to upload the local file before searching.
    """

    provider_name = "serpapi_google_lens"

    def __init__(
        self,
        api_key: str | None = None,
        publisher: S3TemporaryImagePublisher | None = None,
    ):
        self.api_key = api_key or os.environ.get("SERPAPI_KEY", "")
        self.publisher = publisher or S3TemporaryImagePublisher()

    def missing_config(self) -> list[str]:
        missing = list(self.publisher.missing_config())
        if not self.api_key:
            missing.append("SERPAPI_KEY")
        return missing

    def search_path(self, path: str | os.PathLike[str]) -> list[WebSource]:
        image_url = self.publisher.publish(path)
        return self.search(image_url)

    def search(self, image_url: str) -> list[WebSource]:
        if not self.api_key:
            raise RuntimeError("SERPAPI_KEY ausente")
        params = {
            "engine": "google_lens",
            "url": image_url,
            "type": "all",
            "hl": os.environ.get("IRIS_SERPAPI_HL", "en"),
            "country": os.environ.get("IRIS_SERPAPI_COUNTRY", "us"),
            "safe": os.environ.get("IRIS_SERPAPI_SAFE", "active"),
            "api_key": self.api_key,
            "output": "json",
        }
        url = "https://serpapi.com/search?" + parse.urlencode(params)
        with request.urlopen(url, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return normalize_serpapi_sources(payload)


def normalize_serpapi_sources(payload: dict[str, Any]) -> list[WebSource]:
    sources: list[WebSource] = []
    groups = [
        ("knowledge_graph", payload.get("knowledge_graph")),
        ("about_this_image", payload.get("about_this_image")),
        ("exact_matches", payload.get("exact_matches")),
        ("visual_matches", payload.get("visual_matches")),
        ("image_results", payload.get("image_results")),
        ("inline_images", payload.get("inline_images")),
        ("organic_results", payload.get("organic_results")),
    ]
    for match_type, value in groups:
        items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        for item in items:
            title = str(item.get("title") or item.get("name") or item.get("source") or "").strip()
            url = str(item.get("link") or item.get("url") or item.get("source") or "").strip()
            source_url = str(item.get("source") or item.get("original") or "").strip()
            if not title and not url and not source_url:
                continue
            domain = _domain(source_url or url)
            sources.append(
                WebSource(
                    title=title,
                    url=url,
                    source_url=source_url,
                    domain=domain,
                    match_type=match_type,
                    score=0.0,
                )
            )
    deduped: list[WebSource] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (source.title.lower(), source.source_url or source.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped[:30]


def parse_lens_results(items: list[dict[str, Any]], limit: int = 30) -> list[WebSource]:
    """Turn raw ``{title, url, source_url}`` rows scraped from the Lens page
    into deduplicated :class:`WebSource` objects.

    Kept pure (no browser) so the parsing can be unit-tested on its own.
    """
    sources: list[WebSource] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        source_url = str(item.get("source_url") or url).strip()
        if not title and not url:
            continue
        key = (title.lower(), source_url or url)
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            WebSource(
                title=title,
                url=url,
                source_url=source_url,
                domain=_domain(source_url or url),
                match_type="lens_visual_match",
                score=0.0,
            )
        )
    return sources[:limit]


class PlaywrightLensProvider:
    """Local reverse search by driving Google Lens in a headless browser.

    No SerpApi key and no S3 upload required: the local file is sent straight
    into the Lens upload form, exactly like dropping it in the browser. This is
    fragile by nature -- it depends on Google's DOM, which changes without
    notice -- and intended for low-volume, personal use.

    The browser interaction is isolated in ``_scrape_lens`` and can be swapped
    via the ``scraper`` argument, so the provider stays unit-testable.
    """

    provider_name = "playwright_google_lens"
    upload_url = "https://lens.google.com/upload"
    _user_agent = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    )
    _launch_args = (
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    )

    def __init__(
        self,
        *,
        headless: bool | None = None,
        timeout_ms: int | None = None,
        max_results: int = 30,
        locale: str | None = None,
        scraper: Callable[[str | os.PathLike[str]], list[dict[str, Any]]] | None = None,
    ):
        env_headless = os.environ.get("IRIS_LENS_HEADLESS", "1").strip().lower()
        self.headless = headless if headless is not None else env_headless not in {"0", "false", "no"}
        self.timeout_ms = timeout_ms or int(os.environ.get("IRIS_LENS_TIMEOUT_MS", "45000"))
        self.locale = locale or os.environ.get("IRIS_LENS_LOCALE", "en-US")
        self.max_results = max_results
        self._scraper = scraper

    def missing_config(self) -> list[str]:
        if self._scraper is not None:
            return []
        try:
            import playwright.sync_api  # noqa: F401
        except Exception:
            return ["playwright (pip install playwright && playwright install chromium)"]
        return []

    def search_path(self, path: str | os.PathLike[str]) -> list[WebSource]:
        scraper = self._scraper or self._scrape_lens
        return parse_lens_results(scraper(path), limit=self.max_results)

    def _scrape_lens(self, path: str | os.PathLike[str]) -> list[dict[str, Any]]:
        from playwright.sync_api import sync_playwright

        file_path = str(Path(path))
        with self._stealth(sync_playwright()) as pw:
            browser = pw.chromium.launch(headless=self.headless, args=list(self._launch_args))
            try:
                context = browser.new_context(
                    user_agent=self._user_agent,
                    locale=self.locale,
                    viewport={"width": 1366, "height": 768},
                )
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.goto(self.upload_url, wait_until="domcontentloaded")
                self._dismiss_consent(page)
                # The visible upload control is the last file input on the page;
                # selecting it triggers a client-side navigation to the results.
                page.locator("input[type=file]").last.set_input_files(file_path)
                try:
                    page.wait_for_url("**/search**", timeout=self.timeout_ms)
                except Exception:
                    pass
                if "/sorry/" in page.url:
                    raise RuntimeError(
                        "Google Lens exigiu CAPTCHA (tráfego sinalizado). Tente de um IP "
                        "residencial, reduza o volume ou use IRIS_ENRICHMENT_PROVIDER=serpapi."
                    )
                if "/search" not in page.url:
                    raise RuntimeError("Google Lens não retornou página de resultados.")
                try:
                    page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
                except Exception:
                    pass
                return self._extract_results(page)
            finally:
                browser.close()

    @staticmethod
    def _stealth(playwright_cm: Any) -> Any:
        """Wrap the Playwright context manager with stealth evasions if the
        optional ``playwright-stealth`` package is installed; otherwise return
        the plain context manager."""
        try:
            from playwright_stealth import Stealth

            return Stealth().use_sync(playwright_cm)
        except Exception:
            return playwright_cm

    @staticmethod
    def _dismiss_consent(page: Any) -> None:
        for label in ("Aceitar tudo", "Accept all", "I agree", "Concordo", "Aceito"):
            try:
                button = page.get_by_role("button", name=label)
                if button.count():
                    button.first.click(timeout=2000)
                    return
            except Exception:
                continue

    @staticmethod
    def _extract_results(page: Any) -> list[dict[str, Any]]:
        return page.evaluate(
            """
            () => {
              const out = [];
              const seen = new Set();
              for (const a of document.querySelectorAll('a[href^="http"]')) {
                const url = a.href;
                if (!url || seen.has(url)) continue;
                if (/google\\.com|gstatic\\.com|googleusercontent\\.com/.test(url)) continue;
                const title = (a.innerText || a.getAttribute('aria-label') || '').trim();
                if (!title) continue;
                seen.add(url);
                out.push({ title, url, source_url: url });
              }
              return out;
            }
            """
        )


def build_reverse_image_provider() -> ReverseImageProvider:
    """Select the reverse-image provider from ``IRIS_ENRICHMENT_PROVIDER``.

    ``serpapi`` (default) keeps the paid SerpApi + S3 path; ``playwright``
    (aliases ``lens``/``local``) drives Google Lens locally with no API cost.
    """
    kind = os.environ.get("IRIS_ENRICHMENT_PROVIDER", "serpapi").strip().lower()
    if kind in {"playwright", "lens", "local"}:
        return PlaywrightLensProvider()
    return SerpApiLensProvider()


class HeuristicDistiller:
    def distill(self, sources: list[WebSource]) -> EnrichmentSuggestion:
        text = " ".join(
            part
            for source in sources
            for part in [source.title, source.domain, source.source_url]
            if part
        )
        text_l = text.lower()
        style = _first_match(
            text_l,
            {
                "wojak": "wojak",
                "soyjak": "soyjak",
                "anime": "anime",
                "manga": "manga",
                "cartoon": "cartoon",
                "pixel art": "pixel art",
                "comic": "comic",
                "3d": "3d",
            },
        )
        meme_archetype = _first_match(
            text_l,
            {
                "doomer": "doomer",
                "boomer": "boomer",
                "chad": "chad",
                "npc": "npc",
                "brainlet": "brainlet",
                "pepe": "pepe",
                "doge": "doge",
                "wojak": "wojak",
                "soyjak": "soyjak",
                "rage comic": "rage comic",
            },
        )
        character = _candidate_from_titles(sources)
        source_work = _source_work_from_sources(sources, text_l)
        tags = ", ".join(
            x
            for x in [character, source_work, style, meme_archetype, "web-enriched"]
            if x
        )
        evidence = sum(1 for x in [character, source_work, style, meme_archetype] if x)
        confidence = min(0.9, 0.25 + evidence * 0.15 + min(len(sources), 8) * 0.025)
        summary_parts = []
        if character:
            summary_parts.append(f"Possivel personagem: {character}.")
        if source_work:
            summary_parts.append(f"Possivel obra/serie: {source_work}.")
        if meme_archetype:
            summary_parts.append(f"Arquétipo de meme sugerido: {meme_archetype}.")
        if style:
            summary_parts.append(f"Estilo visual sugerido: {style}.")
        summary = " ".join(summary_parts) or "Fontes web encontradas, mas sem identificacao forte."
        return EnrichmentSuggestion(
            provider="heuristic",
            character=character,
            source_work=source_work,
            style=style,
            meme_archetype=meme_archetype,
            context=meme_archetype or source_work,
            tags=tags,
            summary=summary,
            confidence=round(confidence, 3),
            sources=tuple(sources[:12]),
        )


class HybridDistiller:
    def __init__(self, fallback: HeuristicDistiller | None = None):
        self.fallback = fallback or HeuristicDistiller()
        self.endpoint = os.environ.get("IRIS_LLM_ENDPOINT", "").strip()
        self.api_key = os.environ.get("IRIS_LLM_API_KEY", "").strip()
        self.model = os.environ.get("IRIS_LLM_MODEL", "").strip()

    def distill(self, sources: list[WebSource]) -> EnrichmentSuggestion:
        fallback = self.fallback.distill(sources)
        if not self.endpoint or not self.api_key or not self.model:
            return fallback
        try:
            payload = {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return compact JSON with keys: character, source_work, style, "
                            "meme_archetype, context, tags, summary, confidence. "
                            "Use empty strings for unknowns and confidence 0..1."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            [
                                {
                                    "title": source.title,
                                    "url": source.url,
                                    "source_url": source.source_url,
                                    "domain": source.domain,
                                    "match_type": source.match_type,
                                }
                                for source in sources[:12]
                            ],
                            ensure_ascii=False,
                        ),
                    },
                ],
            }
            req = request.Request(
                self.endpoint,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
            )
            with request.urlopen(req, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = _llm_content(data)
            parsed = json.loads(content)
            return EnrichmentSuggestion(
                provider="hybrid_llm",
                character=str(parsed.get("character") or fallback.character),
                source_work=str(parsed.get("source_work") or fallback.source_work),
                style=str(parsed.get("style") or fallback.style),
                meme_archetype=str(parsed.get("meme_archetype") or fallback.meme_archetype),
                context=str(parsed.get("context") or fallback.context),
                tags=str(parsed.get("tags") or fallback.tags),
                summary=str(parsed.get("summary") or fallback.summary),
                confidence=float(parsed.get("confidence") or fallback.confidence),
                sources=fallback.sources,
            )
        except Exception:
            return fallback


class WebEnrichmentService:
    def __init__(
        self,
        provider: ReverseImageProvider | None = None,
        distiller: HybridDistiller | HeuristicDistiller | None = None,
        *,
        publisher: S3TemporaryImagePublisher | None = None,
    ):
        if provider is None:
            provider = build_reverse_image_provider()
            # A custom S3 publisher only applies to the SerpApi path.
            if publisher is not None and isinstance(provider, SerpApiLensProvider):
                provider.publisher = publisher
        self.provider = provider
        self.distiller = distiller or HybridDistiller()

    def missing_config(self) -> list[str]:
        return list(self.provider.missing_config())

    def enrich_path(self, path: str | os.PathLike[str]) -> EnrichmentSuggestion:
        sources = self.provider.search_path(path)
        suggestion = self.distiller.distill(sources)
        return EnrichmentSuggestion(
            provider=self.provider.provider_name,
            character=suggestion.character,
            source_work=suggestion.source_work,
            style=suggestion.style,
            meme_archetype=suggestion.meme_archetype,
            context=suggestion.context,
            tags=suggestion.tags,
            summary=suggestion.summary,
            confidence=suggestion.confidence,
            sources=suggestion.sources,
            error_message=suggestion.error_message,
        )


def create_job(conn: sqlite3.Connection, db_ids: list[int]) -> str:
    create_web_enrichment_tables(conn)
    job_id = uuid.uuid4().hex
    now = now_iso()
    conn.execute(
        """
        INSERT INTO web_enrichment_jobs (id, status, total, done, message, created_at, updated_at)
        VALUES (?, 'queued', ?, 0, 'Na fila', ?, ?)
        """,
        (job_id, len(db_ids), now, now),
    )
    conn.commit()
    return job_id


def update_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    status: str | None = None,
    done: int | None = None,
    message: str | None = None,
    error_message: str | None = None,
) -> None:
    fields: dict[str, Any] = {"updated_at": now_iso()}
    if status is not None:
        fields["status"] = status
        if status in {"completed", "failed"}:
            fields["finished_at"] = now_iso()
    if done is not None:
        fields["done"] = done
    if message is not None:
        fields["message"] = message
    if error_message is not None:
        fields["error_message"] = error_message
    set_clause = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(
        f"UPDATE web_enrichment_jobs SET {set_clause} WHERE id = ?",
        list(fields.values()) + [job_id],
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    create_web_enrichment_tables(conn)
    row = conn.execute("SELECT * FROM web_enrichment_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def find_existing_suggestion(conn: sqlite3.Connection, meme_id: int) -> dict[str, Any] | None:
    """Return the latest non-rejected suggestion for a meme, if any.

    Used as a cache guard: an image that already has a ``pending`` or
    ``applied`` suggestion does not need a fresh (paid) web search.
    """
    create_web_enrichment_tables(conn)
    row = conn.execute(
        """
        SELECT id, status FROM web_enrichment_suggestions
        WHERE meme_id = ? AND status IN ('pending', 'applied')
        ORDER BY id DESC LIMIT 1
        """,
        (meme_id,),
    ).fetchone()
    return dict(row) if row else None


def count_cached_ids(conn: sqlite3.Connection, meme_ids: list[int]) -> int:
    """How many of these memes already have a reusable suggestion."""
    return sum(1 for meme_id in meme_ids if find_existing_suggestion(conn, meme_id) is not None)


def insert_suggestion(
    conn: sqlite3.Connection,
    job_id: str,
    meme_id: int,
    suggestion: EnrichmentSuggestion,
) -> int:
    create_web_enrichment_tables(conn)
    now = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO web_enrichment_suggestions (
            job_id, meme_id, status, provider, character, source_work, style,
            meme_archetype, context, tags, summary, confidence, error_message,
            created_at, updated_at
        ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            meme_id,
            suggestion.provider,
            suggestion.character,
            suggestion.source_work,
            suggestion.style,
            suggestion.meme_archetype,
            suggestion.context,
            suggestion.tags,
            suggestion.summary,
            suggestion.confidence,
            suggestion.error_message,
            now,
            now,
        ),
    )
    suggestion_id = int(cursor.lastrowid)
    conn.executemany(
        """
        INSERT INTO web_enrichment_sources (
            suggestion_id, title, url, source_url, domain, match_type, score
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                suggestion_id,
                source.title,
                source.url,
                source.source_url,
                source.domain,
                source.match_type,
                source.score,
            )
            for source in suggestion.sources
        ],
    )
    conn.commit()
    return suggestion_id


def list_suggestions(conn: sqlite3.Connection, status: str = "pending") -> list[dict[str, Any]]:
    create_web_enrichment_tables(conn)
    params: list[Any] = []
    where = ""
    if status and status != "all":
        where = "WHERE s.status = ?"
        params.append(status)
    rows = conn.execute(
        f"""
        SELECT s.*, m.arquivo, m.caminho
        FROM web_enrichment_suggestions s
        LEFT JOIN memes m ON m.id = s.meme_id
        {where}
        ORDER BY s.created_at DESC, s.id DESC
        """,
        params,
    ).fetchall()
    suggestions = [dict(row) for row in rows]
    for suggestion in suggestions:
        suggestion["sources"] = [
            dict(row)
            for row in conn.execute(
                "SELECT title, url, source_url, domain, match_type, score "
                "FROM web_enrichment_sources WHERE suggestion_id = ? ORDER BY id",
                (suggestion["id"],),
            ).fetchall()
        ]
    return suggestions


def apply_suggestion(conn: sqlite3.Connection, suggestion_id: int, fields: list[str]) -> dict[str, Any]:
    create_web_enrichment_tables(conn)
    row = conn.execute(
        "SELECT * FROM web_enrichment_suggestions WHERE id = ?", (suggestion_id,)
    ).fetchone()
    if not row:
        raise ValueError("Sugestao nao encontrada")
    suggestion = dict(row)
    if suggestion["status"] != "pending":
        raise ValueError("Sugestao ja revisada")

    allowed = set(fields) if fields else {
        "character",
        "source_work",
        "style",
        "meme_archetype",
        "context",
        "tags",
        "summary",
    }
    meme_id = int(suggestion["meme_id"])
    current = conn.execute(
        "SELECT tags, descricao_ia FROM memes WHERE id = ?", (meme_id,)
    ).fetchone()
    if not current:
        raise ValueError("Registro nao encontrado")
    updates: dict[str, str] = {}
    if "source_work" in allowed and suggestion["source_work"]:
        updates["source_work"] = suggestion["source_work"]
    if "style" in allowed and suggestion["style"]:
        updates["style"] = suggestion["style"]
    context_parts = []
    if "context" in allowed and suggestion["context"]:
        context_parts.append(suggestion["context"])
    if "meme_archetype" in allowed and suggestion["meme_archetype"]:
        context_parts.append(suggestion["meme_archetype"])
    if context_parts:
        updates["context"] = ", ".join(_unique_tokens(context_parts))
    if "tags" in allowed and suggestion["tags"]:
        updates["tags"] = ", ".join(
            _unique_tokens([current["tags"] or "", suggestion["tags"]])
        )
    if "summary" in allowed and suggestion["summary"]:
        previous = current["descricao_ia"] or ""
        updates["descricao_ia"] = (
            previous + "\n\nWeb: " + suggestion["summary"] if previous else suggestion["summary"]
        )
    if updates:
        set_clause = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE memes SET {set_clause} WHERE id = ?",
            list(updates.values()) + [meme_id],
        )

    for field, category in [
        ("character", "personagem"),
        ("source_work", "outro"),
        ("meme_archetype", "outro"),
    ]:
        value = suggestion.get(field, "").strip()
        if field in allowed and value:
            concept_id = _find_or_create_concept(conn, value, category)
            conn.execute(
                """
                INSERT OR REPLACE INTO concept_media (concept_id, meme_id, confirmed, added_at)
                VALUES (?, ?, 1, ?)
                """,
                (concept_id, meme_id, now_iso()),
            )

    conn.execute(
        "UPDATE web_enrichment_suggestions SET status = 'applied', updated_at = ? WHERE id = ?",
        (now_iso(), suggestion_id),
    )
    conn.commit()
    return {"ok": True, "meme_id": meme_id, "updated": sorted(updates)}


def reject_suggestion(conn: sqlite3.Connection, suggestion_id: int) -> None:
    create_web_enrichment_tables(conn)
    conn.execute(
        "UPDATE web_enrichment_suggestions SET status = 'rejected', updated_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (now_iso(), suggestion_id),
    )
    conn.commit()


def _find_or_create_concept(conn: sqlite3.Connection, name: str, category: str) -> int:
    row = conn.execute("SELECT id FROM concepts WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        """
        INSERT INTO concepts (name, description, category, search_terms, auto_threshold, created_at)
        VALUES (?, ?, ?, ?, 0.65, ?)
        """,
        (name, "Criado por enriquecimento web", category, name, now_iso()),
    )
    return int(cursor.lastrowid)


def _candidate_from_titles(sources: list[WebSource]) -> str:
    stop = {
        "image",
        "images",
        "meme",
        "memes",
        "wallpaper",
        "png",
        "jpg",
        "gif",
        "reddit",
        "pinterest",
        "wiki",
    }
    counts: dict[str, int] = {}
    for source in sources[:12]:
        title = re.sub(r"\s+", " ", source.title).strip()
        if not title:
            continue
        candidate = re.split(r"\s[-|:]\s| – | — ", title)[0].strip()
        candidate = re.sub(r"\b(official|image|images|meme|memes|wallpaper)\b", "", candidate, flags=re.I)
        candidate = candidate.strip(" -_|:")
        words = [w for w in candidate.split() if w.lower() not in stop]
        if 1 <= len(words) <= 5:
            normalized = " ".join(words)
            if len(normalized) >= 3:
                counts[normalized] = counts.get(normalized, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: (item[1], len(item[0])))[0]


def _source_work_from_sources(sources: list[WebSource], text_l: str) -> str:
    known = [
        "one piece",
        "naruto",
        "dragon ball",
        "jujutsu kaisen",
        "chainsaw man",
        "bleach",
        "attack on titan",
        "evangelion",
        "pokemon",
        "marvel",
        "dc comics",
        "star wars",
    ]
    for item in known:
        if item in text_l:
            return item.title()
    for source in sources[:12]:
        title = source.title.strip()
        parts = re.split(r"\s[-|]\s| – | — ", title)
        if len(parts) >= 2:
            candidate = parts[1].strip()
            candidate = re.sub(r"\b(wiki|fandom|meme|image|wallpaper)\b", "", candidate, flags=re.I)
            candidate = candidate.strip(" -_|:")
            if 3 <= len(candidate) <= 60:
                return candidate
    return ""


def _first_match(text: str, mapping: dict[str, str]) -> str:
    for needle, value in mapping.items():
        if needle in text:
            return value
    return ""


def _domain(url: str) -> str:
    if not url:
        return ""
    parsed = parse.urlparse(url)
    return parsed.netloc.lower().removeprefix("www.")


def _unique_tokens(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in re.split(r"[,;\n]", value):
            token = item.strip()
            key = token.lower()
            if token and key not in seen:
                seen.add(key)
                out.append(token)
    return out
