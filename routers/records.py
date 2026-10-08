"""HTTP adapter for the legacy paginated gallery and timeline."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from core.api_models import OkOut, RecordDetailOut, RecordMetadataOut, RecordsPageOut, TimelineOut
from core.media_metadata import (
    extract_full_metadata,
    extract_metadata,
    merge_file_metadata,
    needs_file_metadata,
)
from core.perf import trace

router = APIRouter(tags=["records"])
logger = logging.getLogger("iris")


@dataclass(frozen=True)
class RecordRouteOperations:
    """Existing catalog operations used by the record HTTP endpoints."""

    get_backend: Callable[[], Any]
    options_from_params: Callable[..., Any]
    sorted_records: Callable[[Any, str, int], list[int]]
    filter_records: Callable[[list[int], Any, Any], list[int]]
    record_to_json: Callable[[Any], dict[str, Any]]
    attach_persons: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    backend_connection: Callable[[], Any]
    invalidate_view_caches: Callable[[], None]
    refresh_backend_metadata: Callable[[], None]


def _operations(request: Request) -> RecordRouteOperations:
    return request.app.state.record_route_operations


@router.get("/api/records", response_model=RecordsPageOut)
async def get_records(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=12, le=500),
    sort_by: str = Query("importacao"),
    sort_asc: int = Query(0),
    media_type: str = Query("all"),
    collection_ids: str = Query(""),
    concept_ids: str = Query(""),
):
    operations = _operations(request)
    backend = operations.get_backend()
    with trace("api.records"):
        options = operations.options_from_params(
            media_type=media_type,
            collection_ids=collection_ids,
            concept_ids=concept_ids,
        )
        # Cached ordering is stable; memberships and filters remain live.
        record_indices = operations.sorted_records(backend, sort_by, sort_asc)
        record_indices = operations.filter_records(record_indices, backend, options)

        total = len(record_indices)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        page_records = [
            record
            for index in record_indices[start : start + per_page]
            if (record := backend.get_record(index)) is not None
        ]

        # Thumbnail generation can decode source media on a cache miss. Keep
        # that CPU-bound work off the event loop so concurrent requests proceed.
        records = await run_in_threadpool(
            lambda: operations.attach_persons(
                [operations.record_to_json(record) for record in page_records]
            )
        )
        return {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "missing_count": sum(
                1
                for record in page_records
                if not record.resolved_path or not os.path.exists(record.resolved_path)
            ),
            "records": records,
        }


@router.get("/api/records/timeline", response_model=TimelineOut)
async def get_records_timeline(
    request: Request,
    media_type: str = Query("all"),
    collection_ids: str = Query(""),
    concept_ids: str = Query(""),
):
    """Return record counts and start offsets grouped by capture month."""
    operations = _operations(request)
    backend = operations.get_backend()
    with trace("api.records.timeline"):
        options = operations.options_from_params(
            media_type=media_type,
            collection_ids=collection_ids,
            concept_ids=concept_ids,
        )
        record_indices = operations.sorted_records(backend, "data", 0)
        record_indices = operations.filter_records(record_indices, backend, options)

        def build_buckets() -> list[dict[str, Any]]:
            buckets: list[dict[str, Any]] = []
            current: str | None = None
            visible_position = 0
            for index in record_indices:
                record = backend.get_record(index)
                if record is None:
                    continue
                mtime = record.file_mtime or 0.0
                month = (
                    dt.datetime.fromtimestamp(mtime).strftime("%Y-%m") if mtime else "desconhecido"
                )
                if month != current:
                    buckets.append({"month": month, "count": 0, "offset": visible_position})
                    current = month
                buckets[-1]["count"] += 1
                visible_position += 1
            return buckets

        buckets = await run_in_threadpool(build_buckets)
        return {"total": sum(bucket["count"] for bucket in buckets), "buckets": buckets}


@router.get("/api/records/{idx}", response_model=RecordDetailOut)
async def get_record_detail(request: Request, idx: int):
    operations = _operations(request)
    backend = operations.get_backend()
    with trace("api.record_detail"):
        record = backend.get_record(idx)
        if record is None:
            raise HTTPException(404, "Record not found")
        result = await run_in_threadpool(operations.record_to_json, record)
        operations.attach_persons([result])
        result["caminho"] = record.caminho
        result["score_details"] = {}
        try:
            result["collections"] = (
                backend.get_record_collections(record.db_id) if record.db_id else []
            )
        except Exception:
            result["collections"] = []
        try:
            result["concepts"] = (
                backend.get_media_concepts(record.db_id)
                if backend.has_concept_tables() and record.db_id
                else []
            )
        except Exception:
            result["concepts"] = []
        return result


@router.get("/api/records/{idx}/metadata", response_model=RecordMetadataOut)
async def get_record_metadata(request: Request, idx: int):
    """Return stored metadata and full metadata read from the original."""
    operations = _operations(request)
    backend = operations.get_backend()
    with trace("api.record_metadata"):
        record = backend.get_record(idx)
        if record is None:
            raise HTTPException(404, "Record not found")
        path = record.resolved_path or ""
        path_exists = bool(path) and os.path.exists(path)

        curated: dict[str, Any] = {}
        raw = ""
        try:
            raw = backend.get_record_metadata_json(record.db_id) if record.db_id else ""
            if raw:
                curated = json.loads(raw)
        except Exception:
            curated = {}
        if path_exists and (not curated or needs_file_metadata(curated)):
            # Legacy rows predate stored extraction, and device sync catalogs without
            # reading the file: read GPS, place and device now, once, and keep them.
            extracted = await run_in_threadpool(extract_metadata, path)
            curated = merge_file_metadata(curated, extracted) if curated else extracted
            if record.db_id:
                try:
                    await run_in_threadpool(
                        backend.replace_record_metadata_json,
                        record.db_id,
                        raw,
                        json.dumps(curated, ensure_ascii=False),
                    )
                except Exception:
                    logger.warning("record_metadata_store_failed db_id=%s", record.db_id, exc_info=True)

        full = await run_in_threadpool(extract_full_metadata, path) if path_exists else {}
        return {"curated": curated, "full": full, "path_exists": path_exists}


@router.post("/api/records/{idx}/rename", response_model=OkOut)
async def rename_record(request: Request, idx: int, name: str = Form(...)):
    """Rename the source file while keeping all catalog path columns in sync."""
    operations = _operations(request)
    backend = operations.get_backend()
    with trace("api.records.rename"):
        record = backend.get_record(idx)
        if record is None:
            raise HTTPException(404, "Record not found")

        requested = name.strip()
        if not requested:
            raise HTTPException(400, "Informe um nome")
        if any(separator in requested for separator in ("/", "\\", "\0")) or requested in (
            ".",
            "..",
        ):
            raise HTTPException(400, "O nome não pode conter caminho")

        current = Path(record.resolved_path or "")
        if not current.is_file():
            raise HTTPException(409, "Arquivo original indisponível")

        stem = Path(requested).stem or requested
        target = current.with_name(stem + current.suffix)
        if target == current:
            return {"ok": True, "arquivo": current.name}
        if target.exists():
            raise HTTPException(409, "Já existe um arquivo com esse nome")

        await run_in_threadpool(os.rename, current, target)

        def update_rows() -> None:
            # Reuse the engine connection; closing it would invalidate the backend.
            connection = operations.backend_connection()
            row = connection.execute(
                "SELECT arquivo, caminho, relative_path, storage_path FROM memes WHERE id = ?",
                (record.db_id,),
            ).fetchone()
            if row is None:
                return

            def swap_name(value: str | None) -> str | None:
                return str(PurePosixPath(value).with_name(target.name)) if value else value

            connection.execute(
                "UPDATE memes SET arquivo = ?, caminho = ?, relative_path = ?, "
                "storage_path = ? WHERE id = ?",
                (
                    target.name,
                    str(target),
                    swap_name(row["relative_path"]),
                    swap_name(row["storage_path"]),
                    record.db_id,
                ),
            )
            connection.commit()

        try:
            await run_in_threadpool(update_rows)
        except Exception:
            # Keep the database and filesystem consistent if the update fails.
            await run_in_threadpool(os.rename, target, current)
            raise

        operations.invalidate_view_caches()
        await run_in_threadpool(operations.refresh_backend_metadata)
        return {"ok": True, "arquivo": target.name}
