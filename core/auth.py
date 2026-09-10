"""Password hashing and session payload helpers for Iris accounts."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()
_DUMMY_HASH = _password_hash.hash("iris-not-a-real-password")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("A senha precisa ter pelo menos 12 caracteres")
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    # Always execute one verification to make unknown usernames less distinguishable.
    return _password_hash.verify(password, password_hash or _DUMMY_HASH)


def load_or_create_secret(path: Path) -> str:
    configured = os.environ.get("IRIS_SECRET_KEY")
    if configured:
        return configured
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret)
    return secret
