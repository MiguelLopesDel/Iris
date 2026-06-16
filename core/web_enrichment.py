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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib import parse, request

from core.browser_session import (
    SHARED_PROFILE_DIR,
    get_browser_session,
    set_window_visible,
    shared_session_enabled,
)


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


@runtime_checkable
class Distiller(Protocol):
    """Turns web sources into a structured suggestion. Implementations range
    from a pure-heuristic one to LLM-backed transports (API or web chat)."""

    def distill(
        self, sources: list[WebSource], vocabulary: dict[str, list[str]] | None = None
    ) -> EnrichmentSuggestion: ...


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


# Domains that rarely help identify a still image: video, social noise, stock
# photos and shops. Dropped from the results.
_NOISE_DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "facebook.com",
    "shutterstock.com", "istockphoto.com", "gettyimages", "alamy.com",
    "dreamstime.com", "123rf.com", "depositphotos.com", "stock.adobe.com",
    "amazon.", "aliexpress.", "ebay.", "etsy.com", "mercadolivre.", "walmart.com",
)
# Domains that usually explain/discuss an image (high signal for identification).
_RICH_DOMAINS = (
    "knowyourmeme.com", "fandom.com", "wikipedia.org", "wikia", "tvtropes.org",
    "reddit.com", "myanimelist.net", "anilist.co", "danbooru", "gelbooru",
    "zerochan.net", "safebooru", "pixiv.net", "deviantart.com", "tumblr.com",
    "wikihow.com",
)


def _domain_tier(domain: str) -> int:
    """+1 for rich/explanatory domains, -1 for noise (video/stock/shop), else 0."""
    d = (domain or "").lower()
    if any(n in d for n in _NOISE_DOMAINS):
        return -1
    if any(r in d for r in _RICH_DOMAINS):
        return 1
    return 0


