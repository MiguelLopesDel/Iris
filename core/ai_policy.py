"""Who processes what with AI: the instance's choice and each account's.

The administrator decides where AI runs (``ai_processing``: on this server,
on processing computers, or nowhere); each account decides whether its photos
are processed at all (on by default). Every place that would run or hand out
AI work asks here, so the two choices are honoured the same way everywhere.
"""
from __future__ import annotations

from pathlib import Path

from core import instance_settings
from core.users_db import user_ai_enabled


def on_server(users_db_path: Path, user, load_model: bool) -> bool:
    """Whether this account's uploads get AI here, at upload time."""
    if not load_model:
        return False
    if instance_settings.ai_settings(users_db_path).mode != "server":
        return False
    return user_ai_enabled(users_db_path, user.id)


def for_workers(users_db_path: Path, user) -> bool:
    """Whether processing computers may take this account's items."""
    if instance_settings.ai_settings(users_db_path).mode != "worker":
        return False
    return user_ai_enabled(users_db_path, user.id)
