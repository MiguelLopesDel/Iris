"""First-run web setup: creates the administrator of a private server with no accounts."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from core import first_setup
from core.auth import hash_password
from core.users_db import has_users

router = APIRouter(prefix="/api/setup", tags=["setup"])
logger = logging.getLogger("iris")

# One creation at a time: two submissions racing must not both create an admin.
_creation_lock = asyncio.Lock()


class SetupIn(BaseModel):
    code: str
    username: str
    display_name: str = ""
    password: str


def _paths(request: Request) -> tuple[Path, Path, Path]:
    state = request.app.state
    return Path(state.users_db_path), Path(state.data_dir), Path(state.setup_media_root)


def _limiter(request: Request) -> first_setup.AttemptLimiter:
    limiter = getattr(request.app.state, "setup_attempts", None)
    if limiter is None:
        limiter = first_setup.AttemptLimiter()
        request.app.state.setup_attempts = limiter
    return limiter


def _close_setup(request: Request, data_dir: Path) -> None:
    first_setup.clear_setup_code(data_dir)
    request.app.state.setup_required = False


@router.get("")
def setup_status(request: Request):
    """Public only while setup is open; afterwards it reveals nothing."""
    if not getattr(request.app.state, "setup_required", False):
        return {"required": False}
    _, data_dir, media_root = _paths(request)
    legacy = first_setup.find_legacy_library(data_dir, media_root)
    status = {"required": True, "legacy_library": legacy.has_db}
    if legacy.has_db:
        summary = first_setup.summarize_legacy_library(legacy)
        status["legacy_summary"] = {
            "items": summary.items,
            "with_file": summary.with_file,
            "missing": summary.missing,
            "outside": summary.outside,
        }
    return status


@router.post("", status_code=201)
async def complete_setup(request: Request, payload: SetupIn):
    if not getattr(request.app.state, "setup_required", False):
        raise HTTPException(409, "A configuração inicial já foi concluída")
    limiter = _limiter(request)
    if limiter.blocked():
        raise HTTPException(429, "Muitas tentativas com código errado; aguarde alguns minutos")
    users_db, data_dir, media_root = _paths(request)
    if not first_setup.code_matches(data_dir, payload.code):
        limiter.record_failure()
        logger.warning("setup_code_rejected")
        raise HTTPException(403, "Código de instalação incorreto")
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    async with _creation_lock:
        if not request.app.state.setup_required:
            raise HTTPException(409, "A configuração inicial já foi concluída")
        legacy = first_setup.find_legacy_library(data_dir, media_root)
        try:
            user = await run_in_threadpool(
                first_setup.create_first_admin,
                users_db, data_dir, legacy,
                username=payload.username,
                password_hash=password_hash,
                display_name=payload.display_name,
            )
        except first_setup.MigrationFailed as exc:
            if not exc.rolled_back:
                # The account exists: leave setup mode so the administrator can
                # sign in and sort out the files the message lists.
                _close_setup(request, data_dir)
                raise HTTPException(500, str(exc)) from exc
            # Nothing changed: setup stays open with the same code.
            raise HTTPException(507 if exc.out_of_space else 500, str(exc)) from exc
        except (first_setup.SetupError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception("setup_failed")
            if has_users(users_db):
                _close_setup(request, data_dir)
            raise HTTPException(500, "A configuração inicial falhou; veja o log do servidor") from exc
        _close_setup(request, data_dir)

    logger.info("setup_completed user_id=%s migrated_legacy=%s", user.id, legacy.has_db)
    request.session.clear()
    request.session.update({"user_id": user.id, "session_version": user.session_version})
    return {
        "ok": True,
        "migrated_legacy_library": legacy.has_db,
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "is_admin": user.is_admin,
        },
    }
