"""Iris FastAPI application.

Serves the SPA shell and a REST JSON API consumed by vanilla JavaScript modules.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

# Ensure core/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from core import app_config, import_review
from core import backup as backup_mod
from core.api_models import (
    BackupConfigOut,
    BackupSnapshotsOut,
    CollectionFromSuggestionOut,
    CollectionMembersAddedOut,
    CollectionMembersOut,
    CollectionsOut,
    ConceptAssociationsOut,
    ConceptCreatedOut,
    ConceptMatchesOut,
    ConceptReferencesOut,
    ConceptsOut,
    ConceptWithReferencesOut,
    DbIdsOut,
    DuplicatesOut,
    EnrichmentJobStartedOut,
    EnrichmentSuggestionsOut,
    FilesystemOut,
    HealthOut,
    ImportReviewOut,
    ImportStartedOut,
    ImportStatusOut,
    ImportSuggestionsOut,
    OkOut,
    PersonMediaOut,
    PersonsOut,
    RecordDetailOut,
    RecordFacesOut,
    RecordMetadataOut,
    RecordsPageOut,
    SearchResponseOut,
    ServerInfoOut,
    SourceSearchResponseOut,
    TrashOut,
    UploadSearchResponseOut,
)
from core.auth import load_or_create_secret
from core.backend import SearchBackend, create_backend
from core.backend_registry import BackendRegistry
from core.device_tokens import read_access_token
from core.file_ops import move_to_trash
from core.media_metadata import extract_full_metadata, extract_metadata
from core.observability import configure_logging, request_path
from core.perf import dump, trace
from core.search_engine import DEFAULT_MODEL, IMAGE_EXTENSIONS, LOW_RESOURCE_MODEL, VIDEO_EXTENSIONS
from core.search_types import IndexRecord, SearchOptions, SearchResult, normalize_text
from core.users_db import IrisUser, get_device, get_user_by_id, has_users
from core.web_enrichment import (
    EnrichmentSuggestion,
    WebEnrichmentService,
    apply_suggestion,
    count_cached_ids,
    create_job,
    create_web_enrichment_tables,
    find_existing_suggestion,
    gather_vocabulary,
    get_job,
    insert_suggestion,
    list_suggestions,
    load_existing_sources,
    reject_suggestion,
    update_job,
)
from routers.auth import router as auth_router
from routers.sync import router as sync_router

# ── Constants ─────────────────────────────────────────────────────────────────
configure_logging()
logger = logging.getLogger("iris")

_DATA_DIR = Path(os.environ.get("IRIS_DATA_DIR", "data"))
_THUMB_DIR = _DATA_DIR / "thumbnails"


def _default_db_path() -> str:
    """Resolve the default catalog path.

    Iris-branded name going forward, but if only the legacy ``meme_compass`` catalog
    exists we keep using it so the rebrand never orphans an existing library.
    """
    env = os.environ.get("IRIS_DB")
    if env:
        return env
    iris = str(_DATA_DIR / "iris_v1.db")
    legacy = str(_DATA_DIR / "meme_compass_full_v1.db")
    if not os.path.exists(iris) and os.path.exists(legacy):
        return legacy
    return iris


_DEFAULT_DB = _default_db_path()
_MEDIA_ROOT = os.environ.get("IRIS_MEDIA_ROOT", "media")
_LOAD_MODEL = os.environ.get("IRIS_LOAD_MODEL", "1").lower() not in {"0", "false", "no"}
_USERS_DB = _DATA_DIR / "users.db"


def _require_search_model() -> None:
    """Return a clear API error when semantic features were intentionally disabled."""
    if not _LOAD_MODEL:
        raise HTTPException(409, "Semantic search is disabled because IRIS_LOAD_MODEL=0")


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


# Defaults deliberately fit a household's high-resolution photo/video imports,
# while still stopping a single request from exhausting the host by accident.
_MAX_UPLOAD_BYTES = _positive_env_int("IRIS_MAX_UPLOAD_BYTES", 32 * 1024**3)
_MAX_UPLOAD_FILES = _positive_env_int("IRIS_MAX_UPLOAD_FILES", 10_000)
_ACCOUNT_QUOTA_BYTES = _positive_env_int("IRIS_ACCOUNT_QUOTA_BYTES", 10 * 1024**4)
_MAX_IMAGE_PIXELS = _positive_env_int("IRIS_MAX_IMAGE_PIXELS", 500_000_000)
_MAX_REQUEST_BYTES = _positive_env_int("IRIS_MAX_REQUEST_BYTES", 64 * 1024**3)
_MAX_SEARCH_TOP_K = _positive_env_int("IRIS_MAX_SEARCH_TOP_K", 1_000)
_MAX_SEARCH_CANDIDATES = _positive_env_int("IRIS_MAX_SEARCH_CANDIDATES", 20_000)

# Request-local state is deliberately separate from the legacy globals below. A
# library backend must never be selected by a process-wide mutable variable when
# multiple authenticated requests are executing concurrently.
_request_backend: contextvars.ContextVar[SearchBackend | None] = contextvars.ContextVar(
    "iris_request_backend", default=None
)
_request_user: contextvars.ContextVar[IrisUser | None] = contextvars.ContextVar(
    "iris_request_user", default=None
)


def _search_worker_count() -> int:
    """Keep CPU search responsive without oversubscribing small machines."""
    try:
        configured = int(os.environ.get("IRIS_SEARCH_WORKERS", "2"))
    except ValueError:
        configured = 2
    return max(1, min(configured, os.cpu_count() or 1))


_search_executor = ThreadPoolExecutor(
    max_workers=_search_worker_count(), thread_name_prefix="iris-search"
)

# ── Backend singleton ─────────────────────────────────────────────────────────
_backend: SearchBackend | None = None
_backend_lock = threading.RLock()
_active_config: dict[str, Any] = {
    "db_path": _DEFAULT_DB,
    "media_root": _MEDIA_ROOT,
    "model_name": os.environ.get("IRIS_MODEL", DEFAULT_MODEL),
    "load_model": _LOAD_MODEL,
}
_import_job: dict[str, Any] = {
    "id": None,
    "status": "idle",
    "done": 0,
    "total": 0,
    "imported": 0,
    "quarantined": 0,
    "current": "",
    "message": "",
    "started_at": None,
    "finished_at": None,
}

# "Import anyway" queue: per-item clicks from the review panel accumulate here and a
# single worker drains them in coalesced batches. Lets the user click through many
# items without waiting for one import to finish (no more 409 between clicks).
_forced_queue: list[str] = []
_forced_lock = threading.Lock()
_forced_worker_running = False

# View-level memoization. Records are immutable between backend reloads, so the
# expensive part of /api/records — sorting the whole library — is cached per
# (sort_by, sort_asc). Filters (media type / collection / concept) stay live on
# each request so membership changes are never served stale. Cleared on reload.
_sorted_records_cache: dict[tuple[str, str, int], list[IndexRecord]] = {}
# /api/info missing-file scan is O(N) syscalls; cache by (db_path, total_records).
_missing_count_cache: dict[tuple[str, int], int] = {}


def _invalidate_view_caches() -> None:
    _sorted_records_cache.clear()
    _missing_count_cache.clear()


def _import_db() -> sqlite3.Connection:
    """Short-lived connection to the active DB for import_jobs / import_review ops."""
    engine = getattr(_get_backend(), "engine", None)
    db_path = getattr(engine, "db_path", None)
    if db_path:
        return sqlite3.connect(str(db_path))
    return sqlite3.connect(str(_active_config["db_path"]))


def _get_backend() -> SearchBackend:
    request_backend = _request_backend.get()
    if request_backend is not None:
        return request_backend
    if _backend is None:
        raise HTTPException(503, "Backend not initialised yet")
    return _backend


def _current_user() -> IrisUser | None:
    return _request_user.get()


def _multiuser_enabled() -> bool:
    return bool(getattr(app.state, "multiuser_enabled", False)) if "app" in globals() else False


def _setup_required() -> bool:
    return bool(getattr(app.state, "setup_required", False)) if "app" in globals() else False


def _thumbnail_dir() -> Path:
    user = _current_user()
    if user is None:
        return _THUMB_DIR
    return user.db_path.parent / "thumbnails"


def _library_usage_bytes(root: Path) -> int:
    try:
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    except OSError:
        return 0


async def _write_upload_limited(upload: UploadFile, destination: Path, remaining_bytes: int) -> int:
    """Persist one upload with a per-file and per-library budget."""
    written = 0
    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES or written > remaining_bytes:
                    raise HTTPException(413, "Upload excede o limite configurado da biblioteca")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return written


def _enforce_uploaded_file_size(upload: UploadFile) -> None:
    position = upload.file.tell()
    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(position)
    if size > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Arquivo excede o limite configurado")


def _open_uploaded_image(upload: UploadFile):
    """Decode an image only after bounding decompression work and dimensions."""
    from PIL import Image

    try:
        _enforce_uploaded_file_size(upload)
        upload.file.seek(0)
        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload.file)
            if image.width * image.height > _MAX_IMAGE_PIXELS:
                raise HTTPException(413, "Imagem excede o limite de pixels configurado")
            return image.convert("RGB")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Não foi possível abrir a imagem: {exc}") from exc


def _require_admin() -> IrisUser | None:
    """Require an Iris administrator only after multi-user mode is bootstrapped."""
    if not _multiuser_enabled():
        return None
    user = _current_user()
    if user is None or not user.is_admin:
        raise HTTPException(403, "Apenas administradores podem executar esta ação")
    return user


async def _run_search(callable_: Any, *args: Any) -> Any:
    """Run blocking model/FAISS work away from FastAPI's event-loop thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_search_executor, callable_, *args)


def _reload_backend(config: dict[str, Any] | None = None) -> SearchBackend:
    global _backend
    previous_config = dict(_active_config)
    if config:
        _active_config.update(config)
    try:
        with _backend_lock:
            # Ensure the full core schema (memes/collections/concepts/…) exists
            # before opening the backend. Without this, launching the server on a
            # fresh DB (before any indexing) leaves it without a `memes` table,
            # which crashes the engine on the next start. init_db is idempotent.
            from core.indexer_db import init_db
            init_db(Path(_active_config["db_path"])).close()
            backend = create_backend(
                db_path=str(_active_config["db_path"]),
                media_root=str(_active_config["media_root"]),
                model_name=str(_active_config["model_name"]),
                load_model=bool(_active_config["load_model"]),
            )
            _ensure_web_enrichment_tables(backend)
            _backend = backend
            _invalidate_view_caches()
            return backend
    except Exception:
        _active_config.clear()
        _active_config.update(previous_config)
        raise


def _available_databases() -> list[str]:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        (path.as_posix() for path in _DATA_DIR.rglob("*.db")),
        reverse=True,
    )


def _backend_connection(backend: SearchBackend | None = None):
    backend = backend or _get_backend()
    engine = getattr(backend, "engine", None)
    if engine is None:
        raise HTTPException(503, "Backend local indisponível para enriquecimento web")
    return engine.db.get_connection()


def _ensure_web_enrichment_tables(backend: SearchBackend | None = None) -> None:
    try:
        conn = _backend_connection(backend)
    except HTTPException:
        return
    create_web_enrichment_tables(conn)
    engine = getattr(backend or _get_backend(), "engine", None)
    if engine is not None:
        engine.db.invalidate_table_cache()


def _create_web_enrichment_service(
    backend_overrides: dict[str, str] | None = None,
) -> WebEnrichmentService:
    return WebEnrichmentService(backend_overrides=backend_overrides)


def _refresh_backend_metadata() -> None:
    backend = _get_backend()
    engine = getattr(backend, "engine", None)
    if engine is None:
        return
    engine.db.invalidate_table_cache()
    engine.records = engine._load_records()
    engine._dados_cache = None


