"""Fast checks for dependencies required by the private-server entry point."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _requirements() -> set[str]:
    requirements = set()
    for filename in ("requirements.txt", "requirements.in"):
        for line in (ROOT / filename).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            requirements.add(
                line.split("=", 1)[0]
                .split(">", 1)[0]
                .split("<", 1)[0]
                .split("[", 1)[0]
                .strip()
                .lower()
            )
    return requirements


def test_private_server_runtime_dependencies_are_declared():
    requirements = _requirements()
    assert {"fastapi", "itsdangerous", "pwdlib", "uvicorn"} <= requirements


def test_cpu_image_installs_the_cpu_onnx_runtime():
    dockerfile = (ROOT / "Dockerfile").read_text()
    requirements = (ROOT / "requirements.txt").read_text()
    assert "ARG IRIS_PROFILE=cpu" in dockerfile
    assert "onnxruntime-gpu" not in requirements
    assert "onnxruntime==" in requirements


def test_image_is_published_and_runs_as_the_host_user():
    compose = (ROOT / "docker-compose.yml").read_text()
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "image: ${IRIS_IMAGE:-ghcr.io/miguellopesdel/iris}:${IRIS_VERSION:-latest}" in compose
    assert 'user: "${IRIS_UID:-1000}:${IRIS_GID:-1000}"' in compose
    assert "ghcr.io/miguellopesdel/iris" in release
    assert "IRIS_PROFILE=${{ matrix.profile }}" in release
    assert "./scripts/test_release.sh" in release


def test_docker_context_excludes_android_sdk_and_emulator_data():
    dockerignore = (ROOT / ".dockerignore").read_text()
    assert ".android-sdk/" in dockerignore
    assert ".android-avd/" in dockerignore


def test_release_test_exercises_clean_compose_startup_and_authentication():
    script = (ROOT / "scripts" / "test_release.sh").read_text()
    assert "git archive HEAD" in script
    assert "git status --porcelain --untracked-files=all" in script
    assert "builds HEAD only" in script
    assert "build iris" in script
    assert "/healthz" in script
    assert "bootstrap_admin.py" in script
    assert "verify_server.py" in script


def test_upgrade_recovery_test_uses_isolated_synthetic_data_and_real_restore():
    script = (ROOT / "scripts" / "test_upgrade_recovery.sh").read_text()
    assert "git archive HEAD" in script
    assert "git diff --binary HEAD" in script
    assert 'git apply --binary --directory="$relative_dir/current"' in script
    assert ".iris-upgrade-test." in script
    assert "synthetic recovery password" in script
    assert "core.backup_scheduler run --pin" in script
    assert "core.instance_backup verify" in script
    assert "core.instance_backup restore" in script
    assert "verify_server 1" in script
    assert "docker buildx build --load" in script


def test_restore_recreates_container_after_atomic_root_swap():
    script = (ROOT / "scripts" / "server.sh").read_text()
    assert "docker compose up -d --force-recreate iris" in script


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
