"""Opaque refresh tokens and short-lived signed device access tokens."""
from __future__ import annotations

import hashlib
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_access_token(secret: str, user_id: int, session_version: int, device_id: str, token_version: int) -> str:
    return URLSafeTimedSerializer(secret, salt="iris-device-access-v1").dumps({
        "user_id": user_id, "session_version": session_version,
        "device_id": device_id, "token_version": token_version,
    })


def read_access_token(secret: str, token: str, max_age_seconds: int = 900) -> dict | None:
    try:
        value = URLSafeTimedSerializer(secret, salt="iris-device-access-v1").loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    return value if isinstance(value, dict) else None
