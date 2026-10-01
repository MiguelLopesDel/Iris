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



def _docker_stub(tmp_path: Path, *, unpublished: str = "") -> Path:
    """Stub docker that logs the image version each command asks for."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "docker", f"""
echo "docker $* IRIS_VERSION=$IRIS_VERSION" >> "{tmp_path}/docker.log"
if [ "$1" = info ]; then echo '{{"runc":{{}}}}'; fi
if [ "$1 $2" = "compose pull" ] && [ "$IRIS_VERSION" = "{unpublished}" ]; then exit 1; fi
exit 0
""")
    _stub(bin_dir, "curl", "exit 0\n")
    _stub(bin_dir, "nvidia-smi", "exit 9\n")
    return bin_dir


def _env(tmp_path: Path, bin_dir: Path, **extra: str) -> dict:
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", **extra,
    }


def _files(project: Path, version: str) -> None:
    (project / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "scripts" / "server.sh", project / "scripts" / "server.sh")
    for name in (".env.example", "docker-compose.yml", "docker-compose.gpu.yml"):
        shutil.copy(ROOT / name, project / name)
    (project / "pyproject.toml").write_text(f'[project]\nname = "iris"\nversion = "{version}"\n')


def _released_clone(tmp_path: Path, env: dict) -> Path:
    """Upstream with releases v0.4.0 and v0.5.0 and main ahead (0.6.0, untagged); a clone of main."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    def git(*args, cwd=upstream):
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    (upstream / ".gitignore").write_text(".env\ndata/\nmedia/\nbackups/\n")
    for version, tag in (("0.4.0", "v0.4.0"), ("0.5.0", "v0.5.0"), ("0.6.0", None)):
        _files(upstream, version)
        (upstream / "CHANGES").write_text(version)
        git("add", "-A")
        git("commit", "-q", "-m", version)
        if tag:
            git("tag", tag)
    project = tmp_path / "Iris"
    git("clone", "-q", str(upstream), str(project), cwd=tmp_path)
    return project


def _head(project: Path, env: dict) -> str:
    return subprocess.run(["git", "describe", "--tags", "--always"], cwd=project, env=env,
                          check=True, capture_output=True, text=True).stdout.strip()


def _server(project: Path, env: dict, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["bash", "scripts/server.sh", *args], cwd=project, env=env, capture_output=True, text=True, timeout=60
    )
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result


def _docker(tmp_path: Path) -> list[str]:
    return (tmp_path / "docker.log").read_text().splitlines()


def _pulled(tmp_path: Path) -> list[str]:
    return [line.rsplit("IRIS_VERSION=", 1)[1] for line in _docker(tmp_path)
            if line.startswith("docker compose pull")]


def test_update_moves_to_the_newest_release_and_never_to_main(tmp_path: Path) -> None:
    """A server on main used to get main's code with the previous release's image."""
    env = _env(tmp_path, _docker_stub(tmp_path))
    project = _released_clone(tmp_path, env)
    assert (project / "CHANGES").read_text() == "0.6.0"

    result = _server(project, env, "update")
    assert "Release: v0.5.0" in result.stdout
    assert _head(project, env) == "v0.5.0" and (project / "CHANGES").read_text() == "0.5.0"
    assert set(_pulled(tmp_path)) == {"0.5.0"}
    assert any(line.startswith("docker compose up -d IRIS_VERSION=0.5.0") for line in _docker(tmp_path))


def test_update_stays_on_a_pinned_release(tmp_path: Path) -> None:
    env = _env(tmp_path, _docker_stub(tmp_path))
    project = _released_clone(tmp_path, env)
    (project / ".env").write_text((ROOT / ".env.example").read_text().replace("IRIS_VERSION=latest", "IRIS_VERSION=0.4.0"))
    _server(project, env, "update")
    assert _head(project, env) == "v0.4.0"
    assert set(_pulled(tmp_path)) == {"0.4.0"}


def test_a_release_still_being_published_changes_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path, _docker_stub(tmp_path, unpublished="0.5.0"))
    project = _released_clone(tmp_path, env)
    before = _head(project, env)
    result = _server(project, env, "update", ok=False)
    assert "not available yet" in result.stderr and "nothing was changed" in result.stderr
    assert _head(project, env) == before
    assert not any(" up " in line for line in _docker(tmp_path))


def test_install_from_a_clone_of_main_runs_the_newest_release(tmp_path: Path) -> None:
    env = _env(tmp_path, _docker_stub(tmp_path))
    project = _released_clone(tmp_path, env)
    _server(project, env, "install", "--cpu")
    assert _head(project, env) == "v0.5.0"
    assert set(_pulled(tmp_path)) == {"0.5.0"}


def test_without_git_the_version_in_the_files_is_run(tmp_path: Path) -> None:
    env = _env(tmp_path, _docker_stub(tmp_path))
    project = tmp_path / "Iris"
    _files(project, "0.9.1")
    _server(project, env, "install", "--cpu")
    assert set(_pulled(tmp_path)) == {"0.9.1"}


def test_without_git_an_unpublished_version_is_built_here(tmp_path: Path) -> None:
    env = _env(tmp_path, _docker_stub(tmp_path, unpublished="0.9.1"))
    project = tmp_path / "Iris"
    _files(project, "0.9.1")
    result = _server(project, env, "install", "--cpu")
    assert "building it locally" in result.stdout
    assert any(line.startswith("docker compose build iris IRIS_VERSION=0.9.1") for line in _docker(tmp_path))
