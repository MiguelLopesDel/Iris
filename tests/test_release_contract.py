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


def test_remote_access_is_not_tied_to_one_vpn():
    """O servidor publica em 127.0.0.1 e não deve exigir uma rede específica.

    Tailscale pode aparecer como exemplo; o que não pode é ser apresentado como
    a única forma, porque quem usa ZeroTier, WireGuard ou um proxy reverso tem
    de conseguir seguir o guia.
    """
    guide = (ROOT / "docs" / "server-deployment.md").read_text().lower()
    for alternativa in ("zerotier", "wireguard", "proxy reverso"):
        assert alternativa in guide, f"o guia não menciona {alternativa}"
    assert "única camada que publica" not in guide