def parse_lens_results(items: list[dict[str, Any]], limit: int = 30) -> list[WebSource]:
    """Turn raw ``{title, url, source_url}`` rows scraped from the Lens page
    into deduplicated :class:`WebSource` objects.

    Noise domains (YouTube, Instagram, stock/shops) are dropped, and
    rich/explanatory domains (knowyourmeme, fandom, wiki, reddit...) are ranked
    first. Kept pure (no browser) so the parsing can be unit-tested on its own.
    """
    sources: list[WebSource] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        source_url = str(item.get("source_url") or url).strip()
        if not title and not url:
            continue
        domain = _domain(source_url or url)
        tier = _domain_tier(domain)
        if tier < 0:
            continue  # drop video/stock/shop/social-noise domains
        key = (title.lower(), source_url or url)
        if key in seen:
            continue
        seen.add(key)
        has_thumb = bool(item.get("has_thumb"))
        # Rank: rich domains first, then real visual matches (thumbnail).
        score = (2.0 if tier > 0 else 0.0) + (1.0 if has_thumb else 0.0)
        sources.append(
            WebSource(
                title=title,
                url=url,
                source_url=source_url,
                domain=domain,
                match_type="lens_visual_match" if has_thumb else "lens_link",
                score=score,
            )
        )
    sources.sort(key=lambda s: s.score, reverse=True)
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
        profile_dir: str | None = None,
        solve_timeout_ms: int | None = None,
        scraper: Callable[[str | os.PathLike[str]], list[dict[str, Any]]] | None = None,
    ):
        env_headless = os.environ.get("IRIS_LENS_HEADLESS", "1").strip().lower()
        self.headless = headless if headless is not None else env_headless not in {"0", "false", "no"}
        self.timeout_ms = timeout_ms or int(os.environ.get("IRIS_LENS_TIMEOUT_MS", "45000"))
        self.locale = locale or os.environ.get("IRIS_LENS_LOCALE", "en-US")
        # Persistent profile keeps the GOOGLE_ABUSE_EXEMPTION cookie across runs,
        # so the reCAPTCHA only needs to be solved once (semi-manual workflow).
        self.profile_dir = profile_dir if profile_dir is not None else os.environ.get(
            "IRIS_LENS_PROFILE_DIR", ""
        )
        # How long to wait for a human to solve the reCAPTCHA in a headed window.
        self.solve_timeout_ms = solve_timeout_ms or int(
            os.environ.get("IRIS_LENS_SOLVE_TIMEOUT_MS", "180000")
        )
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
        file_path = str(Path(path))
        # Reuse the persistent shared browser across jobs (a new tab per image),
        # instead of opening/closing Chromium every time.
        if shared_session_enabled():
            return get_browser_session().submit(
                lambda ctx: self._scrape_on_context(ctx, file_path)
            )
        from playwright.sync_api import sync_playwright

        with self._stealth(sync_playwright()) as pw:
            browser, context = self._launch(pw)
            try:
                return self._scrape_on_context(context, file_path)
            finally:
                (browser or context).close()

    def _scrape_on_context(self, context: Any, file_path: str) -> list[dict[str, Any]]:
        """Run the Lens flow on a fresh tab of an existing browser context, then
        close the tab (leaving the browser open for the next image)."""
        page = context.new_page()
        try:
            page.set_default_timeout(self.timeout_ms)
            page.goto(self.upload_url, wait_until="domcontentloaded")
            self._dismiss_consent(page)
            self._upload_image(page, file_path)
            self._await_results(page)
            self._settle_results(page)
            return self._extract_results(page)
        finally:
            page.close()

    def _settle_results(self, page: Any) -> None:
        """Let the result anchors populate without blocking on ``networkidle``,
        which Google's pages rarely reach (they keep background connections
        open) -- waiting on it froze the flow for the full timeout."""
        try:
            page.wait_for_selector("a[href^='http']", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

    def _launch(self, pw: Any) -> tuple[Any, Any]:
        """Launch a browser/context. With ``profile_dir`` set, a persistent
        context is used so cookies (notably ``GOOGLE_ABUSE_EXEMPTION`` from a
        solved reCAPTCHA) survive between runs. Returns ``(browser, context)``;
        ``browser`` is ``None`` for the persistent case."""
        viewport = {"width": 1366, "height": 768}
        if self.profile_dir:
            context = pw.chromium.launch_persistent_context(
                self.profile_dir,
                headless=self.headless,
                args=list(self._launch_args),
                user_agent=self._user_agent,
                locale=self.locale,
                viewport=viewport,
            )
            return None, context
        browser = pw.chromium.launch(headless=self.headless, args=list(self._launch_args))
        context = browser.new_context(
            user_agent=self._user_agent,
            locale=self.locale,
            viewport=viewport,
        )
        return browser, context

    def _upload_image(self, page: Any, file_path: str) -> None:
        """Send the local file into Lens. Prefers the visible "upload a file"
        link (the real Lens path), falling back to the legacy reverse-search
        input, then to the last file input.

        The Lens upload dialog renders **asynchronously** after a redirect
        (``lens.google.com/upload`` → ``google.com/?olud``), so we first wait for
        an upload affordance to exist -- otherwise we race the page and miss it.
        """
        # ``encoded_image`` only exists once the full Lens dialog has rendered;
        # the early drag-drop inputs appear before the "upload a file" link, so
        # gating on any file input fires too soon and we miss the real control.
        try:
            page.wait_for_selector(
                "input[name=encoded_image]", state="attached", timeout=self.timeout_ms
            )
        except Exception:
            pass

        for selector in (
            "text=/upload de um arquivo/i",
            "text=/upload a file/i",
            "text=/faça upload/i",
        ):
            link = page.locator(selector)
            try:
                if link.count():
                    with page.expect_file_chooser(timeout=10000) as chooser:
                        link.first.click()
                    chooser.value.set_files(file_path)
                    return
            except Exception:
                continue

        encoded = page.locator("input[name=encoded_image]")
        try:
            if encoded.count():
                encoded.set_input_files(file_path)
                return
        except Exception:
            pass

        inputs = page.locator("input[type=file]")
        if inputs.count():
            inputs.last.set_input_files(file_path)
            return
        raise RuntimeError(
            "Não encontrei o campo de upload do Google Lens (a página pode ter "
            "mudado ou não carregou). Tente novamente."
        )

    def _await_results(self, page: Any) -> None:
        """Wait for the Lens results page. If Google walls with a reCAPTCHA and
        we are headed, pause for a human to solve it (the only free path); in
        headless mode there is nobody to solve it, so fail loudly."""
        try:
            page.wait_for_url("**/search**", timeout=self.timeout_ms)
        except Exception:
            pass
        if "/sorry/" in page.url:
            if self.headless:
                raise RuntimeError(
                    "Google Lens exigiu CAPTCHA (reCAPTCHA por 'tráfego incomum'). "
                    "Rode headed com IRIS_LENS_HEADLESS=0 e IRIS_LENS_PROFILE_DIR=<pasta> "
                    "para resolvê-lo uma vez, ou use IRIS_ENRICHMENT_PROVIDER=serpapi."
                )
            print(
                "[lens] reCAPTCHA detectado: resolva o 'I'm not a robot' na janela "
                "do navegador. Aguardando até "
                f"{self.solve_timeout_ms // 1000}s...",
                flush=True,
            )
            set_window_visible(page, True)  # bring the (hidden) window up for the human
            try:
                page.wait_for_url(
                    lambda url: "/sorry/" not in url and "/search" in url,
                    timeout=self.solve_timeout_ms,
                )
            except Exception as exc:
                raise RuntimeError(
                    "CAPTCHA não foi resolvido a tempo. Aumente IRIS_LENS_SOLVE_TIMEOUT_MS "
                    "ou use IRIS_ENRICHMENT_PROVIDER=serpapi."
                ) from exc
            finally:
                set_window_visible(page, False)  # hide it again
        if "/search" not in page.url:
            raise RuntimeError("Google Lens não retornou página de resultados.")

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
        """Extract the actual visual-match cards from the Lens results page.

        The page is full of non-result links (header, footer, related
        searches, language switch). Real visual matches are anchors that wrap a
        thumbnail ``<img>``, so we prefer those and fall back to plain anchors
        only when too few cards are found. Nav/footer chrome is filtered by a
        stopword list."""
        return page.evaluate(
            """
            () => {
              const STOP = [
                'sign in','sign up','log in','fazer login','settings','configura',
                'privacy','privacidade','terms','termos','gmail','images','imagens',
                'about','sobre','help','ajuda','feedback','advertising','business',
                'how search works','accessibility','your data','more results','mais',
                'related','relacionad','learn more','saiba mais','next','previous',
                'send feedback','google apps','all filters','filters','store','maps'
              ];
              const GOOGLE = /google\\.[a-z.]+|gstatic\\.com|googleusercontent\\.com|youtube\\.com\\/(?:about|t\\/)/;
              const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
              const isNoise = (t) => {
                const l = t.toLowerCase();
                return !t || t.length < 3 || STOP.some((w) => l.includes(w));
              };
              const collect = (requireImg) => {
                const out = [];
                const seen = new Set();
                for (const a of document.querySelectorAll('a[href^="http"]')) {
                  const url = a.href;
                  if (!url || GOOGLE.test(url) || seen.has(url)) continue;
                  if (requireImg && !a.querySelector('img')) continue;
                  const img = a.querySelector('img');
                  let title = clean(a.getAttribute('aria-label'))
                    || clean(img && (img.getAttribute('alt') || img.getAttribute('title')))
                    || clean(a.innerText);
                  if (isNoise(title)) continue;
                  seen.add(url);
                  out.push({ title, url, source_url: url, has_thumb: !!img });
                }
                return out;
              };
              let cards = collect(true);          // visual-match cards (thumbnail)
              if (cards.length < 3) {
                const extra = collect(false);     // fallback: any meaningful link
                const have = new Set(cards.map((c) => c.url));
                for (const e of extra) if (!have.has(e.url)) cards.push(e);
              }
              return cards;
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
    def distill(
        self, sources: list[WebSource], vocabulary: dict[str, list[str]] | None = None
    ) -> EnrichmentSuggestion:
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


_DISTILL_SYSTEM_PROMPT = (
    "You identify an image from Google Lens reverse-search results. "
    "The input is a list of web pages that visually match the image "
    "(titles + domains). Infer what the image actually is.\n"
    "Weight authoritative sources more: knowyourmeme.com, fandom.com, "
    "wikipedia.org, wikia, reddit.com and booru/anime wikis usually name "
    "the character, the source work and the meme directly. Ignore stores, "
    "stock-photo sites and unrelated noise.\n"
    "Decide: who/what is the main character or subject; which work it is "
    "from (anime/game/movie/etc.); whether the image is a known meme and, "
    "if so, what the joke/context is; the visual style; and the most useful "
    "search keywords for this image (including the action or pose, e.g. "
    "'looking up', 'staring', 'pointing').\n"
    "Return ONLY compact JSON with keys: character, source_work, style, "
    "meme_archetype, context, tags, summary, confidence. "
    "IMPORTANT: 'character', 'source_work' and 'meme_archetype' must be SHORT "
    "canonical NAMES only (no descriptions, no parentheses, no 'A vs B'); put any "
    "explanation in 'summary'/'context' instead. "
    "'tags' is 3-8 short keywords, comma-separated (no sentences). "
    "'summary' is one or two sentences in Portuguese explaining what the image is "
    "and why it is a meme. Use empty strings for unknowns and confidence between 0 "
    "and 1 reflecting evidence strength."
)


def format_vocabulary(vocabulary: dict[str, list[str]] | None) -> str:
    """Render the library's existing tags/categories as an instruction so the
    model REUSES them instead of inventing new, divergent ones per image."""
    if not vocabulary:
        return ""
    labels = [
        ("characters", "personagens"),
        ("source_works", "obras"),
        ("meme_archetypes", "arquétipos de meme"),
        ("categories", "outras categorias"),
        ("styles", "estilos"),
        ("tags", "tags"),
    ]
    blocks = [
        f"{label}: {', '.join(vocabulary[key])}"
        for key, label in labels
        if vocabulary.get(key)
    ]
    if not blocks:
        return ""
    return (
        "\n\nVocabulário JÁ existente no acervo — PREFIRA reutilizar estes valores "
        "(mesma grafia) quando se aplicarem; só crie um novo se nenhum servir, para "
        "não multiplicar sinônimos/categorias diferentes para a mesma coisa:\n"
        + "\n".join(blocks)
    )


def gather_vocabulary(
    conn: sqlite3.Connection, *, max_tags: int = 50, max_each: int = 60
) -> dict[str, list[str]]:
    """Collect the library's existing vocabulary (concept names, styles, top
    tags) so the AI reuses it instead of inventing divergent values per image."""
    vocab: dict[str, list[str]] = {
        "characters": [],
        "source_works": [],
        "meme_archetypes": [],
        "categories": [],
        "styles": [],
        "tags": [],
    }
    by_category = {
        "personagem": "characters",
        "obra": "source_works",
        "arquetipo": "meme_archetypes",
    }
    try:
        for row in conn.execute("SELECT name, category FROM concepts ORDER BY name"):
            name = (row["name"] or "").strip()
            if not name:
                continue
            # Legacy concepts (and misc kinds) fall back to a generic bucket.
            key = by_category.get((row["category"] or "").strip().lower(), "categories")
            vocab[key].append(name)
    except Exception:
        pass
    try:
        vocab["styles"] = [
            r[0] for r in conn.execute("SELECT style FROM memes WHERE style != ''") if r[0]
        ]
    except Exception:
        pass
    try:
        counts: dict[str, int] = {}
        for (tag_str,) in conn.execute("SELECT tags FROM memes WHERE tags != ''"):
            for tag in (tag_str or "").split(","):
                tag = tag.strip()
                if tag and tag.lower() != "web-enriched":
                    counts[tag] = counts.get(tag, 0) + 1
        vocab["tags"] = [t for t, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
    except Exception:
        pass
    # Clean each bucket: dedupe (case-insensitive), drop Florence-2 junk and
    # over-long descriptive "names" that aren't reusable vocabulary.
    limits = {"styles": 20, "tags": max_tags}
    return {
        key: _clean_vocab_tokens(
            values,
            max_len=40 if key in {"styles", "tags"} else 60,
            drop_junk=(key == "tags"),
        )[: limits.get(key, max_each)]
        for key, values in vocab.items()
    }


def clean_concept_name(name: str) -> str:
    """Reduce a verbose LLM 'name' to a short canonical one: drop parenthetical
    descriptions, take the first alternative, collapse whitespace, cap length.
    'Frieren: Beyond... (Sousou no Frieren)' -> 'Frieren: Beyond...'."""
    name = re.sub(r"\s+", " ", name or "").strip()
    name = re.sub(r"\s*\(.*$", "", name)  # drop '(...)' descriptions to the end
    name = re.split(r"\s*[/;]\s*| [-–—] ", name)[0]  # first of several alternatives
    return name.strip(" -–—:,.").strip()[:60].strip()


def clean_tag_string(tags: str, *, max_tags: int = 12) -> str:
    """Normalize a comma-separated tag string: drop Florence-2 junk/duplicates,
    cap count -- so we stop persisting garbage tags."""
    parts = [t.strip() for t in (tags or "").replace(";", ",").split(",")]
    return ", ".join(_clean_vocab_tokens(parts, max_len=40, drop_junk=True)[:max_tags])


def _clean_vocab_tokens(values: list[str], *, max_len: int, drop_junk: bool) -> list[str]:
    """Dedupe (case-insensitive), normalize whitespace, drop empties / over-long
    values and (for tags) Florence-2 caption artifacts like ``VQA>...<loc_0>``."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = re.sub(r"\s+", " ", value or "").strip()
        if not value or len(value) > max_len:
            continue
        if drop_junk:
            low = value.lower()
            if "<" in value or ">" in value or "loc_" in low or "vqa" in low:
                continue
            if low in {"n/a", "na", "none", "null"}:
                continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def build_distill_messages(
    sources: list[WebSource], vocabulary: dict[str, list[str]] | None = None
) -> tuple[str, str]:
    """Build the (system, user) prompt from the *clean relevant text* only --
    page titles + domains, never raw HTML -- so token usage stays minimal and
    every backend (API or web-chat) receives the exact same compact payload."""
    user = json.dumps(
        {
            "matches": [
                {
                    "title": source.title,
                    "domain": source.domain,
                    "match_type": source.match_type,
                }
                for source in sources[:15]
            ]
        },
        ensure_ascii=False,
    )
    return _DISTILL_SYSTEM_PROMPT, user + format_vocabulary(vocabulary)


def _extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of an LLM/web-chat reply, tolerating markdown
    code fences and surrounding prose."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            return json.loads(match.group(0))
    raise ValueError("Resposta sem JSON")


@runtime_checkable
class LLMBackend(Protocol):
    """Transport that turns the clean prompt into a model reply. Implementations
    differ only in *where* the text is sent (API vs web chat)."""

    name: str

    def available(self) -> bool: ...
    def complete(
        self,
        system: str,
        user: str,
        sources: list[WebSource] | None = None,
        vocabulary: dict[str, list[str]] | None = None,
    ) -> str: ...


class OpenAICompatBackend:
    """Direct API call to any OpenAI-compatible Chat Completions endpoint
    (ChatGPT, Ollama, LM Studio, ...). Stable and recommended."""

    name = "openai"

    def __init__(self, *, endpoint: str = "", api_key: str = "", model: str = "", timeout: int = 45):
        self.endpoint = endpoint or "https://api.openai.com/v1/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.endpoint and self.api_key and self.model)

    def complete(
        self,
        system: str,
        user: str,
        sources: list[WebSource] | None = None,
        vocabulary: dict[str, list[str]] | None = None,
    ) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
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
        with request.urlopen(req, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return _llm_content(data)


class GeminiAPIBackend:
    """Direct call to the Google Gemini ``generateContent`` REST API."""

    name = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, *, api_key: str = "", model: str = "", timeout: int = 45):
        self.api_key = api_key
        self.model = model or "gemini-2.0-flash"
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key and self.model)

    def complete(
        self,
        system: str,
        user: str,
        sources: list[WebSource] | None = None,
        vocabulary: dict[str, list[str]] | None = None,
    ) -> str:
        url = f"{self.base_url}/{self.model}:generateContent?key={parse.quote(self.api_key)}"
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        req = request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"content-type": "application/json"},
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            raise ValueError("Gemini sem texto")
        return text


