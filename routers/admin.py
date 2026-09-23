"""Instance settings for administrators. Configuration only: no media is exposed."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core import fs_clone, instance_settings
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
