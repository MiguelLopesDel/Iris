"""scripts/server.sh install, run for real against stub docker/nvidia-smi/curl commands."""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _service_user_stubs(tmp_path: Path, bin_dir: Path) -> None:
    # `install` hardens: a system user owns the library. Ownership changes are
    # recorded, not made; everything else sudo runs as the test user.
    _stub(bin_dir, "getent", 'echo "$2:x:2000:2000::/nonexistent:/usr/sbin/nologin"\n')
    _stub(bin_dir, "sudo", f"""
echo "sudo $*" >> "{tmp_path}/sudo.log"
case "$1" in chown|useradd) exit 0 ;; esac
exec "$@"
""")


def _install(
    tmp_path: Path, *, gpu: bool, toolkit: bool, args: tuple[str, ...] = (), folder: str = "Iris",
    env_settings: dict[str, str] | None = None,
):
    project = tmp_path / folder
    (project / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "scripts" / "server.sh", project / "scripts" / "server.sh")
    for name in (".env.example", "docker-compose.yml", "docker-compose.gpu.yml"):
        shutil.copy(ROOT / name, project / name)
    data = project / "data"
    if env_settings:
        # An administrator who edited .env before installing.
        lines = (ROOT / ".env.example").read_text().splitlines()
        lines = [line for line in lines if line.split("=", 1)[0] not in env_settings]
        lines += [f"{key}={value}" for key, value in env_settings.items()]
        (project / ".env").write_text("\n".join(lines) + "\n")
        data = Path(env_settings.get("IRIS_DATA_DIR", data))
    data.mkdir(parents=True, exist_ok=True)
    (data / "setup_code").write_text("ABCD-EFGH\n")

    bin_dir = tmp_path / f"bin-{folder}"
    bin_dir.mkdir(exist_ok=True)
    runtimes = '{"nvidia":{},"runc":{}}' if toolkit else '{"runc":{}}'
    _stub(bin_dir, "docker", f"""
echo "docker $*" >> "{tmp_path}/docker.log"
if [ "$1" = info ]; then echo '{runtimes}'; fi
exit 0
""")
    _stub(bin_dir, "curl", "exit 0\n")
    _service_user_stubs(tmp_path, bin_dir)
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


def test_the_library_can_live_on_another_disk(tmp_path: Path) -> None:
    disk = tmp_path / "large-disk" / "iris"
    project, result = _install(tmp_path, gpu=False, toolkit=False, env_settings={
        "IRIS_DATA_DIR": str(disk / "data"), "IRIS_MEDIA_DIR": str(disk / "media"),
    })

    for folder in (disk / "data", disk / "media"):
        assert folder.is_dir() and stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert not (project / "data").exists() and not (project / "media").exists()
    # The setup code is read from where the library is.
    assert "ABCD-EFGH" in result.stdout
    assert "different filesystems" not in result.stderr
    compose = (project / "docker-compose.yml").read_text()
    assert "${IRIS_DATA_DIR:-./data}:/app/data" in compose
    assert "${IRIS_MEDIA_DIR:-./media}:/app/media" in compose


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
    _service_user_stubs(tmp_path, bin_dir)
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


