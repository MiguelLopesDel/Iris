"""When the session cookie carries ``Secure``.

``IRIS_SESSION_HTTPS_ONLY`` takes three values:

* ``true``  -- always ``Secure``: the browser sends the cookie only over HTTPS.
* ``false`` -- never ``Secure``.
* ``auto``  -- the default: ``Secure`` when the request reached Iris over HTTPS,
  either directly or through a proxy that says so with ``X-Forwarded-Proto``.
  Over plain HTTP (a private network, a mesh VPN), sign-in keeps working.

``X-Forwarded-Proto: https`` is honoured from any client, not only trusted
proxies: it can only make the cookie stricter, so forging it gains nothing.
"""
from __future__ import annotations

from collections.abc import Mapping

from starlette.types import ASGIApp, Message, Receive, Scope, Send

ALWAYS = "always"
NEVER = "never"
AUTO = "auto"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def https_only_mode(environ: Mapping[str, str], default: str = AUTO) -> str:
    value = environ.get("IRIS_SESSION_HTTPS_ONLY", "").strip().lower()
    if value in _TRUE:
        return ALWAYS
    if value in _FALSE:
        return NEVER
    if value == AUTO:
        return AUTO
    return default


def _arrived_over_https(scope: Scope) -> bool:
    if scope.get("scheme") in {"https", "wss"}:
        return True
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-proto":
            # A chain of proxies lists one scheme per hop; the first is the client's.
            return value.split(b",")[0].strip().lower() == b"https"
    return False


class SecureCookieOverHttps:
    """Adds ``Secure`` to ``cookie_name`` on responses to requests that arrived over HTTPS."""

    def __init__(self, app: ASGIApp, cookie_name: str) -> None:
        self.app = app
        self.prefix = cookie_name.encode() + b"="

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _arrived_over_https(scope):
            await self.app(scope, receive, send)
            return

        async def send_secure(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (name, value + b"; secure")
                    if name == b"set-cookie" and value.startswith(self.prefix) and b"; secure" not in value.lower()
                    else (name, value)
                    for name, value in message.get("headers", [])
                ]
            await send(message)

        await self.app(scope, receive, send_secure)
