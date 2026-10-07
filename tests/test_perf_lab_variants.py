"""Lab middleware variants swap exactly the intended piece of the stack."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PRINT_STACK = (
    "from scripts.perf_lab import variants\n"
    "print(' > '.join(getattr(m.kwargs.get('dispatch'), '__qualname__', '') or m.cls.__name__"
    " for m in variants.app.user_middleware))\n"
)


def _stack(tmp_path: Path, variant: str) -> list[str]:
    env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), IRIS_DATA_DIR=str(tmp_path / "data"),
               IRIS_SERVER_MODE="private", PERF_LAB_VARIANT=variant)
    result = subprocess.run([sys.executable, "-c", _PRINT_STACK], cwd=PROJECT_ROOT, env=env,
                            capture_output=True, text=True, check=True)
    return result.stdout.strip().splitlines()[-1].split(" > ")


@pytest.mark.parametrize(("variant", "expected_last_two"), [
    ("normal", ["log_request", "authenticate_library_request"]),
    ("trivial_auth_http", ["log_request", "trivial_auth_dispatch"]),
    ("trivial_auth_asgi", ["log_request", "TrivialAuthASGI"]),
    ("trivial_auth_and_log_asgi", ["LogASGI", "TrivialAuthASGI"]),
    ("real_auth_join_inline", ["log_request", "_real_auth.<locals>.dispatch"]),
])
def test_each_variant_replaces_only_its_layer(tmp_path: Path, variant, expected_last_two):
    stack = _stack(tmp_path, variant)
    assert stack[-2:] == expected_last_two
    assert "SessionMiddleware" in stack and "GZipMiddleware" in stack


def test_no_gzip_drops_compression_only(tmp_path: Path):
    stack = _stack(tmp_path, "no_gzip")
    assert "GZipMiddleware" not in stack
    assert stack[-2:] == ["log_request", "authenticate_library_request"]