def _released_clone(tmp_path: Path, env: dict, *, main_tag: str | None = None) -> Path:
    """Upstream with releases v0.4.0 and v0.5.0 and main ahead (0.6.0, untagged
    unless ``main_tag``); a clone of main."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    def git(*args, cwd=upstream):
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    (upstream / ".gitignore").write_text(".env\ndata/\nmedia/\nbackups/\n")
    for version, tag in (("0.4.0", "v0.4.0"), ("0.5.0", "v0.5.0"), ("0.6.0", main_tag)):
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


def test_a_pre_release_is_never_picked_as_the_newest_release(tmp_path: Path) -> None:
    # git sorts v0.6.0-rc.1 above v0.5.0 (and even above v0.6.0).
    env = _env(tmp_path, _docker_stub(tmp_path))
    project = _released_clone(tmp_path, env, main_tag="v0.6.0-rc.1")
    _server(project, env, "update")
    assert _head(project, env) == "v0.5.0"
    assert set(_pulled(tmp_path)) == {"0.5.0"}


def test_a_pre_release_runs_when_pinned(tmp_path: Path) -> None:
    env = _env(tmp_path, _docker_stub(tmp_path))
    project = _released_clone(tmp_path, env, main_tag="v0.6.0-rc.1")
    (project / ".env").write_text(
        (ROOT / ".env.example").read_text().replace("IRIS_VERSION=latest", "IRIS_VERSION=0.6.0-rc.1")
    )
    result = _server(project, env, "update")
    assert "Release: v0.6.0-rc.1" in result.stdout
    assert _head(project, env) == "v0.6.0-rc.1"
    assert set(_pulled(tmp_path)) == {"0.6.0-rc.1"}


def _listening_project(tmp_path: Path, addresses: tuple[str, ...] = ()) -> tuple[Path, dict]:
    """An installed project whose stubs log docker calls and the URLs curl is asked for."""
    project = tmp_path / "Iris"
    _files(project, "0.9.1")
    (project / "data").mkdir()
    bin_dir = _docker_stub(tmp_path)
    _stub(bin_dir, "curl", f'echo "$@" >> "{tmp_path}/curl.log"\nexit 0\n')
    _service_user_stubs(tmp_path, bin_dir)
    interfaces = "\n".join(f"2: eth{i}    inet {a}/24 brd 0.0.0.0 scope global eth{i}" for i, a in enumerate(addresses))
    _stub(bin_dir, "ip", f"cat <<'EOF2'\n1: lo    inet 127.0.0.1/8 scope host lo\n{interfaces}\nEOF2\n")
    env = _env(tmp_path, bin_dir)
    _server(project, env, "install", "--cpu")
    return project, env


def _env_value(project: Path, key: str) -> str | None:
    lines = [line.split("=", 1)[1] for line in (project / ".env").read_text().splitlines() if line.startswith(f"{key}=")]
    return lines[-1] if lines else None


def test_listen_on_an_address_of_this_host(tmp_path: Path) -> None:
    project, env = _listening_project(tmp_path, addresses=("100.99.1.2",))
    assert _env_value(project, "IRIS_BIND") == "127.0.0.1"

    result = _server(project, env, "listen", "100.99.1.2")
    assert _env_value(project, "IRIS_BIND") == "100.99.1.2"
    assert "plain HTTP" in result.stdout
    # Health is checked where Iris now listens.
    assert "http://100.99.1.2:8501/healthz" in (tmp_path / "curl.log").read_text().splitlines()[-1]


def test_listen_refuses_an_address_this_host_does_not_have(tmp_path: Path) -> None:
    project, env = _listening_project(tmp_path, addresses=("100.99.1.2",))
    result = _server(project, env, "listen", "100.99.1.3", ok=False)
    assert "No network interface" in result.stderr
    assert _env_value(project, "IRIS_BIND") == "127.0.0.1"


def test_listening_on_every_network_needs_saying_so(tmp_path: Path) -> None:
    project, env = _listening_project(tmp_path)
    refused = _server(project, env, "listen", "0.0.0.0", ok=False)
    assert "--all-interfaces" in refused.stderr
    assert _env_value(project, "IRIS_BIND") == "127.0.0.1"

    _server(project, env, "listen", "0.0.0.0", "--all-interfaces")
    assert _env_value(project, "IRIS_BIND") == "0.0.0.0"
    assert "http://127.0.0.1:8501/healthz" in (tmp_path / "curl.log").read_text().splitlines()[-1]


def test_listen_warns_when_the_cookie_setting_blocks_browser_sign_in(tmp_path: Path) -> None:
    project, env = _listening_project(tmp_path, addresses=("192.168.1.20",))
    (project / ".env").write_text((project / ".env").read_text().replace(
        "IRIS_SESSION_HTTPS_ONLY=auto", "IRIS_SESSION_HTTPS_ONLY=true"))
    result = _server(project, env, "listen", "192.168.1.20")
    assert "cannot sign in over plain HTTP" in result.stderr


def _sudo(tmp_path: Path) -> list[str]:
    log = tmp_path / "sudo.log"
    return log.read_text().splitlines() if log.exists() else []


def test_install_hands_the_library_to_a_system_user(tmp_path: Path) -> None:
    project, result = _install(tmp_path, gpu=False, toolkit=False)

    env = (project / ".env").read_text()
    assert "IRIS_UID=2000" in env and "IRIS_GID=2000" in env
    assert stat.S_IMODE((project / ".env").stat().st_mode) == 0o600
    chowned = [line.rsplit(" ", 1)[1] for line in _sudo(tmp_path) if line.startswith("sudo chown -R 2000:2000 ")]
    assert sorted(Path(path).name for path in chowned) == ["backups", "data", "media"]
    assert "belong to iris (2000:2000)" in result.stdout
    # The model caches written by the previous user follow.
    assert any("--user 0:0 --entrypoint chown iris -R 2000:2000 /home/iris/.cache/huggingface" in line
               for line in _docker(tmp_path))


def test_staging_gets_its_own_system_user(tmp_path: Path) -> None:
    _, result = _install(tmp_path, gpu=False, toolkit=False,
                         env_settings={"COMPOSE_PROJECT_NAME": "iris-staging"})

    assert "belong to iris-staging" in result.stdout


def test_install_can_leave_ownership_alone(tmp_path: Path) -> None:
    project, _ = _install(tmp_path, gpu=False, toolkit=False, args=("--no-harden",))

    assert not [line for line in _sudo(tmp_path) if "chown" in line]
    assert "IRIS_UID=2000" not in (project / ".env").read_text()


def test_doctor_names_containers_that_can_reach_the_library(tmp_path: Path) -> None:
    project, _ = _install(tmp_path, gpu=False, toolkit=False)
    (project / "data" / "instance_id").write_text("abc\n")
    # Another view of the same folder, as a union filesystem would show it.
    union = tmp_path / "union" / "Iris" / "data"
    union.mkdir(parents=True)
    (union / "instance_id").write_text("abc\n")
    project_name = next(line.split("=", 1)[1] for line in (project / ".env").read_text().splitlines()
                        if line.startswith("COMPOSE_PROJECT_NAME="))
    mounts = {
        "torrent": f"/torrent||1000|{tmp_path / 'union'}",
        "dashboard": f"/dashboard|||{project.parent}",
        "proxy": f"/proxy|||{project / 'data' / 'identity_key.pem'}",
        "media": f"/media|||{tmp_path / 'elsewhere'}",
        f"{project_name}-iris-1": f"/{project_name}-iris-1|2000:2000||{project / 'data'}",
    }
    script = "\n".join(f'    {name}) echo "{line}" ;;' for name, line in mounts.items())
    _stub(tmp_path / "bin-Iris", "docker", f"""
