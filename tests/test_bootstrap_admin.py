"""Regression coverage for first-run private server bootstrap."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from core.users_db import get_user_by_username


def test_bootstrap_admin_creates_an_empty_private_library(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "data"
    result = subprocess.run(
        [
            sys.executable, str(project_root / "scripts" / "bootstrap_admin.py"),
            "--data-dir", str(data_dir), "--db", str(tmp_path / "missing.db"),
            "--media-root", str(tmp_path / "missing-media"), "--username", "admin",
        ],
        input="a sufficiently strong password\na sufficiently strong password\n",
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(project_root)},
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    user = get_user_by_username(data_dir / "users.db", "admin")
    assert user is not None
    assert user.is_admin
    assert user.db_path.is_file()
    assert user.media_root.is_dir()
