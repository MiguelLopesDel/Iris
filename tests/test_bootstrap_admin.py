"""Regression coverage for first-run private server bootstrap."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from core.users_db import get_user_by_username
from scripts.bootstrap_admin import _default_legacy_db


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


def test_bootstrap_uses_the_supported_legacy_catalog_name(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    legacy = data_dir / "meme_compass_full_v1.db"
    legacy.touch()
    assert _default_legacy_db(data_dir) == legacy


def test_bootstrap_cli_migrates_supported_legacy_catalog_by_default(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "meme_compass_full_v1.db").touch()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    result = subprocess.run(
        [
            sys.executable, str(project_root / "scripts" / "bootstrap_admin.py"),
            "--data-dir", str(data_dir), "--media-root", str(media_dir),
            "--username", "admin", "--dry-run",
        ],
        input="a sufficiently strong password\na sufficiently strong password\n",
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(project_root)},
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "moveria" in result.stdout
