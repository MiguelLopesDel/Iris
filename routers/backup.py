"""HTTP adapter for legacy catalog snapshots and media backup tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from core import app_config
from core import backup as backup_mod
from core.api_models import BackupConfigOut, BackupSnapshotsOut, OkOut

router = APIRouter(tags=["backup"])


@dataclass(frozen=True)
class BackupRouteOperations:
    """Application-specific operations needed by the legacy backup routes."""

    data_dir: Path
    active_db_path: Callable[[], Path]
    library_root: Callable[[], Path]
    safe_snapshot_name: Callable[[str], bool]
    do_snapshot: Callable[[str, dict[str, Any]], dict[str, Any]]
    maybe_auto_snapshot: Callable[[str], dict[str, Any] | None]
    do_restore: Callable[[Path, str], dict[str, Any]]
    reload_backend: Callable[[], Any]


def _operations(request: Request) -> BackupRouteOperations:
    return request.app.state.legacy_backup_operations


@router.get("/api/backup/config", response_model=BackupConfigOut)
async def backup_get_config(request: Request):
    operations = _operations(request)
    cfg = app_config.load()
    val = (
        app_config.validate_backup_dir(cfg["backup_dir"], operations.data_dir)
        if cfg["backup_dir"]
        else {"ok": False, "warnings": [], "error": ""}
    )
    return {
        **cfg,
        "dir_ok": val.get("ok", False),
        "warnings": val.get("warnings", []),
        "error": val.get("error", ""),
    }


@router.post("/api/backup/config", response_model=OkOut)
async def backup_set_config(
    request: Request,
    backup_dir: str = Form(""),
    backup_auto: bool = Form(True),
    backup_keep_last: int = Form(10),
    media_originals_root: str = Form("media"),
):
    operations = _operations(request)
    resolved = backup_dir.strip()
    warnings: list[str] = []
    if resolved:
        val = app_config.validate_backup_dir(resolved, operations.data_dir)
        if not val["ok"]:
            raise HTTPException(400, val.get("error", "Destino de backup inválido"))
        resolved = val.get("resolved") or resolved
        warnings = val.get("warnings", [])
    saved = app_config.save(
        {
            "backup_dir": resolved,
            "backup_auto": backup_auto,
            "backup_keep_last": max(1, backup_keep_last),
            "media_originals_root": media_originals_root.strip() or "media",
        }
    )
    return {"ok": True, **saved, "warnings": warnings}


@router.get("/api/backup/snapshots", response_model=BackupSnapshotsOut)
async def backup_list_snapshots():
    cfg = app_config.load()
    if not cfg["backup_dir"]:
        return {"configured": False, "snapshots": []}
    snaps = await run_in_threadpool(backup_mod.list_snapshots, cfg["backup_dir"])
    return {"configured": True, "backup_dir": cfg["backup_dir"], "snapshots": snaps}


@router.post("/api/backup/snapshot", response_model=OkOut)
async def backup_snapshot_now(request: Request, reason: str = Form("manual")):
    operations = _operations(request)
    cfg = app_config.load()
    if not cfg["backup_dir"]:
        raise HTTPException(400, "Configure um destino de backup primeiro")
    try:
        info = await run_in_threadpool(operations.do_snapshot, reason, cfg)
    except Exception as exc:
        raise HTTPException(500, f"Falha ao criar snapshot: {exc}") from exc
    return {"ok": True, "snapshot": info}


@router.post("/api/backup/restore", response_model=OkOut)
async def backup_restore(
    request: Request,
    snapshot_id: str = Form(...),
    mode: str = Form("overlay"),
    confirm: bool = Form(False),
):
    operations = _operations(request)
    if not confirm:
        raise HTTPException(400, "A restauração precisa ser confirmada")
    if mode not in {"overlay", "mirror"}:
        raise HTTPException(400, "Modo de restauração inválido")
    cfg = app_config.load()
    if not cfg["backup_dir"] or not operations.safe_snapshot_name(snapshot_id):
        raise HTTPException(404, "Snapshot não encontrado")
    snap = Path(cfg["backup_dir"]) / snapshot_id
    if not snap.exists():
        raise HTTPException(404, "Snapshot não encontrado")
    # Safety net: snapshot the current state first so the restore is reversible.
    pre = await run_in_threadpool(operations.maybe_auto_snapshot, "pre-restore")
    try:
        result = await run_in_threadpool(operations.do_restore, snap, mode)
        await run_in_threadpool(operations.reload_backend)
    except Exception as exc:
        raise HTTPException(400, f"Não foi possível restaurar: {exc}") from exc
    return {"ok": True, "pre_restore": pre, **result}


@router.get("/api/backup/snapshots/{snapshot_id}/download")
def backup_download_snapshot(request: Request, snapshot_id: str):
    operations = _operations(request)
    cfg = app_config.load()
    if not cfg["backup_dir"] or not operations.safe_snapshot_name(snapshot_id):
        raise HTTPException(404, "Snapshot não encontrado")
    snap = Path(cfg["backup_dir"]) / snapshot_id
    if not snap.exists():
        raise HTTPException(404, "Snapshot não encontrado")
    return FileResponse(snap, media_type="application/gzip", filename=snapshot_id)


@router.post("/api/backup/media/reconcile", response_model=OkOut)
async def backup_media_reconcile(request: Request):
    operations = _operations(request)
    cfg = app_config.load()
    result = await run_in_threadpool(
        backup_mod.reconcile_media,
        operations.active_db_path(),
        operations.library_root(),
        cfg["media_originals_root"],
    )
    return {"ok": True, **result}


@router.post("/api/backup/media/export", response_model=OkOut)
async def backup_media_export(request: Request):
    operations = _operations(request)
    cfg = app_config.load()
    if not cfg["backup_dir"]:
        raise HTTPException(400, "Configure um destino de backup primeiro")
    try:
        result = await run_in_threadpool(
            backup_mod.export_media, operations.library_root(), cfg["backup_dir"]
        )
    except Exception as exc:
        raise HTTPException(400, f"Falha ao exportar biblioteca: {exc}") from exc
    return {"ok": True, **result}
