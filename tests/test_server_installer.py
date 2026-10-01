"""scripts/server.sh install, run for real against stub docker/nvidia-smi/curl commands."""
from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _install(tmp_path: Path, *, gpu: bool, toolkit: bool, args: tuple[str, ...] = (), folder: str = "Iris"):
    project = tmp_path / folder
    (project / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "scripts" / "server.sh", project / "scripts" / "server.sh")
    for name in (".env.example", "docker-compose.yml", "docker-compose.gpu.yml"):
        shutil.copy(ROOT / name, project / name)
    (project / "data").mkdir(exist_ok=True)
    (project / "data" / "setup_code").write_text("ABCD-EFGH\n")

    bin_dir = tmp_path / f"bin-{folder}"
    bin_dir.mkdir(exist_ok=True)
    runtimes = '{"nvidia":{},"runc":{}}' if toolkit else '{"runc":{}}'
    _stub(bin_dir, "docker", f"""
echo "docker $*" >> "{tmp_path}/docker.log"
if [ "$1" = info ]; then echo '{runtimes}'; fi
exit 0
""")
    _stub(bin_dir, "curl", "exit 0\n")
    if gpu:
        _stub(bin_dir, "nvidia-smi", 'echo "GPU 0: NVIDIA GeForce RTX 4050 Laptop GPU (UUID: GPU-x)"\n')
    else:
        # Shadows any real nvidia-smi on the test machine: no driver answers.
        _stub(bin_dir, "nvidia-smi", 'echo "NVIDIA-SMI has failed" >&2\nexit 9\n')
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", "scripts/server.sh", "install", *args],
        cwd=project, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return project, result


def _compose_file(project: Path) -> str | None:
    for line in (project / ".env").read_text().splitlines():
        if line.startswith("COMPOSE_FILE="):
            return line.split("=", 1)[1]
    return None


def test_without_a_gpu_the_cpu_image_is_used_and_setup_is_offered(tmp_path: Path) -> None:
    project, result = _install(tmp_path, gpu=False, toolkit=False)
    assert _compose_file(project) is None
    assert "no NVIDIA GPU found" in result.stdout
    assert "/setup" in result.stdout and "ABCD-EFGH" in result.stdout
    assert "create-admin" not in result.stdout


def test_a_reachable_gpu_selects_the_nvidia_image(tmp_path: Path) -> None:
    project, result = _install(tmp_path, gpu=True, toolkit=True)
    assert _compose_file(project) == "docker-compose.yml:docker-compose.gpu.yml"
    assert "RTX 4050" in result.stdout


def test_a_gpu_docker_cannot_use_keeps_the_cpu_image_and_says_why(tmp_path: Path) -> None:
    project, result = _install(tmp_path, gpu=True, toolkit=False)
    assert _compose_file(project) is None
    assert "NVIDIA Container Toolkit" in result.stderr


def test_flags_override_detection_in_both_directions(tmp_path: Path) -> None:
    project, _ = _install(tmp_path, gpu=False, toolkit=False, args=("--gpu",))
    assert _compose_file(project) == "docker-compose.yml:docker-compose.gpu.yml"
    project, _ = _install(tmp_path, gpu=True, toolkit=True, args=("--cpu",))
    assert _compose_file(project) is None
    assert "#COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml" in (project / ".env").read_text()


def test_installs_in_same_named_folders_get_different_compose_projects(tmp_path: Path) -> None:
    first, _ = _install(tmp_path / "a", gpu=False, toolkit=False)
    second, _ = _install(tmp_path / "b", gpu=False, toolkit=False)
    name = lambda project: next(  # noqa: E731
        line for line in (project / ".env").read_text().splitlines() if line.startswith("COMPOSE_PROJECT_NAME=")
    )
    assert first.name == second.name == "Iris"
    assert name(first) != name(second)
    assert name(first).startswith("COMPOSE_PROJECT_NAME=iris-")
