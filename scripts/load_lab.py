#!/usr/bin/env python3
"""Create and run a disposable multi-account Iris load laboratory.

Typical use (three commands, all on one computer):
  python scripts/load_lab.py prepare --users 50 --records-per-user 100
  python scripts/load_lab.py serve
  python scripts/load_lab.py run --url http://127.0.0.1:8501 --uploaders 50 --viewers 50 --searchers 20
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / ".iris-load"
MARKER = ".iris-load-lab"


def lab_root(value: str | None) -> Path:
    root = Path(value).expanduser().resolve() if value else DEFAULT_ROOT.resolve()
    if root == PROJECT_ROOT or PROJECT_ROOT not in root.parents:
        raise ValueError("lab must be inside the repository and outside its root")
    return root


def accounts_path(root: Path) -> Path:
    return root / "accounts.json"


def prepare(root: Path, *, users: int, records_per_user: int) -> int:
    if users < 1 or records_per_user < 1:
        raise ValueError("users and records-per-user must be positive")
    marker = root / MARKER
    if marker.exists():
        raise RuntimeError("lab already exists; reset it before preparing again")
    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f"{root} exists but is not a recognized Iris lab")
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("Disposable data created by scripts/load_lab.py\n", encoding="utf-8")
    sys.path.insert(0, str(PROJECT_ROOT))
    from PIL import Image

    from core.auth import hash_password
    from core.indexer_db import init_db
    from core.users_db import create_user

    data_dir = root / "data"
    credentials: list[dict[str, str]] = []
    for number in range(1, users + 1):
        username = f"load-{number:03d}"
        password = f"iris-load-{number:03d}-only"
        user = create_user(
            data_dir / "users.db", data_dir, username=username,
            password_hash=hash_password(password), is_admin=number == 1,
        )
        init_db(user.db_path).close()
        image = user.media_root / "load-sample.jpg"
        Image.new("RGB", (32, 32), color=(number % 255, 80, 150)).save(image, "JPEG")
        rows = [
            (f"sample-{record:05d}.jpg", str(image), b"\0" * 16, image.stat().st_size)
            for record in range(records_per_user)
        ]
        with sqlite3.connect(user.db_path) as conn:
            conn.executemany(
                "INSERT INTO memes (arquivo, caminho, embedding, file_size) VALUES (?, ?, ?, ?)", rows
            )
        credentials.append({"username": username, "password": password})
    output = accounts_path(root)
    output.write_text(json.dumps(credentials), encoding="utf-8")
    os.chmod(output, 0o600)
    print(f"Lab ready: {users} accounts, {records_per_user} records per account at {root}")
    print("Credentials exist only in .iris-load/accounts.json (mode 0600).")
    return 0


def serve(root: Path, *, with_model: bool, port: int) -> int:
    if not (root / MARKER).is_file() or not accounts_path(root).is_file():
        raise RuntimeError("lab is missing; run prepare first")
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
    print(f"Iris lab at http://127.0.0.1:{port}")
    if not with_model:
        print("No model: measures login, gallery, upload, and HTTP contention; AI search will fail.")
    return subprocess.call(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT_ROOT, env=env,
    )


def run(root: Path, args: argparse.Namespace) -> int:
    credentials = accounts_path(root)
    if not credentials.is_file():
        raise RuntimeError("lab is missing; run prepare first")
    if max(args.viewers, args.searchers, args.uploaders) > len(json.loads(credentials.read_text())):
        raise ValueError("scenario requests more people than prepared accounts")
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    common = ["--url", args.url, "--accounts-file", str(credentials), "--expect-private"]
    commands: list[list[str]] = []
    if args.viewers:
        commands.append([
            sys.executable, "scripts/load_test.py", *common, "--scenario", "browse",
            "--concurrency", str(args.viewers), "--requests", str(args.viewers * args.browse_requests),
            "--output", str(reports / "browse.json"),
        ])
    if args.searchers:
        commands.append([
            sys.executable, "scripts/load_test.py", *common, "--scenario", "search",
            "--concurrency", str(args.searchers), "--requests", str(args.searchers * args.search_requests),
            "--output", str(reports / "search.json"),
        ])
    if args.uploaders:
        commands.append([
            sys.executable, "scripts/upload_pressure.py", "--url", args.url,
            "--accounts-file", str(credentials), "--uploaders", str(args.uploaders),
            "--upload-mib", str(args.upload_mib), "--upload-mbps", str(args.upload_mbps),
            "--max-total-gib", str(args.max_total_gib), "--output", str(reports / "uploads.json"),
        ])
    processes = [subprocess.Popen(command, cwd=PROJECT_ROOT) for command in commands]
    outcomes = [process.wait() for process in processes]
    print(f"Reports: {reports}")
    return 0 if not any(outcomes) else 1


def reset(root: Path, confirmed: bool) -> int:
    if not confirmed:
        raise RuntimeError("reset requires --yes")
    if not (root / MARKER).is_file():
        raise RuntimeError("refusing to remove directory without the lab marker")
    shutil.rmtree(root)
    print(f"Lab removed: {root}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="default: .iris-load")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--users", type=int, default=50)
    prepare_parser.add_argument("--records-per-user", type=int, default=100)
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--with-model", action="store_true")
    serve_parser.add_argument("--port", type=int, default=8501)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--url", default="http://127.0.0.1:8501")
    run_parser.add_argument("--viewers", type=int, default=50)
    run_parser.add_argument("--searchers", type=int, default=0)
    run_parser.add_argument("--uploaders", type=int, default=0)
    run_parser.add_argument("--browse-requests", type=int, default=20)
    run_parser.add_argument("--search-requests", type=int, default=10)
    run_parser.add_argument("--upload-mib", type=int, default=8)
    run_parser.add_argument("--upload-mbps", type=float, default=10)
    run_parser.add_argument("--max-total-gib", type=float, default=2.0)
    reset_parser = subparsers.add_parser("reset")
    reset_parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    try:
        root = lab_root(args.root)
        if args.command == "prepare":
            return prepare(root, users=args.users, records_per_user=args.records_per_user)
        if args.command == "serve":
            return serve(root, with_model=args.with_model, port=args.port)
        if args.command == "run":
            return run(root, args)
        return reset(root, args.yes)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
