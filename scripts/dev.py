#!/usr/bin/env python3
"""Local Iris development sandbox.

Commands:
  python scripts/dev.py start             # disposable accounts, hot reload, no model
  python scripts/dev.py start --with-model # enables real semantic search/import
  python scripts/dev.py test              # compile, lint and test the repository
  python scripts/dev.py reset --yes       # deletes only the marked .iris-dev sandbox
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / ".iris-dev"
MARKER = ".iris-dev-sandbox"
ADMIN_PASSWORD = "iris-dev-admin"
MEMBER_PASSWORD = "iris-dev-member"


def sandbox_root(value: str | None) -> Path:
    root = Path(value).expanduser().resolve() if value else DEFAULT_ROOT.resolve()
    if root == PROJECT_ROOT or PROJECT_ROOT not in root.parents:
        raise ValueError("sandbox must be inside the repository and cannot be its root")
    return root


def seed_sandbox(root: Path) -> None:
    """Create a tiny, fully isolated two-account library if it does not exist."""
    marker = root / MARKER
    if marker.exists():
        return
    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f"{root} exists but is not a recognized Iris sandbox")
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("Disposable data created by scripts/dev.py\n", encoding="utf-8")

    # Imports occur only after the sandbox is known to be safe to initialize.
    sys.path.insert(0, str(PROJECT_ROOT))
    from PIL import Image

    from core.auth import hash_password
    from core.indexer_db import init_db
    from core.users_db import create_user

    data_dir = root / "data"
    accounts = (
        ("admin", ADMIN_PASSWORD, "Admin local", True, (61, 99, 184)),
        ("familia", MEMBER_PASSWORD, "Família local", False, (31, 132, 77)),
    )
    for username, password, display_name, is_admin, color in accounts:
        user = create_user(
            data_dir / "users.db",
            data_dir,
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            is_admin=is_admin,
        )
        init_db(user.db_path).close()
        image_path = user.media_root / f"demo-{username}.jpg"
        Image.new("RGB", (640, 420), color=color).save(image_path, "JPEG")
        with sqlite3.connect(user.db_path) as conn:
            conn.execute(
                "INSERT INTO memes (arquivo, caminho, embedding, file_size) VALUES (?, ?, ?, ?)",
                (image_path.name, str(image_path), b"\\0" * 16, image_path.stat().st_size),
            )
    print(f"Sandbox created at {root}")
    print(f"  admin / {ADMIN_PASSWORD}   (administrator)")
    print(f"  familia / {MEMBER_PASSWORD} (regular user)")


def start(root: Path, *, with_model: bool, host: str, port: int) -> int:
    seed_sandbox(root)
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
            "IRIS_DATA_DIR": str(root / "data"),
            "IRIS_SERVER_MODE": "private",
            "IRIS_SESSION_HTTPS_ONLY": "false",
            "IRIS_LOAD_MODEL": "1" if with_model else "0",
            "IRIS_LOG_FORMAT": "text",
        }
    )
    print(f"Starting local Iris at http://{host}:{port}")
    if not with_model:
        print("Fast mode: gallery, login, accounts, and UI work; AI search/import require --with-model.")
    command = [
        sys.executable, "-m", "uvicorn", "server:app", "--host", host,
        "--port", str(port), "--reload",
    ]
    return subprocess.call(command, cwd=PROJECT_ROOT, env=env)


def run_tests() -> int:
    commands = (
        [sys.executable, "-m", "compileall", "-q", "core", "routers", "scripts", "tests", "server.py"],
        [sys.executable, "-m", "ruff", "check", "core", "routers", "scripts", "tests", "server.py"],
        [sys.executable, "-m", "pytest", "-q"],
    )
    for command in commands:
        if subprocess.call(command, cwd=PROJECT_ROOT) != 0:
            return 1
    return 0


def reset(root: Path, *, confirmed: bool) -> int:
    if not confirmed:
        raise RuntimeError("reset requires --yes")
    marker = root / MARKER
    if not marker.is_file():
        raise RuntimeError(f"refusing to remove {root}: sandbox marker is missing")
    shutil.rmtree(root)
    print(f"Sandbox removed: {root}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="sandbox directory; default: .iris-dev")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--with-model", action="store_true")
    start_parser.add_argument("--host", default="127.0.0.1")
    start_parser.add_argument("--port", type=int, default=8501)
    reset_parser = subparsers.add_parser("reset")
    reset_parser.add_argument("--yes", action="store_true")
    subparsers.add_parser("test")
    args = parser.parse_args()
    try:
        root = sandbox_root(args.root)
        if args.command == "start":
            return start(root, with_model=args.with_model, host=args.host, port=args.port)
        if args.command == "reset":
            return reset(root, confirmed=args.yes)
        return run_tests()
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
