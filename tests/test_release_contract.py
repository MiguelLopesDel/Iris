"""Fast checks for dependencies required by the private-server entry point."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _requirements() -> set[str]:
    return {
        line.split("=", 1)[0].split(">", 1)[0].split("<", 1)[0].split("[", 1)[0].strip().lower()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line and not line.startswith("#")
    }


def test_private_server_runtime_dependencies_are_declared():
    requirements = _requirements()
    assert {"fastapi", "itsdangerous", "pwdlib", "uvicorn"} <= requirements


def test_cpu_image_has_the_packages_needed_for_insightface_build():
    dockerfile = (ROOT / "Dockerfile").read_text()
    requirements = (ROOT / "requirements.txt").read_text()
    assert "g++" in dockerfile
    assert "onnxruntime-gpu" not in requirements
    assert "onnxruntime==" in requirements


def test_release_test_exercises_clean_compose_startup_and_authentication():
    script = (ROOT / "scripts" / "test_release.sh").read_text()
    assert "git archive HEAD" in script
    assert "build iris" in script
    assert "/healthz" in script
    assert "bootstrap_admin.py" in script
    assert "verify_server.py" in script


def test_server_port_is_configurable_without_exposing_the_container():
    compose = (ROOT / "docker-compose.yml").read_text()
    script = (ROOT / "scripts" / "server.sh").read_text()
    assert '"127.0.0.1:${IRIS_PORT:-8501}:8501"' in compose
    assert "port <1024-65535>" in script
    assert "tailscale serve --bg http://127.0.0.1:" in script