_WEBCHAT_INSTRUCTION = (
    "Identifique a imagem a partir destes resultados de busca reversa do Google "
    "Lens (cada item é um título e a URL da página que casou visualmente). Abra "
    "com a busca web as fontes mais confiáveis (knowyourmeme, fandom, wikipedia, "
    "reddit), leia, e descubra: personagem/sujeito, obra de origem, se é um meme "
    "conhecido e qual o contexto/piada, o estilo, e palavras-chave (inclusive a "
    "pose/ação, ex.: 'olhando para cima', 'encarando'). Responda APENAS com um "
    "JSON com as chaves: character, source_work, style, meme_archetype, context, "
    "tags, summary, confidence. IMPORTANTE: character, source_work e meme_archetype "
    "devem ser NOMES curtos e canônicos (sem descrições, sem parênteses, sem 'A vs "
    "B') — coloque explicações em summary/context. 'tags' = 3 a 8 palavras-chave "
    "curtas; 'summary' em português explicando o que é e por que é meme.\n\nFontes:\n"
)


def build_webchat_url(
    sources: list[WebSource],
    *,
    temporary: bool = True,
    max_chars: int = 6000,
    vocabulary: dict[str, list[str]] | None = None,
) -> str:
    """Build a chatgpt.com deep link that pre-fills the compact prompt + the top
    matches (title + URL) + a trimmed copy of the existing vocabulary. The number
    of matches is trimmed until the whole URL stays under ``max_chars`` so it
    never trips a proxy's 414 (URI too long)."""
    base = "https://chatgpt.com/"
    usable = [s for s in sources if s.url]
    # Keep the vocabulary small for the URL (the size limit lives here).
    caps = {
        "characters": 25,
        "source_works": 20,
        "meme_archetypes": 15,
        "categories": 15,
        "styles": 15,
        "tags": 20,
    }
    trimmed = (
        {k: (vocabulary.get(k) or [])[:cap] for k, cap in caps.items()} if vocabulary else None
    )
    vocab_full = format_vocabulary(trimmed)

    def make(count: int, vocab_txt: str) -> str:
        lines = "\n".join(f"- {s.title} | {s.url}" for s in usable[:count])
        params = {"q": _WEBCHAT_INSTRUCTION + lines + vocab_txt, "hints": "search"}
        if temporary:
            params["temporary-chat"] = "true"
        return base + "?" + parse.urlencode(params)

    # 1) Trim matches down to a small floor first.
    count = min(len(usable), 8)
    url = make(count, vocab_full)
    while count > 3 and len(url) > max_chars:
        count -= 1
        url = make(count, vocab_full)
    # 2) Still over budget? Shrink the vocabulary (it grows unbounded with the
    #    library, so it -- not the matches -- is what blows the URL past 414).
    vocab_txt = vocab_full
    while vocab_txt and len(make(count, vocab_txt)) > max_chars:
        vocab_txt = vocab_txt[: int(len(vocab_txt) * 0.8)].rstrip()
    if vocab_txt != vocab_full and vocab_txt:
        vocab_txt += " …"
    return make(count, vocab_txt)


