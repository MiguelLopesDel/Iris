"""Start the Iris server: HTTP for the browser, plus HTTPS for devices when IRIS_TLS asks.

    python scripts/serve.py            # what the container runs

The web interface always answers plain HTTP on port 8501, so setting up and
using the server in a browser never shows a certificate warning, as with
other self-hosted media servers. With IRIS_TLS=self or custom, the same app
also answers HTTPS on port 8443 for devices: the pairing code sends phones
there, and they trust it by the identity key the code carries
(see core/tls.py and core/server_identity.py).

It prepares the certificate before anything listens, so a bad configuration
stops here with a clear message instead of a server that answers nothing.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core import instance_settings, tls  # noqa: E402

HOST = "0.0.0.0"


def pairing_addresses(data_dir: Path) -> list[str]:
    """The addresses offered to devices, which the self-signed certificate should name."""
    users_db = data_dir / "users.db"
    try:
        if users_db.exists():
            value = instance_settings.resolve_all(users_db)["pairing_addresses"].value
        else:
            value = os.environ.get("IRIS_PAIRING_ADDRESSES", "")
    except Exception:
        # Never block the start on a settings problem; the certificate still names loopback.
        value = os.environ.get("IRIS_PAIRING_ADDRESSES", "")
    return str(value or "").split()


def tls_files(environ: dict[str, str]) -> tls.TlsFiles | None:
    """The certificate for the HTTPS listener, or None when IRIS_TLS is off."""
    data_dir = Path(environ.get("IRIS_DATA_DIR", "data"))
    mode = tls.mode_from_env(environ)
    if mode == "off":
        return None
    if mode == "self":
        names = tls.certificate_names(pairing_addresses(data_dir), environ.get("IRIS_TLS_NAMES", ""))
        return tls.ensure_self_signed(data_dir, names)
    return tls.custom_files(data_dir, environ)


def _ports(environ: dict[str, str]) -> tuple[int, int]:
    return int(environ.get("IRIS_HTTP_PORT", "8501")), int(environ.get("IRIS_HTTPS_PORT", "8443"))


def _serve_both(files: tls.TlsFiles, http_port: int, https_port: int) -> None:
    import uvicorn

    class Secondary(uvicorn.Server):
        # The primary owns the signals; this one only follows it.
        def capture_signals(self):  # type: ignore[override]
            import contextlib

            return contextlib.nullcontext()

    class Primary(uvicorn.Server):
        def __init__(self, config: uvicorn.Config, follower: uvicorn.Server) -> None:
            super().__init__(config)
            self.follower = follower

        def handle_exit(self, sig, frame) -> None:  # type: ignore[override]
            super().handle_exit(sig, frame)
            self.follower.should_exit = self.should_exit
            self.follower.force_exit = self.force_exit

    # One app, two listeners: the HTTP one runs its startup and shutdown.
    https = Secondary(uvicorn.Config(
        "server:app", host=HOST, port=https_port, lifespan="off",
        ssl_certfile=str(files.certfile), ssl_keyfile=str(files.keyfile),
    ))
    http = Primary(uvicorn.Config("server:app", host=HOST, port=http_port, lifespan="on"), https)

    async def run() -> None:
        first = asyncio.create_task(http.serve())
        # Devices wait until the app has started: nothing answers half-ready.
        while not http.started:
            if first.done():
                await first
                return
            await asyncio.sleep(0.05)
        second = asyncio.create_task(https.serve())
        try:
            await asyncio.gather(first, second)
        finally:
            http.should_exit = https.should_exit = True

    asyncio.run(run())


def main() -> int:
    environ = dict(os.environ)
    try:
        files = tls_files(environ)
    except tls.TlsConfigError as exc:
        print(f"iris: {exc}", file=sys.stderr)
        return 2
    http_port, https_port = _ports(environ)
    if files is None:
        os.execv(sys.executable, [
            sys.executable, "-m", "uvicorn", "server:app", f"--host={HOST}", f"--port={http_port}",
        ])
    _serve_both(files, http_port, https_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
