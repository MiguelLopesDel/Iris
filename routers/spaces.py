"""Private shared-space API: membership and the space's own item catalogue."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import space_catalog
from core.library_intake import LibraryQuotaExceeded, save_copy, start_processing
from core.shared_spaces import (
    SpaceMemberExists,
    SpaceNotFound,
    SpacePermissionDenied,
    SpaceUserNotFound,
    add_member,
    create_space,
    get_space,
    list_members,
    list_spaces,
    member_role,
    usernames,
)
from core.space_catalog import (
    SpaceItem,
    SpaceItemConflict,
    SpaceItemNotFound,
    SpaceItemPermissionDenied,
    SpaceQuotaExceeded,
    SpaceStorage,
)

router = APIRouter(prefix="/api/spaces", tags=["spaces"])


class CreateSpaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AddMemberIn(BaseModel):
    username: str
    role: str


class AddItemIn(BaseModel):
    # A database id in the actor's own private library. It is resolved only
    # there, so an id from another library or space names nothing.
    record_id: int = Field(gt=0)


def _actor(request: Request):
    if not request.app.state.multiuser_enabled:
        raise HTTPException(404, "Espaços exigem contas privadas")
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    return user


@router.post("", status_code=201)
def create(request: Request, payload: CreateSpaceIn):
    actor = _actor(request)
    try:
        space = create_space(request.app.state.users_db_path, actor.id, payload.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"space": asdict(space)}


@router.get("")
def list_for_actor(request: Request):
    actor = _actor(request)
    return {
        "spaces": [asdict(space) for space in list_spaces(request.app.state.users_db_path, actor.id)]
    }


@router.get("/{space_id}")
def detail(request: Request, space_id: int):
    actor = _actor(request)
    try:
        space = get_space(request.app.state.users_db_path, space_id, actor.id)
    except SpaceNotFound as exc:
        raise HTTPException(404, "Espaço não encontrado") from exc
    return {"space": asdict(space)}


@router.get("/{space_id}/members")
def members(request: Request, space_id: int):
    actor = _actor(request)
    try:
        found = list_members(request.app.state.users_db_path, space_id, actor.id)
    except SpaceNotFound as exc:
        raise HTTPException(404, "Espaço não encontrado") from exc
    return {"members": [asdict(member) for member in found]}


@router.post("/{space_id}/members", status_code=201)
def invite(request: Request, space_id: int, payload: AddMemberIn):
    actor = _actor(request)
    try:
        member = add_member(
            request.app.state.users_db_path,
            space_id,
            actor.id,
            payload.username,
            payload.role,
        )
    except SpaceNotFound as exc:
        raise HTTPException(404, "Espaço não encontrado") from exc
    except SpacePermissionDenied as exc:
        raise HTTPException(403, "Apenas gestores podem adicionar membros") from exc
    except SpaceUserNotFound as exc:
        raise HTTPException(404, "Conta não encontrada") from exc
    except SpaceMemberExists as exc:
        raise HTTPException(409, "Conta já participa do espaço") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"member": asdict(member)}


_NOT_FOUND = "Espaço não encontrado"
_ITEM_NOT_FOUND = "Item não encontrado"


def _member(request: Request, space_id: int) -> tuple[Any, str]:
    """The actor and their role; outsiders get the same 404 as a missing space."""
    actor = _actor(request)
    try:
        return actor, member_role(request.app.state.users_db_path, space_id, actor.id)
    except SpaceNotFound as exc:
        raise HTTPException(404, _NOT_FOUND) from exc


def _storage(request: Request) -> SpaceStorage:
    return request.app.state.space_storage


def _root(request: Request, space_id: int) -> Path:
    return space_catalog.space_root(Path(request.app.state.data_dir), space_id)


def _purge(request: Request, space_id: int) -> None:
    # Lazy: whoever next looks at the space finishes the expired retention.
    space_catalog.purge_expired(_root(request, space_id), _storage(request).trash_days)


def _item_json(
    request: Request,
    space_id: int,
    item: SpaceItem,
    actor_id: int,
    role: str,
    authors: dict[int, str],
) -> dict[str, Any]:
    base = f"/api/spaces/{space_id}/items/{item.id}"
    payload: dict[str, Any] = {
        "id": item.id,
        "name": item.original_name,
        "mime_type": item.mime_type,
        "media_type": item.media_type,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
        "added_by": item.added_by,
        "added_by_username": authors.get(item.added_by) if item.added_by else None,
        "added_at": item.added_at,
        "can_remove": space_catalog.can_remove(role, actor_id, item),
    }
    if item.removed_at is None:
        payload["thumbnail_url"] = base + "/thumbnail"
        payload["original_url"] = base + "/original"
    else:
        payload["removed_at"] = item.removed_at
        payload["purge_after"] = space_catalog.purge_after(item, _storage(request).trash_days)
    return payload


def _authors(request: Request, items: list[SpaceItem]) -> dict[int, str]:
    ids = {item.added_by for item in items if item.added_by is not None}
    return usernames(request.app.state.users_db_path, ids)


def _page(
    request: Request, space_id: int, items: list[SpaceItem], actor_id: int, role: str, limit: int
) -> dict[str, Any]:
    authors = _authors(request, items)
    return {
        "items": [_item_json(request, space_id, i, actor_id, role, authors) for i in items],
        "next_before": items[-1].id if len(items) == limit else None,
    }


@router.get("/{space_id}/storage")
def space_storage(request: Request, space_id: int):
    _member(request, space_id)
    storage = _storage(request)
    return {
        "used_bytes": space_catalog.usage_bytes(_root(request, space_id)),
        "quota_bytes": storage.quota_bytes,
        "trash_days": storage.trash_days,
    }


@router.get("/{space_id}/items")
def list_space_items(
    request: Request,
    space_id: int,
    limit: int = Query(50, ge=1, le=space_catalog.MAX_PAGE),
    before: int | None = Query(None, gt=0),
):
    actor, role = _member(request, space_id)
    _purge(request, space_id)
    items = space_catalog.list_items(_root(request, space_id), limit, before)
    return _page(request, space_id, items, actor.id, role, limit)


@router.post("/{space_id}/items", status_code=201)
def add_space_item(request: Request, response: Response, space_id: int, payload: AddItemIn):
    actor, role = _member(request, space_id)
    if role not in {"contributor", "manager"}:
        raise HTTPException(403, "Visualizadores não adicionam itens")
    # Provided by server.py: resolves the id inside the caller's own library
    # only, with the same allow-list that guards /media/.
    source = request.app.state.private_original_for(payload.record_id)
    if source is None:
        raise HTTPException(404, _ITEM_NOT_FOUND)
    path, name = source
    try:
        item, created = space_catalog.add_item(
            _root(request, space_id), path, name, actor.id, _storage(request)
        )
    except SpaceQuotaExceeded as exc:
        raise HTTPException(507, "Cota do espaço excedida") from exc
    if not created:
        response.status_code = 200
    authors = _authors(request, [item])
    return {
        "item": _item_json(request, space_id, item, actor.id, role, authors),
        "created": created,
    }


@router.get("/{space_id}/items/{item_id}")
def space_item_detail(request: Request, space_id: int, item_id: int):
    actor, role = _member(request, space_id)
    try:
        item = space_catalog.get_item(_root(request, space_id), item_id)
    except SpaceItemNotFound as exc:
        raise HTTPException(404, _ITEM_NOT_FOUND) from exc
    authors = _authors(request, [item])
    return {"item": _item_json(request, space_id, item, actor.id, role, authors)}


@router.get("/{space_id}/items/{item_id}/thumbnail")
def space_item_thumbnail(request: Request, space_id: int, item_id: int):
    _member(request, space_id)
    try:
        thumb = space_catalog.item_thumbnail(
            _root(request, space_id), item_id, request.app.state.thumbnail_generator
        )
    except SpaceItemNotFound as exc:
        raise HTTPException(404, _ITEM_NOT_FOUND) from exc
    return FileResponse(
        thumb, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"}
    )


@router.get("/{space_id}/items/{item_id}/original")
def space_item_original(request: Request, space_id: int, item_id: int):
    _member(request, space_id)
    try:
        item, path = space_catalog.item_original(_root(request, space_id), item_id)
    except SpaceItemNotFound as exc:
        raise HTTPException(404, _ITEM_NOT_FOUND) from exc
    return FileResponse(
        path,
        media_type=item.mime_type,
        filename=item.original_name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/{space_id}/items/{item_id}/save")
def save_to_library(request: Request, response: Response, space_id: int, item_id: int):
    """Any member, viewers included, may keep a copy in their own library."""
    actor, _ = _member(request, space_id)
    try:
        item, path = space_catalog.item_original(_root(request, space_id), item_id)
    except SpaceItemNotFound as exc:
        raise HTTPException(404, _ITEM_NOT_FOUND) from exc
    try:
        intake = save_copy(
            actor,
            path,
            item.original_name,
            item.sha256,
            item.size_bytes,
            strategy=_storage(request).strategy,
            quota_bytes=request.app.state.account_quota_bytes,
            device_id=getattr(request.state, "iris_device_id", None),
            origin={"space_id": space_id, "space_item_id": item.id},
        )
    except LibraryQuotaExceeded as exc:
        raise HTTPException(507, "Cota da biblioteca excedida") from exc
    if intake.created and request.app.state.load_model:
        registry = request.app.state.backend_registry
        start_processing(actor, intake, lambda: registry.invalidate(actor.id))
    response.status_code = 201 if intake.created else 200
    return {"state": intake.state, "upload_id": intake.upload_id, "media_id": intake.media_id}


@router.delete("/{space_id}/items/{item_id}", status_code=204)
def remove_space_item(request: Request, space_id: int, item_id: int):
    actor, role = _member(request, space_id)
    try:
        space_catalog.remove_item(_root(request, space_id), item_id, actor.id, role)
    except SpaceItemNotFound as exc:
        raise HTTPException(404, _ITEM_NOT_FOUND) from exc
    except SpaceItemPermissionDenied as exc:
        raise HTTPException(403, "Sem permissão para remover este item") from exc
    return Response(status_code=204)


@router.get("/{space_id}/trash")
def list_space_trash(
    request: Request,
    space_id: int,
    limit: int = Query(50, ge=1, le=space_catalog.MAX_PAGE),
    before: int | None = Query(None, gt=0),
):
    """Items the actor could restore: all for a manager, own for a contributor."""
    actor, role = _member(request, space_id)
    _purge(request, space_id)
    items = space_catalog.list_trash(_root(request, space_id), actor.id, role, limit, before)
    return _page(request, space_id, items, actor.id, role, limit)


@router.post("/{space_id}/trash/{item_id}/restore")
def restore_space_item(request: Request, space_id: int, item_id: int):
    actor, role = _member(request, space_id)
    try:
        item = space_catalog.restore_item(_root(request, space_id), item_id, actor.id, role)
    except SpaceItemNotFound as exc:
        raise HTTPException(404, _ITEM_NOT_FOUND) from exc
    except SpaceItemPermissionDenied as exc:
        raise HTTPException(403, "Sem permissão para restaurar este item") from exc
    except SpaceItemConflict as exc:
        raise HTTPException(409, "O mesmo conteúdo já está no espaço") from exc
    authors = _authors(request, [item])
    return {"item": _item_json(request, space_id, item, actor.id, role, authors)}