class WebChatBackend:
    """Drive a logged-in ChatGPT in a dedicated browser profile, end to end:
    open a deep link that pre-fills the prompt, auto-submit, wait for the reply,
    capture it. Free and uses the user's account; fragile by nature (depends on
    chatgpt.com's DOM and a one-time login), so it needs live calibration.

    Connection (decided with the user): a **dedicated persistent profile** is the
    default -- the user logs into ChatGPT once and it is reused. ``cdp_url`` can
    instead attach to the user's own running Chrome. The DOM interaction is
    isolated in ``_send_deeplink`` and can be swapped via ``completer`` for tests.
    """

    name = "webchat"
    _input = "#prompt-textarea"
    _launch_args = ("--disable-blink-features=AutomationControlled", "--no-sandbox")
    _answer = "[data-message-author-role='assistant']"
    # While streaming, a "stop" button is shown; its absence means the reply is done.
    _stop = "button[data-testid='stop-button'], button[aria-label*='Stop'], button[aria-label*='Parar']"

    def __init__(
        self,
        *,
        target: str = "chatgpt",
        cdp_url: str = "",
        headless: bool | None = None,
        profile_dir: str = "",
        channel: str | None = None,
        temporary: bool | None = None,
        timeout_ms: int | None = None,
        completer: Callable[[str], str] | None = None,
    ):
        self.target = (target or "chatgpt").strip().lower()
        self.cdp_url = cdp_url or os.environ.get("IRIS_WEBCHAT_CDP", "")
        # Web chat must run headed (login + far less bot-flagging).
        env_headless = os.environ.get("IRIS_WEBCHAT_HEADLESS", "0").strip().lower()
        self.headless = headless if headless is not None else env_headless not in {"0", "false", "no"}
        # Same profile as the shared session, so login persists across both modes.
        self.profile_dir = profile_dir or os.environ.get(
            "IRIS_WEBCHAT_PROFILE_DIR", SHARED_PROFILE_DIR
        )
        # Real Chrome passes chatgpt.com's Cloudflare check; the bundled Chromium
        # usually gets walled. Default to the system Chrome, fall back to Chromium.
        self.channel = (
            channel if channel is not None else os.environ.get("IRIS_WEBCHAT_CHANNEL", "chrome")
        )
        env_temp = os.environ.get("IRIS_WEBCHAT_TEMPORARY", "1").strip().lower()
        self.temporary = temporary if temporary is not None else env_temp not in {"0", "false", "no"}
        self.timeout_ms = timeout_ms or int(os.environ.get("IRIS_WEBCHAT_TIMEOUT_MS", "120000"))
        # How long to wait for the user to log in / pass Cloudflare in the window.
        self.login_timeout_ms = int(os.environ.get("IRIS_WEBCHAT_LOGIN_TIMEOUT_MS", "300000"))
        self._completer = completer

    def available(self) -> bool:
        if self._completer is not None:
            return True
        if self.target != "chatgpt":
            return False  # only ChatGPT has a working URL prefill
        try:
            import playwright.sync_api  # noqa: F401
        except Exception:
            return False
        return True

    def complete(
        self,
        system: str,
        user: str,
        sources: list[WebSource] | None = None,
        vocabulary: dict[str, list[str]] | None = None,
    ) -> str:
        url = build_webchat_url(sources or [], temporary=self.temporary, vocabulary=vocabulary)
        if self._completer is not None:
            return self._completer(url)
        return self._send_deeplink(url)

    def _send_deeplink(self, url: str) -> str:
        # Reuse the persistent shared browser (same window/profile as Lens) when
        # enabled and not attaching to the user's own Chrome via CDP.
        if not self.cdp_url and shared_session_enabled():
            return get_browser_session().submit(
                lambda ctx: self._send_on_context(ctx, url)
            )
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            context, owns = self._connect(pw)
            try:
                return self._send_on_context(context, url)
            finally:
                if owns:
                    context.close()

    def _send_on_context(self, context: Any, url: str) -> str:
        page = context.new_page()
        try:
            page.set_default_timeout(self.timeout_ms)
            # 1) Open the base site and make sure we're logged in and past any
            #    Cloudflare check *before* sending the prompt (a logged-out
            #    redirect would drop the prefilled ?q=).
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            self._ensure_ready(page)
            # 2) Now open the deep link, which pre-fills the prompt, and submit.
            page.goto(url, wait_until="domcontentloaded")
            editor = page.locator(self._input).first
            editor.wait_for(timeout=self.timeout_ms)
            page.wait_for_timeout(1500)  # let the prefill text settle
            editor.click()
            page.keyboard.press("Enter")
            self._wait_answer(page)
            answers = page.locator(self._answer)
            if not answers.count():
                raise RuntimeError("ChatGPT não retornou resposta.")
            return answers.last.inner_text().strip()
        finally:
            page.close()

    def _ensure_ready(self, page: Any) -> None:
        """Make sure the chat composer is usable. If it isn't, the user most
        likely needs to log in (or clear a Cloudflare check) in the visible
        window -- so we warn and wait, instead of failing immediately."""
        try:
            page.wait_for_selector(self._input, timeout=15000)
            return
        except Exception:
            pass
        if self.headless:
            raise RuntimeError(
                "ChatGPT não está pronto (login ou verificação Cloudflare). Rode com a "
                "janela visível (IRIS_WEBCHAT_HEADLESS=0) e faça login uma vez no perfil "
                "dedicado (IRIS_WEBCHAT_PROFILE_DIR)."
            )
        print(
            "[webchat] Faça login no ChatGPT (e resolva qualquer verificação) na janela "
            f"aberta. Aguardando até {self.login_timeout_ms // 1000}s... O login fica salvo "
            "no perfil dedicado, então só é pedido uma vez.",
            flush=True,
        )
        set_window_visible(page, True)  # bring the (hidden) window up for the human
        try:
            page.wait_for_selector(self._input, timeout=self.login_timeout_ms)
        except Exception as exc:
            raise RuntimeError(
                "Login no ChatGPT não foi concluído a tempo. Tente novamente e faça login "
                "na janela, ou use um backend de API (IRIS_LLM_BACKEND=openai/gemini)."
            ) from exc
        finally:
            set_window_visible(page, False)  # hide it again once logged in

    def _wait_answer(self, page: Any) -> None:
        """Wait until an answer appears and streaming has stopped."""
        page.wait_for_selector(self._answer, timeout=self.timeout_ms)
        try:
            # The stop button shows while streaming; wait for it to disappear.
            page.wait_for_selector(self._stop, state="detached", timeout=self.timeout_ms)
        except Exception:
            pass
        page.wait_for_timeout(1500)

    def _connect(self, pw: Any) -> tuple[Any, bool]:
        if self.cdp_url:
            browser = pw.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            return context, False  # never close the user's own Chrome
        os.makedirs(self.profile_dir, exist_ok=True)
        # Prefer the real Chrome channel (passes Cloudflare); fall back to the
        # bundled Chromium if Chrome is not installed.
        for channel in ([self.channel] if self.channel else []) + [None]:
            try:
                kwargs: dict[str, Any] = {
                    "headless": self.headless,
                    "args": list(self._launch_args),
                }
                if channel:
                    kwargs["channel"] = channel
                context = pw.chromium.launch_persistent_context(self.profile_dir, **kwargs)
                return context, True
            except Exception:
                continue
        raise RuntimeError("Não foi possível abrir o navegador para o web-chat.")


