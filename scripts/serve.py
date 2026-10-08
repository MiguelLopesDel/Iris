"""Start the Iris server over HTTP or HTTPS, as IRIS_TLS says (see core/tls.py).

    python scripts/serve.py            # what the container runs

It prepares the certificate before uvicorn starts, so a bad configuration
stops here with a clear message instead of a server that answers nothing,
then replaces itself with uvicorn.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core import instance_settings, tls  # noqa: E402

HOST = "0.0.0.0"
PORT = "8501"


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


def uvicorn_args(environ: dict[str, str]) -> list[str]:
    data_dir = Path(environ.get("IRIS_DATA_DIR", "data"))
    args = ["-m", "uvicorn", "server:app", f"--host={HOST}", f"--port={PORT}"]
    mode = tls.mode_from_env(environ)
    if mode == "off":
        return args
    if mode == "self":
        names = tls.certificate_names(pairing_addresses(data_dir), environ.get("IRIS_TLS_NAMES", ""))
        files = tls.ensure_self_signed(data_dir, names)
    else:
        files = tls.custom_files(data_dir, environ)
    return args + [f"--ssl-certfile={files.certfile}", f"--ssl-keyfile={files.keyfile}"]


def main() -> int:
    try:
        args = uvicorn_args(dict(os.environ))
    except tls.TlsConfigError as exc:
        print(f"iris: {exc}", file=sys.stderr)
        return 2
    os.execv(sys.executable, [sys.executable, *args])
    return 0  # not reached


if __name__ == "__main__":
    raise SystemExit(main())
