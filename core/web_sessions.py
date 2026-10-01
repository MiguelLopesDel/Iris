"""Browser sessions are devices too: listed, revocable, and checked on every request.

A cookie session carries the id of the device row created when the browser
signed in. Revoking that device -- from any other session -- ends the browser
session on its next request, exactly as a phone's tokens stop working.
"""
from __future__ import annotations

import secrets
from collections.abc import MutableMapping
from pathlib import Path

from core.device_tokens import token_hash
from core.users_db import (
    IrisDevice,
    IrisUser,
    create_device,
    get_device,
    revoke_device,
    touch_device,
)

WEB_PLATFORM = "web"

_BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
_SYSTEMS = (
    ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
    ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"), ("Linux", "Linux"),
)


def describe_browser(user_agent: str) -> str:
    browser = next((name for marker, name in _BROWSERS if marker in user_agent), "Navegador")
    system = next((name for marker, name in _SYSTEMS if marker in user_agent), "")
    return f"{browser} no {system}" if system else browser


def _new_browser_device(users_db: Path, user: IrisUser, user_agent: str) -> IrisDevice:
    # Browsers never refresh tokens: the stored hash matches no token anyone holds.
    return create_device(
        users_db, user.id, describe_browser(user_agent), WEB_PLATFORM, token_hash(secrets.token_urlsafe(32))
    )


def _bind(session: MutableMapping, user: IrisUser, device: IrisDevice) -> None:
    session.update({
        "user_id": user.id,
        "session_version": user.session_version,
        "device_id": device.id,
        "device_token_version": device.token_version,
    })


def start_web_session(session: MutableMapping, users_db: Path, user: IrisUser, user_agent: str) -> IrisDevice:
    """Sign a browser in as a new device of ``user``."""
    session.clear()
    device = _new_browser_device(users_db, user, user_agent)
    _bind(session, user, device)
    return device


def web_session_device(session: MutableMapping, users_db: Path, user: IrisUser, user_agent: str) -> str | None:
    """The device id behind an authenticated cookie session, or ``None`` if it was revoked.

    Sessions opened before browsers became devices get one on first use, so an
    upgrade signs nobody out.
    """
    device_id = session.get("device_id")
    if not device_id:
        device = _new_browser_device(users_db, user, user_agent)
        _bind(session, user, device)
        return device.id
    device = get_device(users_db, str(device_id))
    if (
        device is None
        or device.revoked_at
        or device.user_id != user.id
        or device.token_version != session.get("device_token_version")
    ):
        return None
    touch_device(users_db, device.id)
    return device.id


def end_web_session(session: MutableMapping, users_db: Path) -> None:
    user_id, device_id = session.get("user_id"), session.get("device_id")
    if isinstance(user_id, int) and device_id:
        revoke_device(users_db, user_id, str(device_id))
    session.clear()
