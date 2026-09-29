"""HTTP adapter for the legacy paginated gallery and timeline."""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool

from core.api_models import RecordsPageOut, TimelineOut
from core.perf import trace

router = APIRouter(tags=["records"])


@dataclass(frozen=True)
class GalleryReadOperations:
    """Existing catalog operations used by the gallery HTTP endpoints."""

    get_backend: Callable[[], Any]
    options_from_params: Callable[..., Any]
    sorted_records: Callable[[Any, str, int], list[int]]
    filter_records: Callable[[list[int], Any, Any], list[int]]
    record_to_json: Callable[[Any], dict[str, Any]]
    attach_persons: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def _operations(request: Request) -> GalleryReadOperations:
    return request.app.state.gallery_read_operations


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
