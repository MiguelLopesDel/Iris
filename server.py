"""Iris — FastAPI server replacing the Streamlit frontend.

Serves the SPA shell (Jinja2) and a REST JSON API consumed by vanilla JS modules.
The core/ backend is unchanged — this is a pure frontend swap.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Ensure core/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from core.backend import SearchBackend, create_backend
from core.file_ops import move_to_trash
from core.perf import dump, trace
from core.search_engine import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from core.search_types import IndexRecord, SearchOptions, SearchResult

# ── Constants ─────────────────────────────────────────────────────────────────
_THUMB_DIR = Path("data/thumbnails")
_DEFAULT_DB = os.environ.get("IRIS_DB", "data/meme_compass_full_v1.db")
_MEDIA_ROOT = os.environ.get("IRIS_MEDIA_ROOT", "media")

# ── Backend singleton ─────────────────────────────────────────────────────────
_backend: SearchBackend | None = None


def _get_backend() -> SearchBackend:
    if _backend is None:
        raise HTTPException(503, "Backend not initialised yet")
    return _backend


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _backend
    print(f"[iris] Loading backend — DB: {_DEFAULT_DB}")
    _backend = create_backend(
        db_path=_DEFAULT_DB,
        media_root=_MEDIA_ROOT,
    )
    print(f"[iris] Ready — {_backend.get_total_records()} records")
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

# Load index.html once at startup
_index_html = (template_dir / "index.html").read_text()


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


def _thumbnail_url(r: IndexRecord) -> str:
    """Compute thumbnail URL, generating the thumbnail on-the-fly if missing."""
    fp = r.resolved_path
    if not fp or not os.path.exists(fp):
        return ""
    try:
        stat = os.stat(fp)
        key = hashlib.md5(
            f"{fp}:{stat.st_mtime}:{stat.st_size}".encode()
        ).hexdigest()
        thumb = _THUMB_DIR / f"{key}.jpg"

        if not thumb.exists():
            ext = os.path.splitext(fp)[1].lower()
            _THUMB_DIR.mkdir(parents=True, exist_ok=True)
            if ext in VIDEO_EXTENSIONS:
                # Extract a frame as thumbnail
                import cv2
                cap = cv2.VideoCapture(fp)
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                frame = None
                for pos in (0, total // 4, total // 2):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(pos, 0))
                    ok, frm = cap.read()
                    if ok:
                        frame = frm
                        break
                cap.release()
                if frame is not None:
                    from PIL import Image
                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    img.thumbnail((300, 300), Image.LANCZOS)
                    img.save(str(thumb), format="JPEG", quality=75, optimize=True)
                else:
                    return ""
            else:
                from PIL import Image
                img = Image.open(fp).convert("RGB")
                img.thumbnail((300, 300), Image.LANCZOS)
                img.save(str(thumb), format="JPEG", quality=75, optimize=True)

        return f"/thumbs/{key}.jpg"
    except Exception:
        return ""


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


# ── Page routes ───────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return HTMLResponse(_index_html)


# ── Info ──────────────────────────────────────────────────────────────────────


@app.get("/api/info")
async def get_info():
    backend = _get_backend()
    with trace("api.info"):
        return {
            "total_records": backend.get_total_records(),
            "db_path": _DEFAULT_DB,
            "has_concepts": backend.has_concept_tables(),
        }


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
    file: UploadFile = File(...),
    top_k: int = Form(50),
    threshold: float = Form(0.15),
    balance: float = Form(0.5),
    text_bonus: float = Form(1.0),
    lexical_weight: float = Form(0.25),
    media_type: str = Form("all"),
    collection_ids: str = Form(""),
    concept_ids: str = Form(""),
):
    backend = _get_backend()
    with trace("api.search.image"):
        from PIL import Image
        img = Image.open(file.file).convert("RGB")
        options = _options_from_params(
            top_k=top_k, threshold=threshold, balance=balance,
            text_bonus=text_bonus, lexical_weight=lexical_weight,
            media_type=media_type, collection_ids=collection_ids,
            concept_ids=concept_ids,
        )
        results = backend.search_image(img, options)
        return {
            "filename": file.filename,
            "total": len(results),
            "results": [_result_to_json(r) for r in results],
        }


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
        return {"db_ids": backend.get_collection_members(col_id)}


@app.post("/api/collections/{col_id}/members")
async def add_collection_members(col_id: int, db_ids: str = Form(...)):
    backend = _get_backend()
    with trace("api.collections.add_members"):
        ids = [int(x) for x in db_ids.split(",") if x.strip().isdigit()]
        n = backend.add_records_to_collection(ids, col_id)
        return {"added": n}


@app.delete("/api/collections/{col_id}/members")
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
        return {"references": backend.get_references(concept_id)}


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
        if name is not None: kwargs["name"] = name
        if category is not None: kwargs["category"] = category
        if description is not None: kwargs["description"] = description
        if search_terms is not None: kwargs["search_terms"] = search_terms
        if auto_threshold is not None: kwargs["auto_threshold"] = auto_threshold
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
    file: UploadFile = File(...),
):
    backend = _get_backend()
    with trace("api.concepts.add_reference"):
        from PIL import Image
        img = Image.open(file.file).convert("RGB")
        from core.concepts import make_thumbnail
        thumb = make_thumbnail(img)
        emb = backend.encode_image(img)
        import io as _io
        buf = _io.BytesIO()
        import numpy as np
        np.save(buf, emb)
        emb_bytes = buf.getvalue()
        backend.add_reference(concept_id, emb_bytes, thumb, file.filename or "")
        return {"ok": True}


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


# ── Duplicates ────────────────────────────────────────────────────────────────


@app.get("/api/duplicates")
async def get_duplicates(
    threshold: float = Query(0.985),
    max_neighbors: int = Query(12),
):
    backend = _get_backend()
    with trace("api.duplicates"):
        groups = backend.find_duplicate_groups(threshold, max_neighbors)
        return {
            "threshold": threshold,
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
    thumb_path = _THUMB_DIR / filename
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
    if not abs_path.exists():
        raise HTTPException(404, f"File not found: {file_path}")
    return FileResponse(abs_path, media_type=_guess_mime(str(abs_path)))