case "$1" in
  ps) printf '%s\\n' {" ".join(mounts)} ;;
  inspect) case "$2" in
{script}
  esac ;;
esac
exit 0
""")
    env = {"PATH": f"{tmp_path / 'bin-Iris'}:/usr/bin:/bin", "HOME": str(tmp_path)}
    (tmp_path / "sudo.log").unlink(missing_ok=True)
    result = subprocess.run(["bash", "scripts/server.sh", "doctor"], cwd=project, env=env,
                            capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"torrent sees the library at {union} and runs as uid 1000 (PUID)" in result.stdout
    assert "dashboard sees the library" in result.stdout
    assert "proxy mounts" in result.stdout and "inside the library" in result.stdout
    assert "media " not in result.stdout and f"{project_name}" not in result.stdout
    # The hardened library is unreadable to the administrator: the comparison goes through sudo.
    assert any(line.startswith("sudo cmp -s") for line in _sudo(tmp_path))


def _btrfs_stubs(tmp_path: Path, bin_dir: Path, *, marked: tuple[str, ...] = ()) -> None:
    """findmnt says btrfs; lsattr reports C on ``marked`` files; chattr is recorded."""
    _stub(bin_dir, "findmnt", "echo btrfs\n")
    pattern = "|".join(f"*/{name}" for name in marked) or "__none__"
    _stub(bin_dir, "lsattr", f'''
case "$2" in {pattern}) echo "---------------C------ $2" ;; *) echo "---------------------- $2" ;; esac
''')
    _stub(bin_dir, "chattr", f'echo "chattr $*" >> "{tmp_path}/chattr.log"\n')


def test_harden_rewrites_existing_databases_without_copy_on_write_on_btrfs(tmp_path: Path) -> None:
    project, _ = _install(tmp_path, gpu=False, toolkit=False)
    library = project / "data" / "users" / "1"
    library.mkdir(parents=True)
    (library / "iris.db").write_bytes(b"SQLite format 3\0" + b"x" * 4096)
    (project / "data" / "users.db").write_bytes(b"SQLite format 3\0accounts")
    photo = library / "media" / "IMG_0001.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"jpeg")
    bin_dir = tmp_path / "bin-Iris"
    _btrfs_stubs(tmp_path, bin_dir, marked=("users.db",))
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}

    result = _server(project, env, "harden")

    chattr = (tmp_path / "chattr.log").read_text().splitlines()
    # Only the library database still had copy-on-write; the photo is not touched.
    assert chattr == ["chattr +C ./data/users/1/iris.db.nocow"]
    assert (library / "iris.db").read_bytes() == b"SQLite format 3\0" + b"x" * 4096
    assert not (library / "iris.db.nocow").exists()
    assert "Rewrote 1 database(s)" in result.stdout
    # Iris was stopped before and started again after.
    commands = _docker(tmp_path)
    stop = next(i for i, line in enumerate(commands) if line.startswith("docker compose stop iris"))
    start = max(i for i, line in enumerate(commands) if "up -d --force-recreate iris" in line)
    assert stop < start


def test_a_database_still_in_use_is_left_for_later(tmp_path: Path) -> None:
    project, _ = _install(tmp_path, gpu=False, toolkit=False)
    (project / "data" / "users.db").write_bytes(b"SQLite format 3\0")
    (project / "data" / "users.db-wal").write_bytes(b"pending pages")
    bin_dir = tmp_path / "bin-Iris"
    _btrfs_stubs(tmp_path, bin_dir)

    result = _server(project, {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}, "harden")

    assert not (tmp_path / "chattr.log").exists()
    assert "write-ahead log is not empty" in result.stderr


def test_other_filesystems_keep_their_databases_as_they_are(tmp_path: Path) -> None:
    project, _ = _install(tmp_path, gpu=False, toolkit=False)
    (project / "data" / "users.db").write_bytes(b"SQLite format 3\0")
    bin_dir = tmp_path / "bin-Iris"
    _btrfs_stubs(tmp_path, bin_dir)
    _stub(bin_dir, "findmnt", "echo ext4\n")

    _server(project, {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}, "harden")

    assert not (tmp_path / "chattr.log").exists()


def test_update_gives_the_container_the_host_time_zone(tmp_path: Path) -> None:
    """In UTC a camera's time, which has no offset, was read three hours off."""
    bin_dir = _docker_stub(tmp_path)
    _stub(bin_dir, "timedatectl", "echo America/Sao_Paulo\n")
    env = _env(tmp_path, bin_dir)
    project = _released_clone(tmp_path, env)
    (project / ".env").write_text("IRIS_PORT=8501\n")

    _server(project, env, "update")
    _server(project, env, "update")

    lines = (project / ".env").read_text().splitlines()
    assert [line for line in lines if line.startswith("TZ=")] == ["TZ=America/Sao_Paulo"]