def _open_folder_in_file_manager(path: Path) -> None:
    if sys.platform.startswith("darwin"):
        subprocess.Popen(["open", str(path)])
        return
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    subprocess.Popen(["xdg-open", str(path)])


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Kill browsers leaked by a previous run (a crash that skipped shutdown);
    # they hold the profile lock and would block a fresh launch.
    try:
        from core.browser_session import kill_orphan_browsers

        killed = kill_orphan_browsers()
        if killed:
            print(f"[iris] Limpou {killed} navegador(es) orfao(s) de execucao anterior")
    except Exception:
        pass
    if app.state.multiuser_enabled:
        print("[iris] Ready — private libraries enabled")
    elif _backend is None:
        print(f"[iris] Loading backend — DB: {_active_config['db_path']}")
        backend = _reload_backend()
        print(f"[iris] Ready — {backend.get_total_records()} records")
    else:
        backend = _backend
        print(f"[iris] Ready — {backend.get_total_records()} records")
    if not app.state.multiuser_enabled:
        _resume_unfinished_imports()
    yield
    dump()
    try:
        from core.browser_session import close_browser_session

        close_browser_session()
    except Exception:
        pass
    _search_executor.shutdown(wait=False, cancel_futures=True)
    print("[iris] Shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan, title="Iris")
app.state.data_dir = _DATA_DIR
app.state.users_db_path = _USERS_DB
app.state.auth_secret = load_or_create_secret(_DATA_DIR / "secret_key")
app.state.account_quota_bytes = _ACCOUNT_QUOTA_BYTES
app.state.load_model = _LOAD_MODEL
_server_mode = os.environ.get("IRIS_SERVER_MODE", "legacy").lower()
_private_server_requested = _server_mode in {"private", "multiuser"}
_has_users = has_users(_USERS_DB)
app.state.multiuser_enabled = _private_server_requested or (
    os.environ.get("IRIS_MULTIUSER", "auto").lower() not in {"0", "false", "no"}
    and _has_users
)
app.state.setup_required = app.state.multiuser_enabled and not _has_users
if app.state.multiuser_enabled:
    try:
        engine_cache_size = int(os.environ.get("IRIS_ENGINE_CACHE_SIZE", "1"))
    except ValueError:
        engine_cache_size = 1
    app.state.backend_registry = BackendRegistry(
        _USERS_DB, cache_size=engine_cache_size, load_model=_LOAD_MODEL
    )


@app.middleware("http")
async def authenticate_library_request(request: Request, call_next):
    """Bind every protected request to exactly one private-library backend."""
    if not request.app.state.multiuser_enabled:
        return await call_next(request)
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > _MAX_REQUEST_BYTES:
        return Response(status_code=413, content='{"detail":"Requisição excede o limite configurado"}', media_type="application/json")
    path = request.url.path
    public = (
        path in {"/login", "/setup", "/healthz", "/favicon.ico", "/api/auth/login", "/api/auth/devices/login", "/api/auth/devices/refresh"}
        or path.startswith("/static/")
    )
    if public:
        return await call_next(request)
    if _setup_required():
        if path.startswith("/api/"):
            return Response(status_code=503, content='{"detail":"Configuração inicial necessária"}', media_type="application/json")
        return RedirectResponse("/setup", status_code=303)
    session = request.session
    user_id = session.get("user_id")
    session_version = session.get("session_version")
    device_id = None
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        payload = read_access_token(request.app.state.auth_secret, authorization[7:].strip())
        if payload:
            user_id = payload.get("user_id")
            session_version = payload.get("session_version")
            device_id = payload.get("device_id")
            device = get_device(request.app.state.users_db_path, str(device_id)) if device_id else None
            if device is None or device.revoked_at or device.user_id != user_id or device.token_version != payload.get("token_version"):
                user_id = None
    user = get_user_by_id(request.app.state.users_db_path, int(user_id)) if isinstance(user_id, int) else None
    if user is None or user.session_version != session_version:
        session.clear()
        if path.startswith("/api/"):
            return Response(status_code=401, content='{"detail":"Autenticação necessária"}', media_type="application/json")
        return RedirectResponse("/login", status_code=303)
    # These legacy endpoints operate on host-global paths/configuration. They
    # must not be reachable from a private-library deployment until they are
    # redesigned around an account-owned destination.
    if path.startswith("/api/backup/") or path in {"/api/settings", "/api/filesystem", "/api/open-folder"}:
        return Response(status_code=404, content='{"detail":"Indisponível em bibliotecas privadas"}', media_type="application/json")
    try:
        backend = request.app.state.backend_registry.get(user.id)
    except KeyError:
        session.clear()
        return Response(status_code=401, content='{"detail":"Sessão inválida"}', media_type="application/json")
    request.state.iris_user = user
    request.state.iris_device_id = device_id
    request.state.backend = backend
    backend_token = _request_backend.set(backend)
    user_token = _request_user.set(user)
    try:
        return await call_next(request)
    finally:
        _request_user.reset(user_token)
        _request_backend.reset(backend_token)


@app.middleware("http")
async def log_request(request: Request, call_next):
    """Emit one safe, correlated line for every HTTP request.

    Request content, query parameters, cookies and authorization headers are
    intentionally omitted: they may contain a password, a search for private
    media, or a session token.
    """
    request_id = uuid.uuid4().hex[:16]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "http_request_failed",
            extra={
                "event": "http_request_failed",
                "request_id": request_id,
                "method": request.method,
                "path": request_path(request.url.path),
            },
        )
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    access_enabled = os.environ.get("IRIS_LOG_ACCESS", "1").lower() not in {"0", "false", "no"}
    if access_enabled or response.status_code >= 400:
        user = getattr(request.state, "iris_user", None)
        logger.info(
            "http_request_completed",
            extra={
                "event": "http_request_completed",
                "request_id": request_id,
                "method": request.method,
                "path": request_path(request.url.path),
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "user_id": user.id if user is not None else None,
            },
        )
    return response

# Compress JSON/HTML responses (records pages can be sizeable). Skips small bodies
# and is a no-op for already-compressed media/thumbnails (own content types).
app.add_middleware(GZipMiddleware, minimum_size=1024)
if app.state.multiuser_enabled:
    # Must be outermost so the authentication middleware can read request.session.
    app.add_middleware(
        SessionMiddleware,
        secret_key=app.state.auth_secret,
        session_cookie="iris_session",
        max_age=60 * 60 * 24 * 14,
        same_site="lax",
        https_only=os.environ.get("IRIS_SESSION_HTTPS_ONLY", str(_private_server_requested)).lower()
        in {"1", "true", "yes", "on"},
    )

# Static assets
static_dir = Path(__file__).parent / "static"
template_dir = Path(__file__).parent / "templates"
static_dir.mkdir(parents=True, exist_ok=True)
template_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.include_router(auth_router)
app.include_router(sync_router)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _options_from_params(
    top_k: int = 50,
    threshold: float = 0.15,
    balance: float = 0.5,
    text_bonus: float = 1.0,
    lexical_weight: float = 0.25,
    translate: bool = True,
    candidate_pool: int = 3000,
    media_type: str = "all",
    collection_ids: str = "",
    concept_ids: str = "",
) -> SearchOptions:
    def _parse_ints(raw: str) -> frozenset[int]:
        if not raw.strip():
            return frozenset()
        return frozenset(int(x) for x in raw.split(",") if x.strip().isdigit())

    return SearchOptions(
        top_k=max(1, min(int(top_k), _MAX_SEARCH_TOP_K)),
        threshold=max(-1.0, min(float(threshold), 1.0)),
        balance=balance,
        text_bonus=text_bonus,
        lexical_weight=lexical_weight,
        translate=translate,
        candidate_pool=max(1, min(int(candidate_pool), _MAX_SEARCH_CANDIDATES)),
        media_type=media_type,
        collection_ids=_parse_ints(collection_ids),
        concept_ids=_parse_ints(concept_ids),
    )


def _record_to_json(r: IndexRecord) -> dict[str, Any]:
    """IndexRecord → JSON, excluding heavy numpy arrays."""
    ext = os.path.splitext(r.resolved_path or r.caminho)[1].lower()
    return {
        "index": r.index,
        "db_id": r.db_id,
        "arquivo": r.arquivo,
        "resolved_path": r.resolved_path,
        "texto_extraido": r.texto_extraido,
        "descricao_ia": r.descricao_ia,
        "tags": r.tags,
        "visual_json": r.visual_json,
        "objects": r.objects,
        "style": r.style,
        "source_work": r.source_work,
        "humor": r.humor,
        "context": r.context,
        "content_hash": r.content_hash,
        "file_size": r.file_size,
        "file_mtime": r.file_mtime,
        "media_type": "video" if ext in VIDEO_EXTENSIONS else "image",
        "thumbnail_url": _thumbnail_url(r),
        # Inline placeholder so a client can paint the cell before the
        # thumbnail request finishes. See core/thumb_hash.py.
        "thumb_hash": r.thumb_hash,
    }


def _result_to_json(r: SearchResult) -> dict[str, Any]:
    base = _record_to_json(_get_backend().get_record(r.index) or _empty_record(r))
    base["score"] = round(r.score, 4)
    base["score_details"] = r.score_details
    return base


