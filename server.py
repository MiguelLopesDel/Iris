"""Iris FastAPI application.

Serves the SPA shell and a REST JSON API consumed by vanilla JavaScript modules.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

# Ensure core/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from core.app_operations import (
    backup_inventory,
    create_backup_file,
    inspect_backup_zip,
    restore_backup_zip,
)
from core.backend import SearchBackend, create_backend
from core.file_ops import move_to_trash
from core.perf import dump, trace
from core.search_engine import DEFAULT_MODEL, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from core.search_types import IndexRecord, SearchOptions, SearchResult
from core.web_enrichment import (
    EnrichmentSuggestion,
    WebEnrichmentService,
    apply_suggestion,
    count_cached_ids,
    create_job,
    create_web_enrichment_tables,
    find_existing_suggestion,
    get_job,
    insert_suggestion,
    list_suggestions,
    reject_suggestion,
    update_job,
)

# ── Constants ─────────────────────────────────────────────────────────────────
_THUMB_DIR = Path("data/thumbnails")
_DEFAULT_DB = os.environ.get("IRIS_DB", "data/meme_compass_full_v1.db")
_MEDIA_ROOT = os.environ.get("IRIS_MEDIA_ROOT", "media")
_LOAD_MODEL = os.environ.get("IRIS_LOAD_MODEL", "1").lower() not in {"0", "false", "no"}
_DATA_DIR = Path("data")

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
    "current": "",
    "message": "",
    "started_at": None,
    "finished_at": None,
}


def _get_backend() -> SearchBackend:
    if _backend is None:
        raise HTTPException(503, "Backend not initialised yet")
    return _backend


def _reload_backend(config: dict[str, Any] | None = None) -> SearchBackend:
    global _backend
    previous_config = dict(_active_config)
    if config:
        _active_config.update(config)
    try:
        with _backend_lock:
            backend = create_backend(
                db_path=str(_active_config["db_path"]),
                media_root=str(_active_config["media_root"]),
                model_name=str(_active_config["model_name"]),
                load_model=bool(_active_config["load_model"]),
            )
            _ensure_web_enrichment_tables(backend)
            _backend = backend
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
    if _backend is None:
        print(f"[iris] Loading backend — DB: {_active_config['db_path']}")
        backend = _reload_backend()
    else:
        backend = _backend
    print(f"[iris] Ready — {backend.get_total_records()} records")
    yield
    dump()
    print("[iris] Shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan, title="Iris")

# Static assets
static_dir = Path(__file__).parent / "static"
template_dir = Path(__file__).parent / "templates"
static_dir.mkdir(parents=True, exist_ok=True)
template_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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
        top_k=top_k,
        threshold=threshold,
        balance=balance,
        text_bonus=text_bonus,
        lexical_weight=lexical_weight,
        translate=translate,
        candidate_pool=candidate_pool,
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
    }


def _result_to_json(r: SearchResult) -> dict[str, Any]:
    base = _record_to_json(_get_backend().get_record(r.index) or _empty_record(r))
    base["score"] = round(r.score, 4)
    base["score_details"] = r.score_details
    return base


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


def _thumbnail_url(r: IndexRecord) -> str:
    """Compute thumbnail URL for a gallery record."""
    return _thumbnail_url_from_path(r.resolved_path)


def _thumbnail_url_from_path(fp: str) -> str:
    """Return thumbnail URL, generating on-the-fly if missing.

    Uses md5(path:mtime:size) as cache key so thumbnails survive renames
    but invalidate when the source file changes.
    """
    if not fp or not os.path.exists(fp):
        return ""
    try:
        stat = os.stat(fp)
        key = hashlib.md5(
            f"{fp}:{stat.st_mtime}:{stat.st_size}".encode()
        ).hexdigest()
        thumb = _THUMB_DIR / f"{key}.jpg"

        if not thumb.exists():
            _THUMB_DIR.mkdir(parents=True, exist_ok=True)
            ext = os.path.splitext(fp)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                _generate_video_thumbnail(fp, thumb)
            else:
                _generate_image_thumbnail(fp, thumb)

        return f"/thumbs/{key}.jpg" if thumb.exists() else ""
    except Exception:
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
    from PIL import Image

    from core.concepts import make_thumbnail

    try:
        image = Image.open(upload.file).convert("RGB")
    except Exception as exc:
        raise HTTPException(400, f"Não foi possível abrir {upload.filename}: {exc}") from exc
    thumbnail = make_thumbnail(image)
    embedding = np.asarray(backend.encode_image(image), dtype=np.float32).reshape(-1).tobytes()
    backend.add_reference(concept_id, embedding, thumbnail, upload.filename or "")


def _record_for_db_id(db_id: int) -> IndexRecord | None:
    return next((record for record in _get_backend().get_all_records() if record.db_id == db_id), None)


def _set_import_progress(done: int, total: int, current: str) -> None:
    _import_job.update(done=done, total=total, current=current)


async def _save_upload_to_temp(upload: UploadFile, suffix: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix="iris-upload-", suffix=suffix)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                output.write(chunk)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _run_import_job(job_id: str, sources: list[Path], settings: dict[str, Any], cleanup: Path | None) -> None:
    try:
        from core.indexer import IndexerConfig, create_faiss_indices, process_images, resolve_device

        _import_job.update(
            id=job_id,
            status="running",
            done=0,
            total=0,
            current="",
            message="Carregando modelos e preparando importação.",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
        )
        db_path = Path(str(_active_config["db_path"]))
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
            )
            process_images(config, progress_callback=_set_import_progress)
        create_faiss_indices(db_path, str(settings["model_name"]))
        _reload_backend()
        _import_job.update(
            status="completed",
            message="Importação concluída e índices recarregados.",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except BaseException as exc:
        _import_job.update(
            status="failed",
            message=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)


# ── Page routes ───────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return HTMLResponse(
        (template_dir / "index.html").read_text(),
        headers={"Cache-Control": "no-store"},
    )


# ── Info ──────────────────────────────────────────────────────────────────────


@app.get("/api/info")
async def get_info():
    backend = _get_backend()
    with trace("api.info"):
        records = backend.get_all_records()
        extension_counts: dict[str, int] = {}
        missing_count = 0
        for record in records:
            extension = Path(record.arquivo).suffix.lower() or "(sem extensão)"
            extension_counts[extension] = extension_counts.get(extension, 0) + 1
            if not record.resolved_path or not Path(record.resolved_path).exists():
                missing_count += 1
        return {
            "total_records": backend.get_total_records(),
            "db_path": str(_active_config["db_path"]),
            "media_root": str(_active_config["media_root"]),
            "model_name": str(_active_config["model_name"]),
            "load_model": bool(_active_config["load_model"]),
            "has_concepts": backend.has_concept_tables(),
            "missing_count": missing_count,
            "extension_counts": extension_counts,
            "databases": _available_databases(),
        }


@app.post("/api/settings")
async def update_settings(
    db_path: str = Form(...),
    media_root: str = Form(...),
    model_name: str = Form(DEFAULT_MODEL),
):
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


@app.post("/api/reload")
async def reload_backend():
    backend = await run_in_threadpool(_reload_backend)
    return {"ok": True, "total_records": backend.get_total_records()}


@app.get("/api/filesystem")
async def browse_filesystem(path: str = Query("")):
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


@app.post("/api/open-folder")
async def open_folder(path: str = Form(...)):
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
        await run_in_threadpool(_open_folder_in_file_manager, target)
    except FileNotFoundError as exc:
        raise HTTPException(500, "Gerenciador de arquivos não encontrado neste ambiente") from exc
    except OSError as exc:
        raise HTTPException(500, f"Não foi possível abrir a pasta: {exc}") from exc
    return {"ok": True, "path": str(target)}


@app.get("/api/import/status")
async def import_status():
    return dict(_import_job)


@app.post("/api/import")
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
):
    if _import_job["status"] in {"queued", "running"}:
        raise HTTPException(409, "Já existe uma importação em andamento")
    if batch_size < 1 or batch_size > 64:
        raise HTTPException(400, "Batch size deve ficar entre 1 e 64")
    if device not in {"auto", "cuda", "mps", "cpu"}:
        raise HTTPException(400, "Dispositivo inválido")

    sources: list[Path] = []
    if folder.strip():
        source = Path(folder).expanduser().resolve()
        if not source.exists() or not source.is_dir():
            raise HTTPException(400, "Pasta de importação não encontrada")
        sources.append(source)

    cleanup: Path | None = None
    if files:
        upload_root = _DATA_DIR / "import_uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        cleanup = Path(tempfile.mkdtemp(prefix="iris-", dir=upload_root))
        for upload in files:
            filename = Path(upload.filename or "upload").name
            destination = cleanup / filename
            with destination.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    output.write(chunk)
        sources.append(cleanup)

    if not sources:
        raise HTTPException(400, "Escolha uma pasta ou envie arquivos")

    job_id = uuid.uuid4().hex
    settings = {
        "recursive": recursive,
        "library_name": library_name.strip() or "default",
        "library_root": library_root.strip() or "data/library",
        "copy_to_library": copy_to_library,
        "batch_size": batch_size,
        "device": device,
        "caption_model": caption_model.strip() or "none",
        "whisper_model": whisper_model.strip() or "none",
        "model_name": str(_active_config["model_name"]),
    }
    _import_job.update(id=job_id, status="queued", message="Importação na fila.")
    threading.Thread(
        target=_run_import_job,
        args=(job_id, sources, settings, cleanup),
        daemon=True,
        name=f"iris-import-{job_id[:8]}",
    ).start()
    return {"ok": True, "job_id": job_id}


@app.get("/api/backup/info")
async def get_backup_info():
    return backup_inventory(_DATA_DIR)


@app.get("/api/backup")
def download_backup(include_library: bool = Query(True)):
    filename = datetime.now(timezone.utc).strftime("iris_backup_%Y%m%d_%H%M%S.zip")
    file_descriptor, temporary_name = tempfile.mkstemp(prefix="iris-backup-", suffix=".zip")
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    create_backup_file(_DATA_DIR, temporary, include_library=include_library)
    return FileResponse(
        temporary,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(temporary.unlink, missing_ok=True),
    )


@app.post("/api/backup/inspect")
async def inspect_backup(file: Annotated[UploadFile, File()]):
    temporary = await _save_upload_to_temp(file, ".zip")
    try:
        return await run_in_threadpool(inspect_backup_zip, temporary)
    except Exception as exc:
        raise HTTPException(400, f"Backup inválido: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


@app.post("/api/backup/restore")
async def restore_backup(
    file: Annotated[UploadFile, File()],
    confirm: bool = Form(False),
):
    if not confirm:
        raise HTTPException(400, "A restauração precisa ser confirmada")
    temporary = await _save_upload_to_temp(file, ".zip")
    try:
        result = await run_in_threadpool(restore_backup_zip, temporary, _DATA_DIR)
        await run_in_threadpool(_reload_backend)
        return {"ok": True, **result}
    except Exception as exc:
        raise HTTPException(400, f"Não foi possível restaurar: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


# ── Records (paginated gallery) ───────────────────────────────────────────────


@app.get("/api/records")
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

        records = backend.get_all_records()

        # Filter by media type
        if options.media_type == "video":
            records = [r for r in records if os.path.splitext(r.arquivo)[1].lower() in VIDEO_EXTENSIONS]
        elif options.media_type == "image":
            records = [r for r in records if os.path.splitext(r.arquivo)[1].lower() in IMAGE_EXTENSIONS]

        # Filter by collection
        if options.collection_ids:
            allowed = backend.get_collection_db_ids(options.collection_ids)
            records = [r for r in records if r.db_id in allowed]

        # Filter by concept
        if options.concept_ids:
            allowed = backend.get_concept_db_ids(options.concept_ids)
            records = [r for r in records if r.db_id in allowed]

        # Sort
        def _sort_key(r: IndexRecord) -> object:
            if sort_by == "nome":
                return r.arquivo.lower()
            if sort_by == "data":
                return r.file_mtime or 0.0
            if sort_by == "tamanho":
                return r.file_size or 0
            if sort_by == "tipo":
                return os.path.splitext(r.arquivo)[1].lower()
            return r.db_id or r.index  # importacao

        records_sorted = sorted(records, key=_sort_key, reverse=not bool(sort_asc))
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
            "records": [_record_to_json(r) for r in page_records],
        }


# ── Single record detail ─────────────────────────────────────────────────────


@app.get("/api/records/{idx}")
async def get_record_detail(idx: int):
    backend = _get_backend()
    with trace("api.record_detail"):
        r = backend.get_record(idx)
        if r is None:
            raise HTTPException(404, "Record not found")
        d = _record_to_json(r)
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


# ── Search ────────────────────────────────────────────────────────────────────


@app.get("/api/search")
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
    backend = _get_backend()
    with trace("api.search.text"):
        options = _options_from_params(
            top_k=top_k, threshold=threshold, balance=balance,
            text_bonus=text_bonus, lexical_weight=lexical_weight,
            translate=translate, media_type=media_type,
            collection_ids=collection_ids, concept_ids=concept_ids,
        )
        results = backend.search_text(q.strip(), options)
        return {
            "query": q,
            "total": len(results),
            "results": [_result_to_json(r) for r in results],
        }


@app.post("/api/search/image")
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
    backend = _get_backend()
    with trace("api.search.image"):
        from PIL import Image
        try:
            img = Image.open(file.file).convert("RGB")
        except Exception as exc:
            raise HTTPException(400, f"Could not open image file: {exc}") from exc
        options = _options_from_params(
            top_k=top_k, threshold=threshold, balance=balance,
            text_bonus=text_bonus, lexical_weight=lexical_weight,
            media_type=media_type, collection_ids=collection_ids,
            concept_ids=concept_ids,
        )
        results = backend.search_image(img, options)
        response = {
            "filename": file.filename,
            "total": len(results),
            "results": [_result_to_json(r) for r in results],
        }
        if group_results:
            groups = _group_search_results(results, group_threshold)
            if not show_singletons:
                groups = [group for group in groups if len(group) > 1]
            response["groups"] = [
                [_result_to_json(result) for result in group]
                for group in groups
            ]
        return response


@app.get("/api/search/similar/{idx}")
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
        results = backend.search_similar(idx, options)
        return {
            "source_index": idx,
            "total": len(results),
            "results": [_result_to_json(r) for r in results],
        }


@app.get("/api/search/random")
async def search_random(n: int = Query(20, ge=1, le=100)):
    backend = _get_backend()
    with trace("api.search.random"):
        results = backend.random_results(n)
        return {
            "total": len(results),
            "results": [_result_to_json(r) for r in results],
        }


# ── Collections ───────────────────────────────────────────────────────────────


@app.get("/api/collections")
async def list_collections():
    backend = _get_backend()
    with trace("api.collections.list"):
        return {"collections": backend.list_collections()}


@app.post("/api/collections")
async def create_collection(name: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.create"):
        backend.create_collection(name)
        return {"ok": True}


@app.post("/api/collections/{col_id}/rename")
async def rename_collection(col_id: int, name: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.rename"):
        backend.rename_collection(col_id, name)
        return {"ok": True}


@app.post("/api/collections/{col_id}/delete")
async def delete_collection(col_id: int):
    backend = _get_backend()
    with trace("api.collections.delete"):
        backend.delete_collection(col_id)
        return {"ok": True}


@app.get("/api/collections/{col_id}/members")
async def get_collection_members(col_id: int):
    backend = _get_backend()
    with trace("api.collections.members"):
        db_ids = backend.get_collection_members(col_id)
        records = []
        for db_id in db_ids:
            for r in backend.get_all_records():
                if r.db_id == db_id:
                    records.append(_record_to_json(r))
                    break
        return {"db_ids": db_ids, "records": records}


@app.post("/api/collections/{col_id}/members")
async def add_collection_members(col_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.add_members"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        n = backend.add_records_to_collection(ids, col_id)
        return {"added": n}


@app.post("/api/collections/{col_id}/members/remove")
async def remove_collection_members(col_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.remove_members"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        backend.remove_records_from_collection(ids, col_id)
        return {"ok": True}


@app.get("/api/collections/filter")
async def get_collection_filter(ids: str = Query("")):
    backend = _get_backend()
    with trace("api.collections.filter"):
        if not ids.strip():
            return {"db_ids": []}
        parsed = frozenset(int(x) for x in ids.split(",") if x.strip().isdigit())
        return {"db_ids": sorted(backend.get_collection_db_ids(parsed))}


# ── Concepts ──────────────────────────────────────────────────────────────────


@app.get("/api/concepts")
async def list_concepts():
    backend = _get_backend()
    with trace("api.concepts.list"):
        if not backend.has_concept_tables():
            return {"concepts": []}
        return {"concepts": backend.list_concepts()}


@app.get("/api/concepts/filter")
async def get_concept_filter(ids: str = Query("")):
    backend = _get_backend()
    with trace("api.concepts.filter"):
        if not ids.strip() or not backend.has_concept_tables():
            return {"db_ids": []}
        parsed = frozenset(int(x) for x in ids.split(",") if x.strip().isdigit())
        return {"db_ids": sorted(backend.get_concept_db_ids(parsed))}


@app.get("/api/concepts/{concept_id}/matches")
async def find_concept_matches(
    concept_id: int,
    top_k: int = Query(80),
    min_score: float = Query(0.65),
):
    backend = _get_backend()
    with trace("api.concepts.matches"):
        matches = backend.find_concept_matches(concept_id, top_k, min_score)
        records = []
        for idx, score in matches:
            r = backend.get_record(idx)
            if r:
                d = _record_to_json(r)
                d["score"] = round(score, 4)
                records.append(d)
        return {"matches": records}


@app.get("/api/concepts/{concept_id}/references")
async def get_concept_references(concept_id: int):
    backend = _get_backend()
    with trace("api.concepts.references"):
        return {
            "references": [
                _concept_reference_to_json(reference)
                for reference in backend.get_references(concept_id)
            ]
        }


@app.get("/api/concepts/{concept_id}/associations")
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


@app.post("/api/concepts")
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


@app.post("/api/concepts/with-references")
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


@app.post("/api/concepts/{concept_id}/update")
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


@app.post("/api/concepts/{concept_id}/delete")
async def delete_concept(concept_id: int):
    backend = _get_backend()
    with trace("api.concepts.delete"):
        backend.delete_concept(concept_id)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/references")
async def add_concept_reference(
    concept_id: int,
    file: Annotated[UploadFile, File()],
):
    backend = _get_backend()
    with trace("api.concepts.add_reference"):
        await run_in_threadpool(_store_concept_reference, backend, concept_id, file)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/references/batch")
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


@app.post("/api/concepts/{concept_id}/references/{ref_id}/delete")
async def delete_concept_reference(concept_id: int, ref_id: int):
    backend = _get_backend()
    with trace("api.concepts.delete_reference"):
        backend.delete_reference(ref_id)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/confirm")
async def confirm_concept_media(concept_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.concepts.confirm"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        backend.set_media_confirmed(concept_id, ids)
        return {"ok": True}


@app.post("/api/concepts/{concept_id}/reject")
async def reject_concept_media(concept_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.concepts.reject"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        backend.set_media_rejected(concept_id, ids)
        return {"ok": True}


# ── Web enrichment ───────────────────────────────────────────────────────────


def _record_by_db_id(db_id: int) -> IndexRecord | None:
    return next((record for record in _get_backend().get_all_records() if record.db_id == db_id), None)


def _run_web_enrichment_job(
    job_id: str,
    db_ids: list[int],
    force: bool = False,
    backend_overrides: dict[str, str] | None = None,
) -> None:
    conn = _backend_connection()
    try:
        service = _create_web_enrichment_service(backend_overrides)
        update_job(conn, job_id, status="running", message="Iniciando busca web")
        done = 0
        for db_id in db_ids:
            record = _record_by_db_id(db_id)
            label = record.arquivo if record else f"DB {db_id}"
            if not force and find_existing_suggestion(conn, db_id) is not None:
                done += 1
                update_job(conn, job_id, done=done, message=f"{label}: reaproveitado (cache)")
                continue
            update_job(conn, job_id, done=done, message=f"Pesquisando {label}")
            try:
                if record is None or not record.resolved_path:
                    raise RuntimeError("Registro sem arquivo resolvido")
                path = Path(record.resolved_path)
                if not path.exists() or not path.is_file():
                    raise RuntimeError("Arquivo não encontrado")
                suggestion = service.enrich_path(path)
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


@app.post("/api/enrichment/jobs")
async def create_enrichment_job(
    db_ids: str = Form(...),
    force: str = Form(""),
    llm_backend: str = Form(""),
    llm_model: str = Form(""),
    webchat_target: str = Form(""),
    webchat_cdp: str = Form(""),
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
            "cdp": webchat_cdp.strip(),
        }.items()
        if v
    }
    service = _create_web_enrichment_service(overrides)
    missing = service.missing_config()
    if missing:
        raise HTTPException(400, "Configuração ausente: " + ", ".join(missing))
    force_flag = force.strip().lower() in {"1", "true", "yes", "on"}
    cached = 0 if force_flag else count_cached_ids(conn, ids)
    job_id = create_job(conn, ids)
    thread = threading.Thread(
        target=_run_web_enrichment_job,
        args=(job_id, ids, force_flag, overrides),
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id, "total": len(ids), "cached": cached, "force": force_flag}


@app.get("/api/enrichment/jobs/{job_id}")
async def get_enrichment_job(job_id: str):
    job = get_job(_backend_connection(), job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado")
    return job


@app.get("/api/enrichment/suggestions")
async def get_enrichment_suggestions(status: str = Query("pending")):
    return {"suggestions": list_suggestions(_backend_connection(), status=status)}


@app.post("/api/enrichment/suggestions/{suggestion_id}/apply")
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


@app.post("/api/enrichment/suggestions/{suggestion_id}/reject")
async def reject_enrichment_suggestion(suggestion_id: int):
    reject_suggestion(_backend_connection(), suggestion_id)
    return {"ok": True}


# ── Duplicates ────────────────────────────────────────────────────────────────


@app.get("/api/duplicates")
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


@app.post("/api/trash")
async def trash_records(db_ids: str = Form(...)):
    backend = _get_backend()
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
    # Prevent path traversal: resolve and verify stays within _THUMB_DIR
    thumb_path = (_THUMB_DIR / filename).resolve()
    if not thumb_path.is_relative_to(_THUMB_DIR.resolve()):
        raise HTTPException(404, "Thumbnail not found")
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(
        thumb_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
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