def test_a_time_zone_set_by_hand_is_kept(tmp_path: Path) -> None:
    bin_dir = _docker_stub(tmp_path)
    _stub(bin_dir, "timedatectl", "echo America/Sao_Paulo\n")
    env = _env(tmp_path, bin_dir)
    project = _released_clone(tmp_path, env)
    (project / ".env").write_text("TZ=Europe/Lisbon\n")

    _server(project, env, "update")

    assert _env_value(project, "TZ") == "Europe/Lisbon"


# -- update agent ----------------------------------------------------------------


def _agent_env(tmp_path: Path, **extra: str) -> tuple[dict, Path]:
    bin_dir = _docker_stub(tmp_path, **extra)
    _stub(bin_dir, "systemctl", f'echo "systemctl $*" >> "{tmp_path}/systemctl.log"\n')
    units = tmp_path / "systemd"
    units.mkdir()
    return _env(tmp_path, bin_dir, IRIS_SYSTEMD_DIR=str(units)), units


def test_the_update_agent_installs_a_path_unit_watching_the_request(tmp_path: Path) -> None:
    env, units = _agent_env(tmp_path)
    project = _released_clone(tmp_path, env)
    result = _server(project, env, "updater", "install")
    assert "Update agent installed (iris-updater.path)" in result.stdout

    path_unit = (units / "iris-updater.path").read_text()
    service = (units / "iris-updater.service").read_text()
    assert f"PathExists={project}/data/updater/request.json" in path_unit
    assert f"ExecStart={project}/scripts/server.sh updater run" in service
    assert "Environment=IRIS_UPDATER_RUN=1" in service
    assert "systemctl enable --now iris-updater.path" in (tmp_path / "systemctl.log").read_text()
    # The web interface offers the button only once the agent says it is there.
    assert (project / "data" / "updater" / "agent.json").is_file()

    _server(project, env, "updater", "uninstall")
    assert not (units / "iris-updater.path").exists()
    assert not (project / "data" / "updater" / "agent.json").exists()