class LLMDistiller:
    """Distiller backed by a pluggable :class:`LLMBackend`. Builds the clean
    prompt, asks the backend, parses JSON, and falls back to the heuristic on
    any failure or when the backend is not configured."""

    def __init__(self, backend: LLMBackend | None, fallback: HeuristicDistiller | None = None):
        self.backend = backend
        self.fallback = fallback or HeuristicDistiller()

    def distill(
        self, sources: list[WebSource], vocabulary: dict[str, list[str]] | None = None
    ) -> EnrichmentSuggestion:
        fallback = self.fallback.distill(sources)
        if self.backend is None:
            return fallback
        if not self.backend.available():
            # Backend selected but not usable (e.g. missing key, no login) -- make
            # the silent fallback visible instead of returning generic data.
            reason = f"backend '{self.backend.name}' indisponível (config/login?)"
            print(f"[distill] {reason}; usando heurística.", flush=True)
            return replace(fallback, error_message=f"IA: {reason}")
        try:
            system, user = build_distill_messages(sources, vocabulary)
            parsed = _extract_json(self.backend.complete(system, user, sources, vocabulary))
            return EnrichmentSuggestion(
                provider=f"llm:{self.backend.name}",
                # Normalize the LLM's verbose output so we stop persisting
                # full-sentence "names" and junk tags as concepts/tags.
                character=clean_concept_name(str(parsed.get("character") or fallback.character)),
                source_work=clean_concept_name(str(parsed.get("source_work") or fallback.source_work)),
                style=str(parsed.get("style") or fallback.style),
                meme_archetype=clean_concept_name(
                    str(parsed.get("meme_archetype") or fallback.meme_archetype)
                ),
                context=str(parsed.get("context") or fallback.context),
                tags=clean_tag_string(str(parsed.get("tags") or fallback.tags)),
                summary=str(parsed.get("summary") or fallback.summary),
                confidence=float(parsed.get("confidence") or fallback.confidence),
                sources=fallback.sources,
            )
        except Exception as exc:
            # Don't fail silently: log why and tag the fallback so the UI shows it.
            detail = f"{type(exc).__name__}: {exc}".strip()
            print(
                f"[distill] backend '{self.backend.name}' falhou ({detail}); usando heurística.",
                flush=True,
            )
            return replace(fallback, error_message=f"IA ({self.backend.name}) falhou: {detail}")


