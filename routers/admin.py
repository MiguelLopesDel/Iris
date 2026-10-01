"""Instance settings for administrators. Configuration only: no media is exposed."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import compute_device, faces, fs_clone, instance_settings
from core.instance_backup import BackupError, snapshots
from core.instance_settings import SettingError

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _admin(request: Request):
    if not request.app.state.multiuser_enabled:
        raise HTTPException(404, "Disponível apenas com contas privadas")
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    if not user.is_admin:
        raise HTTPException(403, "Apenas administradores alteram a instalação")
    return user


def _spaces_dir(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "spaces"


def _apply(request: Request) -> None:
    """Recompute the effective policy; new shared copies use it immediately."""
    policy = instance_settings.space_storage_policy(
        request.app.state.users_db_path, _spaces_dir(request)
    )
    request.app.state.space_policy = policy
    request.app.state.space_storage = policy.storage
    gpu = instance_settings.gpu_allowed(request.app.state.users_db_path)
    if gpu != compute_device.gpu_allowed():
        compute_device.set_gpu_allowed(gpu)
        # Loaded models stay on the device they were built for; drop them so
        # search engines and the face detector reload on the new one.
        registry = getattr(request.app.state, "backend_registry", None)
        if registry is not None:
            registry.clear()
        faces.set_detector(None)


def _state(request: Request) -> dict[str, Any]:
    resolved = instance_settings.resolve_all(request.app.state.users_db_path)
    policy = request.app.state.space_policy
    return {
        "settings": {
            key: {
                "value": item.value,
                "source": item.source,
                "env_value": item.env_value,
                "default": item.default,
            }
            for key, item in resolved.items()
        },
        "storage": {**asdict(policy.report), "warning": policy.warning},
        "gpu": {**asdict(compute_device.gpu_status()), "in_use": compute_device.resolve("auto") == "cuda"},
        "strategies": list(fs_clone.STRATEGIES),
    }


@router.get("/settings")
def read_settings(request: Request):
    _admin(request)
    return _state(request)


@router.put("/settings")
def update_settings(request: Request, payload: dict[str, Any]):
    actor = _admin(request)
    if not payload:
        raise HTTPException(422, "Nenhuma configuração enviada")
    try:
        values = {key: instance_settings.parse(key, raw) for key, raw in payload.items()}
    except SettingError as exc:
        raise HTTPException(422, str(exc)) from exc
    if "space_storage" in values:
        # Probe now, so an impossible choice is refused instead of saved.
        try:
            fs_clone.resolve(str(values["space_storage"]), _spaces_dir(request))
        except fs_clone.StorageConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
    instance_settings.save(request.app.state.users_db_path, values, actor.id)
    _apply(request)
    return _state(request)


@router.delete("/settings/{key}")
def reset_setting(request: Request, key: str):
    """Forget the interface value; the .env or built-in default applies again."""
    actor = _admin(request)
    path = request.app.state.users_db_path
    previous = instance_settings.resolve_all(path).get(key)
    try:
        instance_settings.reset(path, key)
    except SettingError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        _apply(request)
    except fs_clone.StorageConfigError as exc:
        # The .env value this would fall back to cannot work on this disk.
        if previous is not None and previous.source == "interface":
            instance_settings.save(path, {key: previous.value}, actor.id)
        raise HTTPException(409, f"O valor do .env não funciona neste disco: {exc}") from exc
    return _state(request)


# -- backups -----------------------------------------------------------------------

_RETENTION = {
    "policy": "segue a política",
    "pinned": "guardado para sempre",
    None: "anterior à política",
}


class RunBackupIn(BaseModel):
    pin: bool = False


@router.get("/backups")
def backup_status(request: Request):
    _admin(request)
    service = request.app.state.backup_service
    settings = service.settings()
    next_run = service.next_run()
    return {
        # Where the files land on the host, when Docker told us; else ours.
        "destination": os.environ.get("IRIS_BACKUP_HOST_DIR") or str(service.dest.resolve()),
        "same_disk_as_data": service.same_disk_as_data(),
        "enabled": settings.enabled,
        "next_run": next_run.astimezone(settings.zone).isoformat() if next_run else None,
        # Today's time has passed without a scheduled backup: it runs shortly.
        "catching_up": bool(next_run and next_run <= service.clock()),
        "running": service.running,
        "runs": [asdict(run) for run in service.runs()],
        "snapshots": [
            {
                "name": info.path.name,
                "created_at": info.created_at.astimezone(settings.zone).isoformat(),
                "iris_version": info.iris_version,
                "iris_commit": info.iris_commit,
                "retention": info.retention,
                "retention_label": _RETENTION.get(info.retention, info.retention),
                "files": info.files,
                "bytes_total": info.bytes_total,
            }
            for info in reversed(snapshots(service.dest))
        ] if service.dest.is_dir() else [],
    }


@router.post("/backups", status_code=202)
def run_backup(request: Request, payload: RunBackupIn):
    _admin(request)
    try:
        request.app.state.backup_service.run_in_background("manual", pinned=payload.pin)
    except BackupError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"started": True}