def test_the_agent_does_nothing_without_a_request(tmp_path: Path) -> None:
    env, _ = _agent_env(tmp_path)
    project = _released_clone(tmp_path, env)
    (project / "data" / "updater").mkdir(parents=True)
    _server(project, env, "updater", "run")
    assert not (project / "data" / "updater" / "status.json").exists()
    assert not (tmp_path / "docker.log").exists() or not _pulled(tmp_path)


def test_a_request_runs_a_normal_update_and_reports_it(tmp_path: Path) -> None:
    env, _ = _agent_env(tmp_path)
    project = _released_clone(tmp_path, env)
    folder = project / "data" / "updater"
    folder.mkdir(parents=True)
    (folder / "request.json").write_text('{"requested_by": "alice"}')

    _server(project, env, "updater", "run")
    status = json.loads((folder / "status.json").read_text())
    assert status["state"] == "done" and status["to"] == "0.5.0"
    assert "Release: v0.5.0" in (folder / "last.log").read_text()
    # Consumed, or the path unit would start the service again and again.
    assert not (folder / "request.json").exists()
    assert _head(project, env) == "v0.5.0"


def test_a_failed_update_is_reported_and_not_retried(tmp_path: Path) -> None:
    env, _ = _agent_env(tmp_path, unpublished="0.5.0")
    project = _released_clone(tmp_path, env)
    folder = project / "data" / "updater"
    folder.mkdir(parents=True)
    (folder / "request.json").write_text("{}")

    _server(project, env, "updater", "run", ok=False)
    assert json.loads((folder / "status.json").read_text())["state"] == "failed"
    assert "not available yet" in (folder / "last.log").read_text()
    assert not (folder / "request.json").exists()


def test_update_does_not_back_up_first(tmp_path: Path) -> None:
    """An update replaces code and images, never the originals; the daily backup covers data."""
    env = _env(tmp_path, _docker_stub(tmp_path))
    project = _released_clone(tmp_path, env)
    result = _server(project, env, "update")
    assert "Backing up" not in result.stdout
    assert not any("backup_scheduler" in line for line in _docker(tmp_path))