class HybridDistiller(LLMDistiller):
    """Back-compat shim: an :class:`LLMDistiller` wired to an OpenAI-compatible
    backend from the legacy ``IRIS_LLM_*`` environment variables."""

    def __init__(self, fallback: HeuristicDistiller | None = None):
        backend = OpenAICompatBackend(
            endpoint=os.environ.get("IRIS_LLM_ENDPOINT", "").strip(),
            api_key=os.environ.get("IRIS_LLM_API_KEY", "").strip(),
            model=os.environ.get("IRIS_LLM_MODEL", "").strip(),
        )
        super().__init__(backend, fallback)


def build_distiller(overrides: dict[str, str] | None = None) -> HeuristicDistiller | LLMDistiller:
    """Select the distiller/backend from ``overrides`` (e.g. from the UI) with
    fallback to ``IRIS_*`` env vars. ``IRIS_LLM_BACKEND`` picks the transport:
    ``heuristic`` (default), ``openai``, ``gemini`` or ``webchat``."""
    over = overrides or {}

    def cfg(key: str, env: str) -> str:
        return str(over.get(key) or os.environ.get(env, "")).strip()

    kind = (cfg("backend", "IRIS_LLM_BACKEND") or "heuristic").lower()
    if kind in {"", "heuristic", "none", "off"}:
        # Legacy: if OpenAI-compatible env is fully set, honour it transparently.
        if all(os.environ.get(k, "").strip() for k in
               ("IRIS_LLM_ENDPOINT", "IRIS_LLM_API_KEY", "IRIS_LLM_MODEL")):
            return HybridDistiller()
        return HeuristicDistiller()
    if kind in {"openai", "chatgpt", "openai_compat"}:
        backend: LLMBackend = OpenAICompatBackend(
            endpoint=cfg("endpoint", "IRIS_LLM_ENDPOINT"),
            api_key=cfg("api_key", "IRIS_LLM_API_KEY"),
            model=cfg("model", "IRIS_LLM_MODEL") or "gpt-4o-mini",
        )
    elif kind == "gemini":
        backend = GeminiAPIBackend(
            api_key=cfg("api_key", "IRIS_LLM_API_KEY"),
            model=cfg("model", "IRIS_LLM_MODEL") or "gemini-2.0-flash",
        )
    elif kind in {"webchat", "web", "browser"}:
        temp_raw = cfg("temporary", "IRIS_WEBCHAT_TEMPORARY")
        temporary = None if temp_raw == "" else temp_raw not in {"0", "false", "no"}
        backend = WebChatBackend(
            target=cfg("target", "IRIS_WEBCHAT_TARGET") or "chatgpt",
            cdp_url=cfg("cdp", "IRIS_WEBCHAT_CDP"),
            temporary=temporary,
        )
    else:
        return HeuristicDistiller()
    return LLMDistiller(backend)