def _attach_persons(dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bulk-attach the persons present in each media (single query, no N+1)."""
    backend = _get_backend()
    persons_map: dict[int, list[dict]] = {}
    try:
        if backend.has_face_tables():
            ids = [d["db_id"] for d in dicts if d.get("db_id")]
            if ids:
                persons_map = backend.get_media_persons(ids)
    except Exception:
        persons_map = {}
    for d in dicts:
        d["persons"] = persons_map.get(d.get("db_id") or 0, [])
    return dicts


async def _results_to_json(results: list[SearchResult]) -> list[dict[str, Any]]:
    # Same reasoning as get_records: _result_to_json can decode/resize a source
    # file on a thumbnail-cache miss, which must not run on the event loop.
    return await run_in_threadpool(
        lambda: _attach_persons([_result_to_json(r) for r in results])
    )


def _empty_record(r: SearchResult) -> IndexRecord:
    return IndexRecord(
        index=r.index, arquivo=r.arquivo, caminho=r.caminho,
        resolved_path=r.resolved_path, texto_extraido=r.texto_extraido,
        descricao_ia=r.descricao_ia, tags=r.tags,
        embedding=np.zeros(1, dtype=np.float32),
        desc_embedding=None,
    )


# ── Thumbnail helpers ───────────────────────────────────────────────────────────

_THUMB_SIZE = (300, 300)
_THUMB_QUALITY = 75

# Cache TTL curto (path -> (expiry_ts, url)) para evitar re-stat do mesmo arquivo
# quando a galeria pré-busca páginas vizinhas em rajada. A TTL se auto-invalida,
# então mudanças no arquivo aparecem em segundos (o miss recomputa a chave real).
_THUMB_URL_TTL = 5.0
_THUMB_URL_CACHE_MAX = 100_000
_thumb_url_cache: dict[str, tuple[float, str]] = {}


def _thumbnail_url(r: IndexRecord) -> str:
    """Compute thumbnail URL for a gallery record."""
    return _thumbnail_url_from_path(r.resolved_path)


def _thumbnail_url_from_path(fp: str) -> str:
    """Return thumbnail URL, generating on-the-fly if missing.

    Uses md5(path:mtime:size) as cache key so thumbnails survive renames
    but invalidate when the source file changes.
    """
    if not fp:
        return ""
    cached = _thumb_url_cache.get(fp)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    url = _compute_thumbnail_url(fp)
    if len(_thumb_url_cache) > _THUMB_URL_CACHE_MAX:
        _thumb_url_cache.clear()
    _thumb_url_cache[fp] = (now + _THUMB_URL_TTL, url)
    return url


def _compute_thumbnail_url(fp: str) -> str:
    if not os.path.exists(fp):
        return ""
    try:
        stat = os.stat(fp)
        key = hashlib.md5(
            f"{fp}:{stat.st_mtime}:{stat.st_size}".encode()
        ).hexdigest()
        thumb_dir = _thumbnail_dir()
        thumb = thumb_dir / f"{key}.jpg"

        if not thumb.exists():
            thumb_dir.mkdir(parents=True, exist_ok=True)
            if _multiuser_enabled():
                os.chmod(thumb_dir, 0o700)
            ext = os.path.splitext(fp)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                _generate_video_thumbnail(fp, thumb)
            else:
                _generate_image_thumbnail(fp, thumb)

        return f"/thumbs/{key}.jpg" if thumb.exists() else ""
    except Exception as exc:
        # Corrupt/unreadable source, decode error or thumbnail write failure —
        # degrade to "no thumbnail" so the gallery still renders, but make the
        # failure visible instead of swallowing it silently.
        logger.warning("thumbnail generation failed for %s: %s", fp, exc)
        return ""


def _generate_video_thumbnail(video_path: str, thumb_path: Path) -> None:
    """Extract a non-blank frame from video_path and save as JPEG thumbnail.

    Tries frames at start, 1/4, and 1/2 positions. Skips blank/solid-color frames
    (mean < 8 = all-black, mean > 247 = all-white, std < 12 = uniform color).
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            return
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return

        frame = None
        for pos in (0, total // 4, total // 2):
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(pos, 0))
            ok, frm = cap.read()
            if ok and not _is_blank_frame(frm):
                frame = frm
                break

        if frame is None:
            return

        from PIL import Image
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        img.thumbnail(_THUMB_SIZE, Image.LANCZOS)
        img.save(str(thumb_path), format="JPEG", quality=_THUMB_QUALITY, optimize=True)
    finally:
        cap.release()


def _generate_image_thumbnail(image_path: str, thumb_path: Path) -> None:
    """Resize an image file and save as JPEG thumbnail."""
    from PIL import Image
    img = Image.open(image_path).convert("RGB")
    img.thumbnail(_THUMB_SIZE, Image.LANCZOS)
    img.save(str(thumb_path), format="JPEG", quality=_THUMB_QUALITY, optimize=True)


def _is_blank_frame(frame) -> bool:
    """Return True if frame is all-black, all-white, or a uniform solid color."""
    import cv2
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    std = float(gray.std())
    # All-black (mean < 8), all-white (mean > 247), or uniform/solid (std < 12)
    return bool(mean < 8.0 or mean > 247.0 or std < 12.0)


def _guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")


def _group_search_results(
    results: list[SearchResult],
    similarity_threshold: float,
) -> list[list[SearchResult]]:
    if not results:
        return []
    backend = _get_backend()
    embeddings: list[np.ndarray] = []
    valid_results: list[SearchResult] = []
    for result in results:
        record = backend.get_record(result.index)
        if record is None or record.embedding is None:
            continue
        vector = np.asarray(record.embedding, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm <= 0:
            continue
        embeddings.append(vector / norm)
        valid_results.append(result)
    if not valid_results:
        return [[result] for result in results]

    parents = list(range(len(valid_results)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parents[root_right] = root_left

    matrix = np.stack(embeddings)
    similarities = matrix @ matrix.T
    for left in range(len(valid_results)):
        for right in range(left + 1, len(valid_results)):
            if float(similarities[left, right]) >= similarity_threshold:
                union(left, right)

    grouped: dict[int, list[SearchResult]] = {}
    for position, result in enumerate(valid_results):
        grouped.setdefault(find(position), []).append(result)
    return list(grouped.values())


def _concept_reference_to_json(reference: dict[str, Any]) -> dict[str, Any]:
    import base64

    thumbnail = reference.get("thumbnail")
    return {
        "id": reference.get("id"),
        "label": reference.get("label", ""),
        "added_at": reference.get("added_at"),
        "thumbnail": base64.b64encode(thumbnail).decode("ascii") if thumbnail else "",
    }


def _store_concept_reference(
    backend: SearchBackend,
    concept_id: int,
    upload: UploadFile,
) -> None:
    from core.concepts import make_thumbnail

    image = _open_uploaded_image(upload)
    thumbnail = make_thumbnail(image)
    embedding = np.asarray(backend.encode_image(image), dtype=np.float32).reshape(-1).tobytes()
    backend.add_reference(concept_id, embedding, thumbnail, upload.filename or "")


def _record_for_db_id(db_id: int) -> IndexRecord | None:
    return next((record for record in _get_backend().get_all_records() if record.db_id == db_id), None)


def _set_import_progress(done: int, total: int, current: str) -> None:
    _import_job.update(done=done, total=total, current=current)


def _run_import_job(
    job_id: str,
    sources: list[Path],
    settings: dict[str, Any],
    cleanup: Path | None,
    *,
    explicit_files: list[Path] | None = None,
    dedup_enabled: bool = True,
) -> None:
    try:
        from core.indexer import (
            IndexerConfig,
            create_faiss_indices,
            process_images,
            resolve_device,
        )

        _import_job.update(
            id=job_id,
            status="running",
            done=0,
            total=0,
            imported=0,
            quarantined=0,
            current="",
            message="Carregando modelos e preparando importação.",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
        )
        with _import_db() as conn:
            import_review.update_job(conn, job_id, status="running", message="Carregando modelos…")
        engine = getattr(_get_backend(), "engine", None)
        db_path = Path(str(getattr(engine, "db_path", _active_config["db_path"])))
        total_imported = 0
        total_quarantined = 0
        for source in sources:
            config = IndexerConfig(
                media_dir=source,
                db_path=db_path,
                model_name=str(settings["model_name"]),
                batch_size=int(settings["batch_size"]),
                device=resolve_device(str(settings["device"])),
                recursive=bool(settings["recursive"]),
                limit=None,
                rebuild_faiss_only=False,
                caption_model=str(settings["caption_model"]),
                whisper_model=str(settings["whisper_model"]),
                sample_manifest=None,
                library_name=str(settings["library_name"]),
                library_root=Path(str(settings["library_root"])),
                copy_to_library=bool(settings["copy_to_library"]),
                extract_faces=not bool(settings.get("low_resource", False)),
            )
            result = process_images(
                config,
                progress_callback=_set_import_progress,
                job_id=job_id,
                dedup_enabled=dedup_enabled,
                explicit_files=explicit_files,
            )
            total_imported += int(result.get("imported", 0))
            total_quarantined += int(result.get("quarantined", 0))
        create_faiss_indices(db_path, str(settings["model_name"]))
        if _multiuser_enabled():
            user = _current_user()
            if user is not None:
                app.state.backend_registry.invalidate(user.id)
        else:
            _reload_backend({"model_name": str(settings["model_name"])})
        done_msg = (
            f"Importação concluída: {total_imported} nova(s), "
            f"{total_quarantined} em revisão."
        )
        finished = datetime.now(timezone.utc).isoformat()
        _import_job.update(
            status="completed",
            imported=total_imported,
            quarantined=total_quarantined,
            message=done_msg,
            finished_at=finished,
        )
        with _import_db() as conn:
            import_review.update_job(
                conn, job_id, status="completed", imported=total_imported,
                quarantined=total_quarantined, message=done_msg, finished_at=finished,
            )
    except BaseException as exc:
        finished = datetime.now(timezone.utc).isoformat()
        # An unavailable source (folder unmounted / disappeared) is *pausable*, not a
        # failure: keep the job resumable so it picks up where it stopped once the
        # folder is back (on restart, or when the user re-imports it).
        paused = type(exc).__name__ == "ImportSourceUnavailable"
        if paused:
            msg = "Pasta de origem inacessível (desmontada?). Importação pausada — será retomada quando a pasta voltar."
            _import_job.update(status="interrupted", message=msg, finished_at=finished)
            try:
                with _import_db() as conn:
                    import_review.update_job(
                        conn, job_id, status="interrupted", error_message=msg,
                    )
            except Exception:
                pass
        else:
            _import_job.update(status="failed", message=str(exc), finished_at=finished)
            try:
                with _import_db() as conn:
                    import_review.update_job(
                        conn, job_id, status="failed", error_message=str(exc), finished_at=finished,
                    )
            except Exception:
                pass
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)


def _resume_unfinished_imports() -> None:
    """Re-launch an import left running/queued by a previous (crashed) process.

    Relies on the indexer's skip-sets (already-imported memes + queued review rows)
    so it picks up where it stopped without re-importing. Only folder sources that
    still exist can resume; upload temp dirs are gone after a restart.
    """
    try:
        with _import_db() as conn:
            jobs = import_review.unfinished_jobs(conn)
    except Exception:
        return
    for job in jobs:
        try:
            raw_sources = [Path(s) for s in json.loads(job.get("source") or "[]")]
            settings = json.loads(job.get("settings") or "{}")
        except Exception:
            raw_sources, settings = [], {}
        present = [s for s in raw_sources if s.exists() and s.is_dir()]
        if not raw_sources or not settings:
            # No source/settings recorded → genuinely unrecoverable.
            with _import_db() as conn:
                import_review.update_job(
                    conn, job["id"], status="failed",
                    error_message="Sem origem/configuração para retomar.",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
            continue
        if not present:
            # Sources configured but not mounted yet → wait, don't fail. The job stays
            # 'interrupted' so a later restart (or re-import of the folder) resumes it.
            with _import_db() as conn:
                import_review.update_job(
                    conn, job["id"], status="interrupted",
                    error_message="Aguardando a pasta de origem ficar acessível para retomar.",
                )
            _import_job.update(
                id=job["id"], status="interrupted",
                imported=int(job.get("imported", 0)),
                quarantined=int(job.get("quarantined", 0)),
                message="Importação pausada: aguardando a pasta de origem ficar acessível.",
            )
            print(f"[iris] Importação {job['id'][:8]} aguardando pasta acessível")
            continue
        _import_job.update(
            id=job["id"], status="queued", done=int(job.get("done", 0)),
            total=int(job.get("total", 0)), imported=int(job.get("imported", 0)),
            quarantined=int(job.get("quarantined", 0)),
            message="Retomando importação interrompida.",
        )
        print(f"[iris] Retomando importação interrompida {job['id'][:8]}")
        threading.Thread(
            target=_run_import_job,
            args=(job["id"], present, settings, None),
            daemon=True,
            name=f"iris-import-{job['id'][:8]}",
        ).start()
        break  # only one import runs at a time


# ── Page routes ───────────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def serve_login():
    if _setup_required():
        return RedirectResponse("/setup", status_code=303)
    if not _multiuser_enabled():
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        (static_dir / "login.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/setup", response_class=HTMLResponse)
async def serve_setup():
    if not _setup_required():
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        "<main><h1>Iris precisa da primeira conta</h1>"
        "<p>Crie a primeira conta no servidor. Em uma instalação vazia, o comando cria uma biblioteca privada vazia; "
        "em uma instalação antiga, ele migra automaticamente o catálogo e a mídia existentes.</p>"
        "<pre>docker compose run --rm iris python scripts/bootstrap_admin.py --username administrador</pre>"
        "</main>",
        status_code=503,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/healthz", response_model=HealthOut)
async def healthz():
    """Unauthenticated liveness/readiness probe; never reveals library details."""
    return {
        "status": "setup_required" if _setup_required() else "ok",
        "mode": "multiuser" if _multiuser_enabled() else "legacy",
    }


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return HTMLResponse(
        (template_dir / "index.html").read_text(),
        headers={"Cache-Control": "no-store"},
    )


# ── Info ──────────────────────────────────────────────────────────────────────


def _missing_count(backend: SearchBackend, total: int) -> int:
    """Count records whose file is gone. O(N) syscalls — cached per (db, total)."""
    engine = getattr(backend, "engine", None)
    key = (str(getattr(engine, "db_path", _active_config["db_path"])), total)
    cached = _missing_count_cache.get(key)
    if cached is not None:
        return cached
    count = sum(
        1 for r in backend.get_all_records()
        if not r.resolved_path or not os.path.exists(r.resolved_path)
    )
    _missing_count_cache[key] = count
    return count


@app.get("/api/info", response_model=ServerInfoOut)
async def get_info(check_missing: int = Query(0)):
    backend = _get_backend()
    with trace("api.info"):
        # extension_counts is pure in-memory (cheap). The missing-file scan does
        # one stat() per record, so it's opt-in (the stats panel asks for it) and
        # cached — the sidebar's frequent /api/info stays syscall-free.
        records = backend.get_all_records()
        extension_counts: dict[str, int] = {}
        for record in records:
            extension = Path(record.arquivo).suffix.lower() or "(sem extensão)"
            extension_counts[extension] = extension_counts.get(extension, 0) + 1
        total = backend.get_total_records()
        missing_count = None
        if check_missing:
            missing_count = 0 if not records else await run_in_threadpool(
                _missing_count, backend, total
            )
        user = _current_user()
        private_library = _multiuser_enabled()
        return {
            "total_records": total,
            "db_path": "" if user else str(_active_config["db_path"]),
            "media_root": "" if user else str(_active_config["media_root"]),
            "model_name": user.model_name if user else str(_active_config["model_name"]),
            "load_model": bool(_active_config["load_model"]),
            "multiuser": _multiuser_enabled(),
            "current_user": (
                {"username": user.username, "display_name": user.display_name, "is_admin": user.is_admin}
                if user else None
            ),
            "has_concepts": backend.has_concept_tables(),
            "has_faces": backend.has_face_tables(),
            "missing_count": missing_count,
            "extension_counts": extension_counts,
            "databases": [] if user else _available_databases(),
            "capabilities": {
                "semantic_search": _LOAD_MODEL,
                "image_search": _LOAD_MODEL,
                "face_search": _LOAD_MODEL and backend.has_face_tables(),
                "host_administration": not private_library,
                "folder_import": not private_library,
                "host_backup": not private_library,
                "open_host_folder": not private_library,
                "webchat_enrichment": not private_library,
            },
        }


@app.post("/api/settings", response_model=OkOut)
async def update_settings(
    db_path: str = Form(...),
    media_root: str = Form(...),
    model_name: str = Form(DEFAULT_MODEL),
):
    _require_admin()
    if _multiuser_enabled():
        raise HTTPException(409, "As bibliotecas privadas não podem trocar de caminho pela interface")
    candidate_db = Path(db_path)
    if not candidate_db.is_absolute() and candidate_db.parts[:1] != (_DATA_DIR.name,):
        candidate_db = _DATA_DIR / candidate_db
    if candidate_db.suffix.lower() != ".db":
        raise HTTPException(400, "O banco precisa ser um arquivo .db")
    candidate_db.parent.mkdir(parents=True, exist_ok=True)
    backend = await run_in_threadpool(
        _reload_backend,
        {
            "db_path": str(candidate_db),
            "media_root": str(Path(media_root).expanduser()),
            "model_name": model_name.strip() or DEFAULT_MODEL,
        },
    )
    return {"ok": True, "total_records": backend.get_total_records()}


@app.post("/api/reload", response_model=OkOut)
async def reload_backend():
    _require_admin()
    if _multiuser_enabled():
        user = _current_user()
        assert user is not None
        app.state.backend_registry.invalidate(user.id)
        backend = await run_in_threadpool(app.state.backend_registry.get, user.id)
        return {"ok": True, "total_records": backend.get_total_records()}
    backend = await run_in_threadpool(_reload_backend)
    return {"ok": True, "total_records": backend.get_total_records()}


@app.get("/api/filesystem", response_model=FilesystemOut)
async def browse_filesystem(path: str = Query("")):
    _require_admin()
    current = Path(path).expanduser() if path.strip() else Path.home()
    try:
        current = current.resolve()
        if not current.exists() or not current.is_dir():
            raise HTTPException(404, "Pasta não encontrada")
        directories = sorted(
            (
                {"name": child.name, "path": str(child)}
                for child in current.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            ),
            key=lambda item: item["name"].lower(),
        )
    except PermissionError as exc:
        raise HTTPException(403, "Sem permissão para listar esta pasta") from exc
    return {
        "path": str(current),
        "parent": str(current.parent),
        "directories": directories,
    }


@app.post("/api/open-folder", response_model=OkOut)
async def open_folder(path: str = Form(...)):
    _require_admin()
    target = Path(path).expanduser()
    try:
        target = target.resolve()
    except OSError as exc:
        raise HTTPException(400, "Caminho inválido") from exc
    if target.is_file():
        target = target.parent
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "Pasta não encontrada")
    try:
        _open_folder_in_file_manager(target)
    except FileNotFoundError as exc:
        raise HTTPException(500, "Gerenciador de arquivos não encontrado neste ambiente") from exc
    except OSError as exc:
        raise HTTPException(500, f"Não foi possível abrir a pasta: {exc}") from exc
    return {"ok": True, "path": str(target)}


@app.get("/api/import/status", response_model=ImportStatusOut)
async def import_status():
    if _multiuser_enabled():
        try:
            with _import_db() as conn:
                job = import_review.latest_job(conn)
            if job:
                return {
                    "id": job["id"], "status": job["status"], "done": job["done"],
                    "total": job["total"], "imported": job["imported"],
                    "quarantined": job["quarantined"], "current": "", "message": job["message"],
                }
        except Exception:
            pass
        return {"id": None, "status": "idle", "done": 0, "total": 0, "imported": 0, "quarantined": 0, "current": "", "message": ""}
    if _import_job.get("id"):
        return dict(_import_job)
    # No in-memory job (e.g. fresh process): hydrate from the persisted job row.
    try:
        with _import_db() as conn:
            job = import_review.latest_job(conn)
        if job:
            return {
                "id": job["id"], "status": job["status"], "done": job["done"],
                "total": job["total"], "imported": job["imported"],
                "quarantined": job["quarantined"], "current": "",
                "message": job["message"],
            }
    except Exception:
        pass
    return dict(_import_job)


@app.post("/api/import", response_model=ImportStartedOut)
async def start_import(
    folder: str = Form(""),
    files: Annotated[list[UploadFile] | None, File()] = None,
    recursive: bool = Form(True),
    library_name: str = Form("default"),
    library_root: str = Form("data/library"),
    copy_to_library: bool = Form(True),
    batch_size: int = Form(8),
    device: str = Form("auto"),
    caption_model: str = Form("microsoft/Florence-2-large"),
    whisper_model: str = Form("tiny"),
    low_resource: bool = Form(False),
):
    # Development/load-lab mode deliberately avoids a hidden model download or
    # heavyweight indexer process after accepting a multipart request. FastAPI
    # has already parsed the body at this point, so request-size/network limits
    # can still be exercised without unexpectedly consuming CPU/RAM in a fast
    # local sandbox.
    if not _LOAD_MODEL:
        raise HTTPException(409, "Indexing is disabled because IRIS_LOAD_MODEL=0")
    if _multiuser_enabled():
        with _import_db() as conn:
            if any(job["status"] in {"queued", "running"} for job in import_review.unfinished_jobs(conn)):
                raise HTTPException(409, "Já existe uma importação em andamento nesta biblioteca")
    elif _import_job["status"] in {"queued", "running"}:
        raise HTTPException(409, "Já existe uma importação em andamento")
    if batch_size < 1 or batch_size > 64:
        raise HTTPException(400, "Batch size deve ficar entre 1 e 64")
    if device not in {"auto", "cuda", "mps", "cpu"}:
        raise HTTPException(400, "Dispositivo inválido")

    sources: list[Path] = []
    if _multiuser_enabled() and folder.strip():
        raise HTTPException(403, "Importe arquivos pelo envio; pastas do servidor são restritas")
    if folder.strip():
        source = Path(folder).expanduser().resolve()
        if not source.exists() or not source.is_dir():
            raise HTTPException(400, "Pasta de importação não encontrada")
        sources.append(source)

    cleanup: Path | None = None
    if files:
        if len(files) > _MAX_UPLOAD_FILES:
            raise HTTPException(413, "Quantidade de arquivos excede o limite configurado")
        user = _current_user()
        upload_root = (user.db_path.parent / "import_uploads") if user else (_DATA_DIR / "import_uploads")
        upload_root.mkdir(parents=True, exist_ok=True)
        cleanup = Path(tempfile.mkdtemp(prefix="iris-", dir=upload_root))
        used_bytes = _library_usage_bytes(user.media_root) if user else 0
        remaining_bytes = max(0, _ACCOUNT_QUOTA_BYTES - used_bytes)
        try:
            for upload in files:
                filename = Path(upload.filename or "upload").name
                destination = cleanup / filename
                written = await _write_upload_limited(upload, destination, remaining_bytes)
                remaining_bytes -= written
        except Exception:
            shutil.rmtree(cleanup, ignore_errors=True)
            raise
        sources.append(cleanup)

    if not sources:
        raise HTTPException(400, "Escolha uma pasta ou envie arquivos")

    # Reversibility: snapshot the catalog before a (re)index changes it.
    await run_in_threadpool(maybe_auto_snapshot, "pre-import")

    job_id = uuid.uuid4().hex
    user = _current_user()
    model_name = LOW_RESOURCE_MODEL if low_resource else (user.model_name if user else str(_active_config["model_name"]))
    settings = {
        "recursive": recursive,
        "library_name": "media" if user else (library_name.strip() or "default"),
        "library_root": str(user.media_root.parent) if user else (library_root.strip() or "data/library"),
        "copy_to_library": True if user else copy_to_library,
        "batch_size": 1 if low_resource else batch_size,
        "device": "cpu" if low_resource else device,
        "caption_model": "none" if low_resource else caption_model.strip() or "none",
        "whisper_model": "none" if low_resource else whisper_model.strip() or "none",
        "model_name": model_name,
        "low_resource": low_resource,
    }
    _import_job.update(
        id=job_id, status="queued", done=0, total=0, imported=0, quarantined=0,
        message="Importação na fila.",
    )
    with _import_db() as conn:
        import_review.create_job(
            conn, job_id, json.dumps([str(s) for s in sources]), json.dumps(settings)
        )
    context = contextvars.copy_context()
    threading.Thread(
        target=context.run,
        args=(_run_import_job, job_id, sources, settings, cleanup),
        daemon=True,
        name=f"iris-import-{job_id[:8]}",
    ).start()
    return {"ok": True, "job_id": job_id}


# ── Import review (deduplication quarantine) ────────────────────────────────────


@app.get("/api/import/review", response_model=ImportReviewOut)
async def import_review_list(
    detection: str = Query(""),
    limit: int = Query(200),
    offset: int = Query(0),
):
    with _import_db() as conn:
        counts = import_review.counts_by_detection(conn)
        items = import_review.list_items(
            conn, detection=detection or None, limit=max(1, min(limit, 500)), offset=max(0, offset)
        )
    records_by_id = {r.db_id: r for r in _get_backend().get_all_records()}
    enriched = []
    for item in items:
        match = records_by_id.get(item.get("match_meme_id")) if item.get("match_meme_id") else None
        enriched.append(
            {
                "id": item["id"],
                "detection": item["detection"],
                "candidate_path": item["candidate_path"],
                "candidate_filename": os.path.basename(item["candidate_path"]),
                "candidate_thumb_url": (
                    f"/api/import/review/{item['id']}/thumb" if item["has_thumb"] else ""
                ),
                "candidate_full_url": f"/api/import/review/{item['id']}/full",
                "match_meme_id": item.get("match_meme_id"),
                "match_filename": os.path.basename(match.arquivo) if match else "",
                "match_thumb_url": _thumbnail_url(match) if match else "",
                "match_full_url": (
                    "/media/" + match.resolved_path.lstrip("/") if match and match.resolved_path else ""
                ),
                "score": round(float(item["score"]), 4),
            }
        )
    categories = [
        {
            "detection": d,
            "label": import_review.DETECTION_LABELS.get(d, d),
            "count": counts.get(d, 0),
        }
        for d in import_review.DETECTIONS
        if counts.get(d, 0)
    ]
    return {"categories": categories, "total": sum(counts.values()), "items": enriched}


@app.get("/api/import/suggestions", response_model=ImportSuggestionsOut)
async def import_suggestions(job_id: str = Query("")):
    """Collection suggestions from the metadata of the most-recent import (or job_id)."""
    from core import import_suggestions as suggest_mod

    with _import_db() as conn:
        job = import_review.get_job(conn, job_id) if job_id else import_review.latest_job(conn)
        since = (job or {}).get("created_at", "")
        suggestions = suggest_mod.suggest_collections(conn, since=since) if since else []
    return {"job_id": (job or {}).get("id", ""), "suggestions": suggestions}


@app.post("/api/collections/from-suggestion", response_model=CollectionFromSuggestionOut)
async def create_collection_from_suggestion(
    name: str = Form(...),
    db_ids: str = Form(...),
):
    """Create (or reuse) a collection by name and add the suggested members to it."""
    backend = _get_backend()
    name = name.strip()
    ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
    if not name or not ids:
        raise HTTPException(400, "Nome e itens são obrigatórios.")
    with trace("api.collections.from_suggestion"):
        existing = {c["name"]: c["id"] for c in backend.list_collections()}
        col_id = existing.get(name)
        if col_id is None:
            col_id = backend.engine.create_collection(name)
        added = backend.add_records_to_collection(ids, col_id)
        return {"collection_id": col_id, "name": name, "added": added}


@app.get("/api/import/review/{item_id}/thumb")
async def import_review_thumb(item_id: int):
    with _import_db() as conn:
        blob = import_review.get_thumb(conn, item_id)
    if not blob:
        raise HTTPException(404, "Sem miniatura")
    return Response(content=blob, media_type="image/jpeg")


@app.get("/api/import/review/{item_id}/full")
async def import_review_full(item_id: int):
    """Serve the candidate file at full size for the review lightbox.

    The candidate isn't in the DB (it's a pending import), so it can't go through
    /media. Serve it straight from its recorded path; if the file is gone (e.g. the
    source was unmounted), fall back to the stored thumbnail so the panel still works.
    """
    with _import_db() as conn:
        row = conn.execute(
            "SELECT candidate_path FROM import_review WHERE id = ?", (item_id,)
        ).fetchone()
        path = row[0] if row else None
        if path and os.path.exists(path):
            return FileResponse(path, media_type=_guess_mime(path))
        blob = import_review.get_thumb(conn, item_id)
    if not blob:
        raise HTTPException(404, "Arquivo indisponível")
    return Response(content=blob, media_type="image/jpeg")


@app.post("/api/import/review/resolve", response_model=OkOut)
async def import_review_resolve(
    ids: str = Form(""),
    detection: str = Form(""),
    action: str = Form("ignore"),
):
    if action not in {"ignore", "trash", "import"}:
        raise HTTPException(400, "Ação inválida")
    if _multiuser_enabled() and action == "import":
        # The legacy coalesced queue is process-global. Do not let it cross a
        # library boundary; a per-library queue will replace it in a follow-up.
        raise HTTPException(409, "Importar itens em revisão ainda não está disponível em bibliotecas privadas")
    with _import_db() as conn:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        if detection and not id_list:
            id_list = import_review.ids_for_detection(conn, detection)
        items = import_review.items_for_ids(conn, id_list)
        if not items:
            raise HTTPException(404, "Nenhum item pendente encontrado")
        item_ids = [it["id"] for it in items]

        if action == "ignore":
            resolved = import_review.mark_resolved(conn, item_ids, "ignored")
            # Remember the decision so re-imports skip these instantly.
            import_review.ledger_mark_paths(conn, [it["candidate_path"] for it in items], "ignored")
            return {"ok": True, "resolved": resolved}

        if action == "trash":
            moved, failed = move_to_trash([it["candidate_path"] for it in items])
            resolved = import_review.mark_resolved(conn, item_ids, "trashed")
            import_review.ledger_mark_paths(conn, [it["candidate_path"] for it in items], "trashed")
            return {"ok": True, "resolved": resolved, "moved": len(moved), "failed": len(failed)}

    # action == "import": queue the candidates for force-indexing (dedup off).
    # Enqueuing (not launching one job per click) is what lets the user click
    # "import anyway" through many items in a row without waiting / hitting 409.
    paths = [Path(it["candidate_path"]) for it in items if Path(it["candidate_path"]).exists()]
    if not paths:
        raise HTTPException(400, "Nenhum arquivo disponível para importar")
    with _import_db() as conn:
        import_review.mark_resolved(conn, item_ids, "imported")
        import_review.ledger_mark_paths(conn, [str(p) for p in paths], "imported")
    depth = _enqueue_forced_import(paths)
    return {"ok": True, "imported_files": len(paths), "queued": depth}


def _enqueue_forced_import(paths: list[Path]) -> int:
    """Queue files to be force-indexed (dedup off) by the single drain worker.

    Returns the queue depth after enqueuing. Rapid per-item "import anyway" clicks
    coalesce into a few batches instead of one blocking job per click.
    """
    global _forced_worker_running
    started = False
    with _forced_lock:
        _forced_queue.extend(str(p) for p in paths)
        depth = len(_forced_queue)
        if not _forced_worker_running:
            _forced_worker_running = True
            started = True
    if started:
        threading.Thread(
            target=_forced_import_worker, daemon=True, name="iris-forced-import"
        ).start()
    return depth


def _forced_import_worker() -> None:
    """Drain the forced-import queue in coalesced batches, one batch at a time."""
    global _forced_worker_running
    try:
        while True:
            # Debounce: let a burst of clicks pile up so they index together.
            time.sleep(1.2)
            with _forced_lock:
                batch = list(dict.fromkeys(_forced_queue))  # dedupe, keep order
                _forced_queue.clear()
                if not batch:
                    _forced_worker_running = False
                    return
            # Defer to a normal (non-forced) import if one is in flight.
            while _import_job.get("status") in {"queued", "running"} and not _import_job.get("forced"):
                time.sleep(0.5)
            paths = [Path(p) for p in batch if Path(p).exists()]
            if paths:
                _run_forced_import_batch(paths)
    finally:
        with _forced_lock:
            _forced_worker_running = False


def _run_forced_import_batch(paths: list[Path]) -> None:
    """Synchronously force-index a batch of files even if flagged as duplicates."""
    job_id = uuid.uuid4().hex
    try:
        common = Path(os.path.commonpath([str(p) for p in paths]))
        media_dir = common if common.is_dir() else common.parent
    except Exception:
        media_dir = paths[0].parent
    settings = {
        "recursive": False,
        "library_name": "default",
        "library_root": "data/library",
        "copy_to_library": True,
        "batch_size": 8,
        "device": "auto",
        "caption_model": "microsoft/Florence-2-large",
        "whisper_model": "tiny",
        "model_name": str(_active_config["model_name"]),
    }
    _import_job.update(
        id=job_id, status="queued", done=0, total=0, imported=0, quarantined=0,
        forced=True, message="Importando itens marcados como “importar mesmo assim”.",
    )
    with _import_db() as conn:
        import_review.create_job(conn, job_id, json.dumps([str(media_dir)]), json.dumps(settings))
    try:
        _run_import_job(
            job_id, [media_dir], settings, None,
            explicit_files=paths, dedup_enabled=False,
        )
    finally:
        _import_job["forced"] = False


# ── Backup: versioned catalog snapshots (external destination) ──────────────────


def _active_db_path() -> Path:
    return Path(str(_active_config["db_path"]))


def _weights_path() -> Path:
    return _DATA_DIR / "best_weights.json"


def _library_root() -> Path:
    """Resolve the active media library root (storage_path is relative to it)."""
    try:
        with sqlite3.connect(str(_active_db_path())) as conn:
            row = conn.execute(
                "SELECT root_path FROM media_libraries ORDER BY id LIMIT 1"
            ).fetchone()
        if row and row[0]:
            return Path(row[0])
    except Exception:
        pass
    return _DATA_DIR / "library" / "default"


def _safe_snapshot_name(name: str) -> bool:
    return name.endswith(backup_mod.SNAPSHOT_SUFFIX) and "/" not in name and ".." not in name


def _rebuild_faiss(db_path: Path, model_name: str) -> None:
    from core.indexer import create_faiss_indices

    create_faiss_indices(Path(db_path), model_name or str(_active_config["model_name"]))


def _do_snapshot(reason: str, cfg: dict) -> dict:
    info = backup_mod.catalog_snapshot(
        _active_db_path(), cfg["backup_dir"],
        weights_path=_weights_path(), model_name=str(_active_config["model_name"]),
        reason=reason,
    )
    backup_mod.apply_retention(cfg["backup_dir"], int(cfg.get("backup_keep_last") or 0))
    return info


def maybe_auto_snapshot(reason: str) -> dict | None:
    """Take a pre-op catalog snapshot if auto-backup is on. Never raises (best effort)."""
    try:
        cfg = app_config.load()
        if not cfg.get("backup_auto") or not cfg.get("backup_dir"):
            return None
        if not app_config.validate_backup_dir(cfg["backup_dir"], _DATA_DIR).get("ok"):
            return None
        return _do_snapshot(reason, cfg)
    except Exception as exc:
        print(f"[iris] auto-snapshot ({reason}) falhou: {exc}")
        return None


@app.get("/api/backup/config", response_model=BackupConfigOut)
async def backup_get_config():
    cfg = app_config.load()
    val = (
        app_config.validate_backup_dir(cfg["backup_dir"], _DATA_DIR)
        if cfg["backup_dir"] else {"ok": False, "warnings": [], "error": ""}
    )
    return {
        **cfg,
        "dir_ok": val.get("ok", False),
        "warnings": val.get("warnings", []),
        "error": val.get("error", ""),
    }


@app.post("/api/backup/config", response_model=OkOut)
async def backup_set_config(
    backup_dir: str = Form(""),
    backup_auto: bool = Form(True),
    backup_keep_last: int = Form(10),
    media_originals_root: str = Form("media"),
):
    resolved = backup_dir.strip()
    warnings: list[str] = []
    if resolved:
        val = app_config.validate_backup_dir(resolved, _DATA_DIR)
        if not val["ok"]:
            raise HTTPException(400, val.get("error", "Destino de backup inválido"))
        resolved = val.get("resolved") or resolved
        warnings = val.get("warnings", [])
    saved = app_config.save({
        "backup_dir": resolved,
        "backup_auto": backup_auto,
        "backup_keep_last": max(1, backup_keep_last),
        "media_originals_root": media_originals_root.strip() or "media",
    })
    return {"ok": True, **saved, "warnings": warnings}


@app.get("/api/backup/snapshots", response_model=BackupSnapshotsOut)
async def backup_list_snapshots():
    cfg = app_config.load()
    if not cfg["backup_dir"]:
        return {"configured": False, "snapshots": []}
    snaps = await run_in_threadpool(backup_mod.list_snapshots, cfg["backup_dir"])
    return {"configured": True, "backup_dir": cfg["backup_dir"], "snapshots": snaps}


@app.post("/api/backup/snapshot", response_model=OkOut)
async def backup_snapshot_now(reason: str = Form("manual")):
    cfg = app_config.load()
    if not cfg["backup_dir"]:
        raise HTTPException(400, "Configure um destino de backup primeiro")
    try:
        info = await run_in_threadpool(_do_snapshot, reason, cfg)
    except Exception as exc:
        raise HTTPException(500, f"Falha ao criar snapshot: {exc}") from exc
    return {"ok": True, "snapshot": info}


@app.post("/api/backup/restore", response_model=OkOut)
async def backup_restore(
    snapshot_id: str = Form(...),
    mode: str = Form("overlay"),
    confirm: bool = Form(False),
):
    if not confirm:
        raise HTTPException(400, "A restauração precisa ser confirmada")
    if mode not in {"overlay", "mirror"}:
        raise HTTPException(400, "Modo de restauração inválido")
    cfg = app_config.load()
    if not cfg["backup_dir"] or not _safe_snapshot_name(snapshot_id):
        raise HTTPException(404, "Snapshot não encontrado")
    snap = Path(cfg["backup_dir"]) / snapshot_id
    if not snap.exists():
        raise HTTPException(404, "Snapshot não encontrado")
    # Safety net: snapshot the current state first so the restore is reversible.
    pre = await run_in_threadpool(maybe_auto_snapshot, "pre-restore")
    try:
        result = await run_in_threadpool(_do_restore, snap, mode)
        await run_in_threadpool(_reload_backend)
    except Exception as exc:
        raise HTTPException(400, f"Não foi possível restaurar: {exc}") from exc
    return {"ok": True, "pre_restore": pre, **result}


def _do_restore(snap: Path, mode: str) -> dict:
    return backup_mod.restore_snapshot(
        snap, db_path=_active_db_path(), weights_path=_weights_path(),
        mode=mode, rebuild_faiss=_rebuild_faiss, model_name=str(_active_config["model_name"]),
    )


@app.get("/api/backup/snapshots/{snapshot_id}/download")
def backup_download_snapshot(snapshot_id: str):
    cfg = app_config.load()
    if not cfg["backup_dir"] or not _safe_snapshot_name(snapshot_id):
        raise HTTPException(404, "Snapshot não encontrado")
    snap = Path(cfg["backup_dir"]) / snapshot_id
    if not snap.exists():
        raise HTTPException(404, "Snapshot não encontrado")
    return FileResponse(snap, media_type="application/gzip", filename=snapshot_id)


@app.post("/api/backup/media/reconcile", response_model=OkOut)
async def backup_media_reconcile():
    cfg = app_config.load()
    res = await run_in_threadpool(
        backup_mod.reconcile_media, _active_db_path(), _library_root(), cfg["media_originals_root"]
    )
    return {"ok": True, **res}


@app.post("/api/backup/media/export", response_model=OkOut)
async def backup_media_export():
    cfg = app_config.load()
    if not cfg["backup_dir"]:
        raise HTTPException(400, "Configure um destino de backup primeiro")
    try:
        res = await run_in_threadpool(backup_mod.export_media, _library_root(), cfg["backup_dir"])
    except Exception as exc:
        raise HTTPException(400, f"Falha ao exportar biblioteca: {exc}") from exc
    return {"ok": True, **res}


# ── Records (paginated gallery) ───────────────────────────────────────────────


def _sort_key_for(sort_by: str):
    def _key(r: IndexRecord) -> object:
        if sort_by == "nome":
            return r.arquivo.lower()
        if sort_by == "data":
            return r.file_mtime or 0.0
        if sort_by == "tamanho":
            return r.file_size or 0
        if sort_by == "tipo":
            return os.path.splitext(r.arquivo)[1].lower()
        return r.db_id or r.index  # importacao
    return _key


def _sorted_records(backend: SearchBackend, sort_by: str, sort_asc: int) -> list[IndexRecord]:
    """Full library sorted by the given criteria — cached per (sort_by, sort_asc).

    The sort is the O(N log N) cost paid on every page nav (the gallery prefetches
    ±1 page); memoising it makes subsequent pages of the same view ~O(page). Safe
    because records are immutable between backend reloads (which clear the cache).
    """
    engine = getattr(backend, "engine", None)
    db_key = str(getattr(engine, "db_path", id(backend)))
    key = (db_key, sort_by, int(bool(sort_asc)))
    cached = _sorted_records_cache.get(key)
    if cached is not None:
        return cached
    result = sorted(
        backend.get_all_records(), key=_sort_key_for(sort_by), reverse=not bool(sort_asc)
    )
    _sorted_records_cache[key] = result
    return result


def _filter_records(
    records: list[IndexRecord],
    backend: SearchBackend,
    options: SearchOptions,
) -> list[IndexRecord]:
    if options.media_type == "video":
        records = [
            r for r in records if os.path.splitext(r.arquivo)[1].lower() in VIDEO_EXTENSIONS
        ]
    elif options.media_type == "image":
        records = [
            r for r in records if os.path.splitext(r.arquivo)[1].lower() in IMAGE_EXTENSIONS
        ]

    if options.collection_ids:
        allowed = backend.get_collection_db_ids(options.collection_ids)
        records = [r for r in records if r.db_id in allowed]

    if options.concept_ids:
        allowed = backend.get_concept_db_ids(options.concept_ids)
        records = [r for r in records if r.db_id in allowed]

    return records


@app.get("/api/records", response_model=RecordsPageOut)
async def get_records(
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=12, le=500),
    sort_by: str = Query("importacao"),
    sort_asc: int = Query(0),
    media_type: str = Query("all"),
    collection_ids: str = Query(""),
    concept_ids: str = Query(""),
):
    backend = _get_backend()
    with trace("api.records"):
        options = _options_from_params(
            media_type=media_type,
            collection_ids=collection_ids,
            concept_ids=concept_ids,
        )

        # Sort once (cached); filtering after sort preserves order and stays live
        # so collection/concept membership changes are never served stale.
        records = _sorted_records(backend, sort_by, sort_asc)

        records = _filter_records(records, backend, options)

        records_sorted = records
        total = len(records_sorted)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        page_records = records_sorted[start : start + per_page]

        return {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "missing_count": sum(
                1 for r in page_records
                if not r.resolved_path or not os.path.exists(r.resolved_path)
            ),
            # Thumbnail-cache misses decode/resize the source file (PIL, or cv2
            # frame extraction for video) inside _record_to_json. Left inline,
            # that CPU-bound work runs straight on the event loop and stalls
            # every other in-flight request — including every other thumbnail
            # already cached — until it finishes.
            "records": await run_in_threadpool(
                lambda: _attach_persons([_record_to_json(r) for r in page_records])
            ),
        }


# ── Single record detail ─────────────────────────────────────────────────────


@app.get("/api/records/{idx}", response_model=RecordDetailOut)
async def get_record_detail(idx: int):
    backend = _get_backend()
    with trace("api.record_detail"):
        r = backend.get_record(idx)
        if r is None:
            raise HTTPException(404, "Record not found")
        d = await run_in_threadpool(_record_to_json, r)
        _attach_persons([d])
        # Add extra detail fields
        d["caminho"] = r.caminho
        d["score_details"] = {}
        # Add collection memberships
        try:
            d["collections"] = backend.get_record_collections(r.db_id) if r.db_id else []
        except Exception:
            d["collections"] = []
        # Add concept memberships
        try:
            if backend.has_concept_tables() and r.db_id:
                d["concepts"] = backend.get_media_concepts(r.db_id)
            else:
                d["concepts"] = []
        except Exception:
            d["concepts"] = []
        return d


@app.get("/api/records/{idx}/metadata", response_model=RecordMetadataOut)
async def get_record_metadata(idx: int):
    """Curated (stored) + full (read on demand from the original file) metadata."""
    backend = _get_backend()
    with trace("api.record_metadata"):
        r = backend.get_record(idx)
        if r is None:
            raise HTTPException(404, "Record not found")
        path = r.resolved_path or ""
        path_exists = bool(path) and os.path.exists(path)

        curated: dict = {}
        try:
            raw = backend.get_record_metadata_json(r.db_id) if r.db_id else ""
            if raw:
                curated = json.loads(raw)
        except Exception:
            curated = {}
        if not curated and path_exists:
            # Legacy rows indexed before metadata extraction existed.
            curated = await run_in_threadpool(extract_metadata, path)

        full: dict = {}
        if path_exists:
            full = await run_in_threadpool(extract_full_metadata, path)
        return {"curated": curated, "full": full, "path_exists": path_exists}


# ── Search ────────────────────────────────────────────────────────────────────


def _filename_match_score(record: IndexRecord, query: str) -> float:
    normalized_query = normalize_text(query)
    normalized_name = normalize_text(Path(record.arquivo).name)
    normalized_stem = normalize_text(Path(record.arquivo).stem)
    if not normalized_query:
        return 0.0
    if normalized_name == normalized_query or normalized_stem == normalized_query:
        return 1.0
    if normalized_query in normalized_name:
        return 0.85
    words = [word for word in normalized_query.split() if word]
    if not words:
        return 0.0
    matched = sum(1 for word in words if word in normalized_name)
    return (matched / len(words)) * 0.7


def _record_to_search_result(record: IndexRecord, score: float, kind: str) -> SearchResult:
    return SearchResult(
        score=score,
        index=record.index,
        arquivo=record.arquivo,
        caminho=record.caminho,
        resolved_path=record.resolved_path,
        texto_extraido=record.texto_extraido,
        descricao_ia=record.descricao_ia,
        tags=record.tags,
        embedding=record.embedding,
        score_details={"filename": score, "kind": kind},
    )


@app.get("/api/search", response_model=SearchResponseOut)
async def search_text(
    q: str = Query(...),
    top_k: int = Query(50),
    threshold: float = Query(0.15),
    balance: float = Query(0.5),
    text_bonus: float = Query(1.0),
    lexical_weight: float = Query(0.25),
    translate: bool = Query(True),
    media_type: str = Query("all"),
    collection_ids: str = Query(""),
    concept_ids: str = Query(""),
):
    _require_search_model()
    backend = _get_backend()
    with trace("api.search.text"):
        options = _options_from_params(
            top_k=top_k, threshold=threshold, balance=balance,
            text_bonus=text_bonus, lexical_weight=lexical_weight,
            translate=translate, media_type=media_type,
            collection_ids=collection_ids, concept_ids=concept_ids,
        )
        results = await _run_search(backend.search_text, q.strip(), options)
        return {
            "query": q,
            "total": len(results),
            "results": await _results_to_json(results),
        }


@app.get("/api/search/filename", response_model=SearchResponseOut)
async def search_filename(
    q: str = Query(...),
    top_k: int = Query(50, ge=1, le=500),
    media_type: str = Query("all"),
    collection_ids: str = Query(""),
    concept_ids: str = Query(""),
):
    backend = _get_backend()
    with trace("api.search.filename"):
        query = q.strip()
        options = _options_from_params(
            top_k=top_k,
            media_type=media_type,
            collection_ids=collection_ids,
            concept_ids=concept_ids,
        )
        records = await _run_search(_filter_records, backend.get_all_records(), backend, options)
        scored = [
            (score, record)
            for record in records
            if (score := _filename_match_score(record, query)) > 0
        ]
        scored.sort(key=lambda item: (-item[0], item[1].arquivo.lower(), item[1].db_id))
        results = [
            _record_to_search_result(record, score, "filename")
            for score, record in scored[:options.top_k]
        ]
        return {
            "query": q,
            "total": len(results),
            "results": await _results_to_json(results),
        }


@app.post("/api/search/image", response_model=UploadSearchResponseOut)
async def search_image(
    file: Annotated[UploadFile, File()],
    top_k: int = Form(50),
    threshold: float = Form(0.15),
    balance: float = Form(0.5),
    text_bonus: float = Form(1.0),
    lexical_weight: float = Form(0.25),
    media_type: str = Form("all"),
    collection_ids: str = Form(""),
    concept_ids: str = Form(""),
    group_results: bool = Form(False),
    group_threshold: float = Form(0.90),
    show_singletons: bool = Form(True),
):
    _require_search_model()
    backend = _get_backend()
    with trace("api.search.image"):
        img = await run_in_threadpool(_open_uploaded_image, file)
        options = _options_from_params(
            top_k=top_k, threshold=threshold, balance=balance,
            text_bonus=text_bonus, lexical_weight=lexical_weight,
            media_type=media_type, collection_ids=collection_ids,
            concept_ids=concept_ids,
        )
        results = await _run_search(backend.search_image, img, options)
        response = {
            "filename": file.filename,
            "total": len(results),
            "results": await _results_to_json(results),
        }
        if group_results:
            groups = _group_search_results(results, max(-1.0, min(group_threshold, 1.0)))
            if not show_singletons:
                groups = [group for group in groups if len(group) > 1]
            response["groups"] = [
                await _results_to_json(group)
                for group in groups
            ]
        return response


@app.post("/api/search/face", response_model=UploadSearchResponseOut)
async def search_face(
    file: Annotated[UploadFile, File()],
    top_k: int = Form(50),
):
    _require_search_model()
    backend = _get_backend()
    with trace("api.search.face"):
        img = await run_in_threadpool(_open_uploaded_image, file)
        if not backend.has_face_tables():
            raise HTTPException(409, "Reconhecimento facial indisponível neste catálogo.")
        results = await run_in_threadpool(
            backend.search_face, img, max(1, min(top_k, _MAX_SEARCH_TOP_K))
        )
        if results is None:
            raise HTTPException(422, "Nenhum rosto detectado na imagem enviada.")
        return {
            "filename": file.filename,
            "total": len(results),
            "results": await _results_to_json(results),
        }


@app.get("/api/search/face/by-record/{idx}", response_model=SourceSearchResponseOut)
async def search_face_by_record(idx: int, top_k: int = Query(50)):
    _require_search_model()
    backend = _get_backend()
    with trace("api.search.face.by_record"):
        if not backend.has_face_tables():
            raise HTTPException(409, "Reconhecimento facial indisponível neste catálogo.")
        results = backend.search_face_by_record(idx, max(1, min(top_k, _MAX_SEARCH_TOP_K)))
        if results is None:
            raise HTTPException(422, "Nenhum rosto detectado nesta mídia.")
        return {
            "source_index": idx,
            "total": len(results),
            "results": await _results_to_json(results),
        }


@app.get("/api/search/face/by-face/{face_id}", response_model=SourceSearchResponseOut)
async def search_face_by_face(face_id: int, top_k: int = Query(50)):
    _require_search_model()
    backend = _get_backend()
    with trace("api.search.face.by_face"):
        if not backend.has_face_tables():
            raise HTTPException(409, "Reconhecimento facial indisponível neste catálogo.")
        results = backend.search_face_by_face(face_id, max(1, min(top_k, _MAX_SEARCH_TOP_K)))
        return {
            "source_face": face_id,
            "total": len(results),
            "results": await _results_to_json(results),
        }


@app.get("/api/search/similar/{idx}", response_model=SourceSearchResponseOut)
async def search_similar(
    idx: int,
    top_k: int = Query(50),
    threshold: float = Query(0.15),
    balance: float = Query(0.5),
    text_bonus: float = Query(1.0),
    lexical_weight: float = Query(0.25),
    media_type: str = Query("all"),
):
    backend = _get_backend()
    with trace("api.search.similar"):
        options = _options_from_params(
            top_k=top_k, threshold=threshold, balance=balance,
            text_bonus=text_bonus, lexical_weight=lexical_weight,
            media_type=media_type,
        )
        results = await _run_search(backend.search_similar, idx, options)
        return {
            "source_index": idx,
            "total": len(results),
            "results": await _results_to_json(results),
        }


@app.get("/api/search/random", response_model=SearchResponseOut)
async def search_random(n: int = Query(20, ge=1, le=100)):
    backend = _get_backend()
    with trace("api.search.random"):
        results = backend.random_results(n)
        return {
            "total": len(results),
            "results": await _results_to_json(results),
        }


# ── Collections ───────────────────────────────────────────────────────────────


@app.get("/api/collections", response_model=CollectionsOut)
async def list_collections():
    backend = _get_backend()
    with trace("api.collections.list"):
        return {"collections": backend.list_collections()}


@app.post("/api/collections", response_model=OkOut)
async def create_collection(name: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.create"):
        collection_id = backend.create_collection(name)
        if not isinstance(collection_id, int):
            collection_id = None
        return {"ok": True, "collection_id": collection_id}


@app.post("/api/collections/{col_id}/rename", response_model=OkOut)
async def rename_collection(col_id: int, name: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.rename"):
        backend.rename_collection(col_id, name)
        return {"ok": True}


@app.post("/api/collections/{col_id}/delete", response_model=OkOut)
async def delete_collection(col_id: int):
    backend = _get_backend()
    with trace("api.collections.delete"):
        backend.delete_collection(col_id)
        return {"ok": True}


@app.get("/api/collections/{col_id}/members", response_model=CollectionMembersOut)
async def get_collection_members(col_id: int):
    backend = _get_backend()
    with trace("api.collections.members"):
        db_ids = backend.get_collection_members(col_id)

        def _build() -> list[dict[str, Any]]:
            records = []
            for db_id in db_ids:
                for r in backend.get_all_records():
                    if r.db_id == db_id:
                        records.append(_record_to_json(r))
                        break
            return records

        records = await run_in_threadpool(_build)
        return {"db_ids": db_ids, "records": records}


@app.post("/api/collections/{col_id}/members", response_model=CollectionMembersAddedOut)
async def add_collection_members(col_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.add_members"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        n = backend.add_records_to_collection(ids, col_id)
        return {"added": n}


@app.post("/api/collections/{col_id}/members/remove", response_model=OkOut)
async def remove_collection_members(col_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.remove_members"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        backend.remove_records_from_collection(ids, col_id)
        return {"ok": True}


@app.get("/api/collections/filter", response_model=DbIdsOut)
async def get_collection_filter(ids: str = Query("")):
    backend = _get_backend()
    with trace("api.collections.filter"):
        if not ids.strip():
            return {"db_ids": []}
        parsed = frozenset(int(x) for x in ids.split(",") if x.strip().isdigit())
        return {"db_ids": sorted(backend.get_collection_db_ids(parsed))}


# ── Concepts ──────────────────────────────────────────────────────────────────


@app.get("/api/concepts", response_model=ConceptsOut)
async def list_concepts():
    backend = _get_backend()
    with trace("api.concepts.list"):
        if not backend.has_concept_tables():
            return {"concepts": []}
        return {"concepts": backend.list_concepts()}


@app.get("/api/concepts/filter", response_model=DbIdsOut)
async def get_concept_filter(ids: str = Query("")):
    backend = _get_backend()
    with trace("api.concepts.filter"):
        if not ids.strip() or not backend.has_concept_tables():
            return {"db_ids": []}
        parsed = frozenset(int(x) for x in ids.split(",") if x.strip().isdigit())
        return {"db_ids": sorted(backend.get_concept_db_ids(parsed))}


@app.get("/api/concepts/{concept_id}/matches", response_model=ConceptMatchesOut)
async def find_concept_matches(
    concept_id: int,
    top_k: int = Query(80),
    min_score: float = Query(0.65),
):
    backend = _get_backend()
    with trace("api.concepts.matches"):
        matches = backend.find_concept_matches(
            concept_id,
            max(1, min(top_k, _MAX_SEARCH_TOP_K)),
            max(-1.0, min(min_score, 1.0)),
        )
        records = []
        for idx, score in matches:
            r = backend.get_record(idx)
            if r:
                d = _record_to_json(r)
                d["score"] = round(score, 4)
                records.append(d)
        return {"matches": records}


@app.get("/api/concepts/{concept_id}/references", response_model=ConceptReferencesOut)
async def get_concept_references(concept_id: int):
    backend = _get_backend()
    with trace("api.concepts.references"):
        return {
            "references": [
                _concept_reference_to_json(reference)
                for reference in backend.get_references(concept_id)
            ]
        }


@app.get("/api/concepts/{concept_id}/associations", response_model=ConceptAssociationsOut)
async def get_concept_associations(
    concept_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    backend = _get_backend()
    confirmed_ids = sorted(backend.get_confirmed_meme_ids(concept_id))
    total = len(confirmed_ids)
    start = (page - 1) * per_page
    page_ids = confirmed_ids[start : start + per_page]
    records = [
        _record_to_json(record)
        for db_id in page_ids
        if (record := _record_for_db_id(db_id)) is not None
    ]
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "records": records,
    }


@app.post("/api/concepts", response_model=ConceptCreatedOut)
async def create_concept(
    name: str = Form(...),
    category: str = Form("outro"),
    description: str = Form(""),
    search_terms: str = Form(""),
    auto_threshold: float = Form(0.65),
):
    backend = _get_backend()
    with trace("api.concepts.create"):
        cid = backend.create_concept(
            name=name, category=category, description=description,
            search_terms=search_terms, auto_threshold=auto_threshold,
        )
        return {"id": cid}


@app.post("/api/concepts/with-references", response_model=ConceptWithReferencesOut)
async def create_concept_with_references(
    files: Annotated[list[UploadFile], File()],
    name: str = Form(...),
    category: str = Form("outro"),
    description: str = Form(""),
    search_terms: str = Form(""),
    auto_threshold: float = Form(0.65),
):
    if not files:
        raise HTTPException(400, "Adicione pelo menos uma imagem de referência")
    backend = _get_backend()
    concept_id = backend.create_concept(
        name=name,
        category=category,
        description=description,
        search_terms=search_terms,
        auto_threshold=auto_threshold,
    )
    try:
        for upload in files:
            await run_in_threadpool(_store_concept_reference, backend, concept_id, upload)
    except Exception:
        backend.delete_concept(concept_id)
        raise
    return {"id": concept_id, "references": len(files)}


@app.post("/api/concepts/{concept_id}/update", response_model=OkOut)
async def update_concept(
    concept_id: int,
    name: str | None = Form(None),
    category: str | None = Form(None),
    description: str | None = Form(None),
    search_terms: str | None = Form(None),
    auto_threshold: float | None = Form(None),
):
    backend = _get_backend()
    with trace("api.concepts.update"):
        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if category is not None:
            kwargs["category"] = category
        if description is not None:
            kwargs["description"] = description
        if search_terms is not None:
            kwargs["search_terms"] = search_terms
        if auto_threshold is not None:
            kwargs["auto_threshold"] = auto_threshold
        backend.update_concept(concept_id, **kwargs)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/delete", response_model=OkOut)
async def delete_concept(concept_id: int):
    backend = _get_backend()
    with trace("api.concepts.delete"):
        backend.delete_concept(concept_id)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/references", response_model=OkOut)
async def add_concept_reference(
    concept_id: int,
    file: Annotated[UploadFile, File()],
):
    backend = _get_backend()
    with trace("api.concepts.add_reference"):
        await run_in_threadpool(_store_concept_reference, backend, concept_id, file)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/references/batch", response_model=OkOut)
async def add_concept_references(
    concept_id: int,
    files: Annotated[list[UploadFile], File()],
):
    backend = _get_backend()
    added = 0
    for file in files:
        await run_in_threadpool(_store_concept_reference, backend, concept_id, file)
        added += 1
    return {"ok": True, "added": added}


@app.post("/api/concepts/{concept_id}/references/{ref_id}/delete", response_model=OkOut)
async def delete_concept_reference(concept_id: int, ref_id: int):
    backend = _get_backend()
    with trace("api.concepts.delete_reference"):
        backend.delete_reference(ref_id)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/confirm", response_model=OkOut)
async def confirm_concept_media(concept_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.concepts.confirm"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        backend.set_media_confirmed(concept_id, ids)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/reject", response_model=OkOut)
async def reject_concept_media(concept_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.concepts.reject"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        backend.set_media_rejected(concept_id, ids)
        return {"ok": True}


# ── Pessoas (rostos) ──────────────────────────────────────────────────────────


@app.get("/api/persons", response_model=PersonsOut)
async def list_persons():
    backend = _get_backend()
    with trace("api.persons.list"):
        if not backend.has_face_tables():
            return {"persons": []}
        return {"persons": backend.list_persons()}


@app.get("/api/persons/{person_id}/media", response_model=PersonMediaOut)
async def person_media(person_id: int):
    backend = _get_backend()
    with trace("api.persons.media"):
        results = backend.get_person_media(person_id)
        try:
            person = next((p for p in backend.list_persons() if p["id"] == person_id), None)
        except Exception:
            person = None
        return {
            "person_id": person_id,
            "person_name": (person or {}).get("name") or "",
            "total": len(results),
            "results": await _results_to_json(results),
        }


@app.post("/api/persons/{person_id}/rename", response_model=OkOut)
async def rename_person(person_id: int, name: str = Form("")):
    backend = _get_backend()
    with trace("api.persons.rename"):
        backend.rename_person(person_id, name)
        return {"ok": True}


@app.post("/api/persons/merge", response_model=OkOut)
async def merge_persons(source_id: int = Form(...), target_id: int = Form(...)):
    backend = _get_backend()
    with trace("api.persons.merge"):
        backend.merge_persons(source_id, target_id)
        return {"ok": True}


@app.post("/api/persons/{person_id}/delete", response_model=OkOut)
async def delete_person(person_id: int):
    backend = _get_backend()
    with trace("api.persons.delete"):
        backend.delete_person(person_id)
        return {"ok": True}


@app.post("/api/persons", response_model=OkOut)
async def create_person(name: str = Form("")):
    backend = _get_backend()
    with trace("api.persons.create"):
        if not backend.has_face_tables():
            raise HTTPException(409, "Reconhecimento facial indisponível neste catálogo.")
        person_id = backend.create_person(name)
        return {"ok": True, "person_id": person_id}


@app.post("/api/faces/{face_id}/person", response_model=OkOut)
async def assign_face_person(
    face_id: int,
    person_id: int | None = Form(None),
    name: str = Form(""),
):
    """Assign a face to a person. ``name`` creates the person first; neither → unassign."""
    backend = _get_backend()
    with trace("api.faces.assign"):
        if not backend.has_face_tables():
            raise HTTPException(409, "Reconhecimento facial indisponível neste catálogo.")
        try:
            if name.strip():
                person_id = backend.create_person(name)
            backend.set_face_person(face_id, person_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "person_id": person_id}


@app.post("/api/persons/cluster", response_model=OkOut)
async def cluster_persons(recluster: bool = Form(False)):
    backend = _get_backend()
    with trace("api.persons.cluster"):
        if not backend.has_face_tables():
            raise HTTPException(409, "Reconhecimento facial indisponível neste catálogo.")
        stats = await run_in_threadpool(backend.cluster_faces, recluster)
        return {"ok": True, **stats}


@app.get("/api/records/{idx}/faces", response_model=RecordFacesOut)
async def record_faces(idx: int):
    backend = _get_backend()
    with trace("api.records.faces"):
        record = backend.get_record(idx)
        if record is None or not record.db_id:
            return {"faces": []}
        if not backend.has_face_tables():
            return {"faces": []}
        return {"faces": backend.get_media_faces(record.db_id)}


@app.get("/api/faces/{face_id}/thumb")
async def face_thumb(face_id: int):
    backend = _get_backend()
    with trace("api.faces.thumb"):
        blob = backend.get_face_thumbnail(face_id) if backend.has_face_tables() else None
        if not blob:
            raise HTTPException(404, "Rosto não encontrado.")
        return Response(content=blob, media_type="image/jpeg")


# ── Web enrichment ───────────────────────────────────────────────────────────


def _record_by_db_id(db_id: int) -> IndexRecord | None:
    return next((record for record in _get_backend().get_all_records() if record.db_id == db_id), None)


def _run_web_enrichment_job(
    job_id: str,
    db_ids: list[int],
    force: bool = False,
    backend_overrides: dict[str, str] | None = None,
    research: bool = False,
) -> None:
    conn = _backend_connection()
    try:
        service = _create_web_enrichment_service(backend_overrides)
        # Existing tags/categories, so the AI reuses them instead of inventing new ones.
        vocabulary = gather_vocabulary(conn)
        update_job(conn, job_id, status="running", message="Iniciando busca web")
        done = 0
        for db_id in db_ids:
            record = _record_by_db_id(db_id)
            label = record.arquivo if record else f"DB {db_id}"
            if not force and find_existing_suggestion(conn, db_id) is not None:
                done += 1
                update_job(conn, job_id, done=done, message=f"{label}: reaproveitado (cache)")
                continue
            # Reuse the previous Lens sources (just re-run the AI) unless the user
            # explicitly asked for a fresh search. Avoids re-opening the browser.
            cached_sources = [] if research else load_existing_sources(conn, db_id)
            try:
                if cached_sources:
                    update_job(conn, job_id, done=done, message=f"Re-enviando {label} para a IA")
                    suggestion = service.redistill(cached_sources, vocabulary)
                else:
                    update_job(conn, job_id, done=done, message=f"Pesquisando {label}")
                    if record is None or not record.resolved_path:
                        raise RuntimeError("Registro sem arquivo resolvido")
                    path = Path(record.resolved_path)
                    if not path.exists() or not path.is_file():
                        raise RuntimeError("Arquivo não encontrado")
                    suggestion = service.enrich_path(path, vocabulary)
            except Exception as exc:
                suggestion = EnrichmentSuggestion(
                    provider="web_enrichment",
                    summary="Falha ao enriquecer esta imagem.",
                    confidence=0,
                    error_message=str(exc),
                )
            insert_suggestion(conn, job_id, db_id, suggestion)
            done += 1
            update_job(conn, job_id, done=done, message=f"{done}/{len(db_ids)} concluído")
        update_job(conn, job_id, status="completed", done=done, message="Enriquecimento concluído")
    except Exception as exc:
        update_job(
            conn,
            job_id,
            status="failed",
            message="Falha no enriquecimento",
            error_message=str(exc),
        )


@app.post("/api/enrichment/jobs", response_model=EnrichmentJobStartedOut)
async def create_enrichment_job(
    db_ids: str = Form(...),
    force: str = Form(""),
    llm_backend: str = Form(""),
    llm_model: str = Form(""),
    webchat_target: str = Form(""),
    webchat_temporary: str = Form(""),
    research: str = Form(""),
):
    conn = _backend_connection()
    ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
    if not ids:
        raise HTTPException(400, "Selecione pelo menos uma imagem")
    # Backend overrides from the UI selector (API keys stay in env for safety).
    overrides = {
        k: v
        for k, v in {
            "backend": llm_backend.strip(),
            "model": llm_model.strip(),
            "target": webchat_target.strip(),
            "temporary": webchat_temporary.strip(),
        }.items()
        if v
    }
    effective_llm_backend = llm_backend.strip() or os.environ.get("IRIS_LLM_BACKEND", "")
    if _multiuser_enabled() and effective_llm_backend.lower() in {"webchat", "web", "browser"}:
        raise HTTPException(403, "Web-chat é desativado em bibliotecas privadas")
    force_flag = force.strip().lower() in {"1", "true", "yes", "on"}
    research_flag = research.strip().lower() in {"1", "true", "yes", "on"}
    service = _create_web_enrichment_service(overrides)
    # The Lens provider config is only required when a fresh search will run; a
    # pure re-distill of already-found sources needs no provider config.
    needs_search = research_flag or any(not load_existing_sources(conn, i) for i in ids)
    if needs_search:
        missing = service.missing_config()
        if missing:
            raise HTTPException(400, "Configuração ausente: " + ", ".join(missing))
    cached = 0 if force_flag else count_cached_ids(conn, ids)
    job_id = create_job(conn, ids)
    context = contextvars.copy_context()
    thread = threading.Thread(
        target=context.run,
        args=(_run_web_enrichment_job, job_id, ids, force_flag, overrides, research_flag),
        daemon=True,
    )
    thread.start()
    return {
        "job_id": job_id,
        "total": len(ids),
        "cached": cached,
        "force": force_flag,
        "research": research_flag,
    }


@app.get("/api/enrichment/jobs/{job_id}", response_model=dict[str, Any])
async def get_enrichment_job(job_id: str):
    job = get_job(_backend_connection(), job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado")
    return job


@app.get("/api/enrichment/suggestions", response_model=EnrichmentSuggestionsOut)
async def get_enrichment_suggestions(status: str = Query("pending")):
    return {"suggestions": list_suggestions(_backend_connection(), status=status)}


@app.post("/api/enrichment/suggestions/{suggestion_id}/apply", response_model=dict[str, Any])
async def apply_enrichment_suggestion(
    suggestion_id: int,
    fields: str = Form(""),
):
    selected = [field.strip() for field in fields.split(",") if field.strip()]
    try:
        result = apply_suggestion(_backend_connection(), suggestion_id, selected)
        _refresh_backend_metadata()
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/enrichment/suggestions/{suggestion_id}/reject", response_model=OkOut)
async def reject_enrichment_suggestion(suggestion_id: int):
    reject_suggestion(_backend_connection(), suggestion_id)
    return {"ok": True}


# ── Duplicates ────────────────────────────────────────────────────────────────


@app.get("/api/duplicates", response_model=DuplicatesOut)
async def get_duplicates(
    threshold: float = Query(0.985, ge=0.0, le=1.0),
    max_neighbors: int = Query(12, ge=2, le=50),
    min_group_size: int = Query(2, ge=2, le=500),
):
    backend = _get_backend()
    with trace("api.duplicates"):
        groups = backend.find_duplicate_groups(threshold, max_neighbors)
        groups = [group for group in groups if len(group.items) >= min_group_size]
        return {
            "threshold": threshold,
            "max_neighbors": max_neighbors,
            "min_group_size": min_group_size,
            "total_groups": len(groups),
            "groups": [
                {
                    "group_id": g.group_id,
                    "kind": g.kind,
                    "score": g.score,
                    "items": [
                        {
                            "index": it.index,
                            "arquivo": it.arquivo,
                            "resolved_path": it.resolved_path,
                            "score_to_anchor": it.score_to_anchor,
                            "thumbnail_url": _thumbnail_url_from_path(it.resolved_path),
                            "file_mtime": (
                                os.path.getmtime(it.resolved_path)
                                if it.resolved_path and os.path.exists(it.resolved_path)
                                else 0
                            ),
                        }
                        for it in g.items
                    ],
                }
                for g in groups
            ],
        }


# ── Trash ─────────────────────────────────────────────────────────────────────


@app.post("/api/trash", response_model=TrashOut)
async def trash_records(db_ids: str = Form(...)):
    backend = _get_backend()
    await run_in_threadpool(maybe_auto_snapshot, "pre-trash")
    with trace("api.trash"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        paths = []
        for db_id in ids:
            records = [r for r in backend.get_all_records() if r.db_id == db_id]
            for r in records:
                if r.resolved_path and os.path.exists(r.resolved_path):
                    paths.append(r.resolved_path)
        moved, failed = move_to_trash(paths)
        return {"moved": len(moved), "failed": len(failed)}


# ── Static media ──────────────────────────────────────────────────────────────


@app.get("/thumbs/{filename}")
async def serve_thumbnail(filename: str):
    # Prevent path traversal and keep cached previews inside the caller's library.
    thumb_dir = _thumbnail_dir()
    thumb_path = (thumb_dir / filename).resolve()
    if not thumb_path.is_relative_to(thumb_dir.resolve()):
        raise HTTPException(404, "Thumbnail not found")
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(
        thumb_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/media/{file_path:path}")
async def serve_media(file_path: str):
    """Serve an original media file. Path is relative to filesystem root."""
    abs_path = Path("/") / file_path
    resolved = abs_path.resolve()
    allowed_paths = {
        Path(record.resolved_path).resolve()
        for record in _get_backend().get_all_records()
        if record.resolved_path
    }
    if resolved not in allowed_paths:
        raise HTTPException(404, f"File not found: {file_path}")
    if not resolved.exists():
        raise HTTPException(404, f"File not found: {file_path}")
    return FileResponse(resolved, media_type=_guess_mime(str(resolved)))