class WebEnrichmentService:
    def __init__(
        self,
        provider: ReverseImageProvider | None = None,
        distiller: Distiller | None = None,
        *,
        publisher: S3TemporaryImagePublisher | None = None,
        backend_overrides: dict[str, str] | None = None,
    ):
        if provider is None:
            provider = build_reverse_image_provider()
            # A custom S3 publisher only applies to the SerpApi path.
            if publisher is not None and isinstance(provider, SerpApiLensProvider):
                provider.publisher = publisher
        self.provider = provider
        self.distiller = distiller or build_distiller(backend_overrides)

    def missing_config(self) -> list[str]:
        return list(self.provider.missing_config())

    def enrich_path(
        self,
        path: str | os.PathLike[str],
        vocabulary: dict[str, list[str]] | None = None,
    ) -> EnrichmentSuggestion:
        """Full pipeline: reverse-image search (Lens) + distill."""
        sources = self.provider.search_path(path)
        return self._distill(sources, provider=self.provider.provider_name, vocabulary=vocabulary)

    def redistill(
        self,
        sources: list[WebSource],
        vocabulary: dict[str, list[str]] | None = None,
    ) -> EnrichmentSuggestion:
        """Re-run only the AI distiller over sources already found earlier --
        no new Lens search. Lets the user re-send the same matches to a
        different/better backend without re-searching (and without re-opening
        the browser)."""
        return self._distill(sources, provider="redistill", vocabulary=vocabulary)

    def _distill(
        self,
        sources: list[WebSource],
        *,
        provider: str,
        vocabulary: dict[str, list[str]] | None = None,
    ) -> EnrichmentSuggestion:
        suggestion = self.distiller.distill(sources, vocabulary)
        return EnrichmentSuggestion(
            provider=provider,
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


def load_existing_sources(conn: sqlite3.Connection, meme_id: int) -> list[WebSource]:
    """Load the web sources from the latest suggestion of a meme, so we can
    re-run only the AI distiller without hitting Google Lens again."""
    create_web_enrichment_tables(conn)
    existing = find_existing_suggestion(conn, meme_id)
    if not existing:
        return []
    rows = conn.execute(
        "SELECT title, url, source_url, domain, match_type, score "
        "FROM web_enrichment_sources WHERE suggestion_id = ? ORDER BY id",
        (existing["id"],),
    ).fetchall()
    return [
        WebSource(
            title=row["title"],
            url=row["url"],
            source_url=row["source_url"],
            domain=row["domain"],
            match_type=row["match_type"],
            score=row["score"],
        )
        for row in rows
    ]


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
        ("source_work", "obra"),
        ("meme_archetype", "arquetipo"),
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
    # Titles from sources that tend to name the character/work get more weight.
    authoritative = ("knowyourmeme", "fandom", "wikipedia", "wikia", "booru", "myanimelist")
    counts: dict[str, float] = {}
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
                weight = 3.0 if any(a in source.domain for a in authoritative) else 1.0
                counts[normalized] = counts.get(normalized, 0.0) + weight
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
