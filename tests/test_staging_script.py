"""scripts/staging.sh: it must only ever touch its own staging installation.

Each test builds a throwaway "GitHub" repository (main plus pull request refs),
a staging checkout cloned from it, and fake docker/curl commands that record
every call. Nothing here talks to a real Docker daemon or server.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "staging.sh"
FORBIDDEN = ("down", " rm", "prune", "volume", "--force", "kill")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def staging(tmp_path: Path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "app.txt").write_text("base\n")
    (seed / "scripts").mkdir()
    (seed / "scripts" / "staging.sh").write_text(SCRIPT.read_text())
    git(seed, "add", ".")
    git(seed, "commit", "-q", "-m", "base")
    # PR 7 changes another file; PR 8 conflicts with PR 9.
    prs = {7: ("feature.txt", "seven\n"), 8: ("app.txt", "eight\n"), 9: ("app.txt", "nine\n")}
    for number, (name, content) in prs.items():
        git(seed, "checkout", "-q", "-b", f"pr{number}", "main")
        (seed / name).write_text(content)
        git(seed, "add", name)
        git(seed, "commit", "-q", "-m", f"pr {number}")
        git(seed, "checkout", "-q", "main")
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    for number in (7, 8, 9):
        sha = git(seed, "rev-parse", f"pr{number}")
        git(origin, "update-ref", f"refs/pull/{number}/head", sha)

    checkout = tmp_path / "Iris-staging"
    git(tmp_path, "clone", "-q", str(origin), str(checkout))
    (checkout / ".env").write_text(
        "COMPOSE_PROJECT_NAME=iris-staging\nIRIS_PORT=8502\nIRIS_BIND=127.0.0.1\n"
    )

    fake = tmp_path / "fake"
    fake.mkdir()
    log = tmp_path / "docker.log"
    config = {
        "services": {"iris": {
            "volumes": [
                {"type": "bind", "source": "/srv/staging/data", "target": "/app/data"},
                {"type": "bind", "source": "/srv/staging/media", "target": "/app/media"},
            ],
            "ports": [{"published": "8502"}],
        }}
    }
    (fake / "config.json").write_text(json.dumps(config))
    (fake / "ps-a.txt").write_text("abc\tiris\nown\tiris-staging\n")
    (fake / "ps-ports.txt").write_text("iris\t0.0.0.0:8501->8501/tcp\n")
    (fake / "inspect-abc.txt").write_text("/srv/production/data\n/srv/production/media\n")
    docker = fake / "docker"
    docker.write_text(f"""#!/usr/bin/env bash
echo "$*" >> {log}
F={fake}
case "$*" in
  *"config --format json"*) cat $F/config.json ;;
  "ps -a "*) cat $F/ps-a.txt ;;
  "ps --format"*) cat $F/ps-ports.txt ;;
  inspect*) id="${{@: -1}}"; cat "$F/inspect-$id.txt" 2>/dev/null || true ;;
  *" build iris") exit "${{FAKE_BUILD_STATUS:-0}}" ;;
  *) exit 0 ;;
esac
""")
    docker.chmod(0o755)
    curl = fake / "curl"
    curl.write_text('#!/usr/bin/env bash\necho \'{"status":"ok"}\'\n')
    curl.chmod(0o755)

    def run(*args: str, **env: str) -> subprocess.CompletedProcess:
        environment = dict(
            os.environ,
            IRIS_STAGING_DOCKER=str(docker),
            PATH=f"{fake}:{os.environ['PATH']}",
            GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"),
            **env,
        )
        return subprocess.run(
            ["bash", str(checkout / "scripts" / "staging.sh"), *args],
            cwd=checkout, env=environment, capture_output=True, text=True,
        )

    def calls() -> list[str]:
        return log.read_text().splitlines() if log.exists() else []

    return checkout, fake, run, calls


def head(checkout: Path) -> str:
    return git(checkout, "rev-parse", "HEAD")


def assert_nothing_destructive(calls: list[str]) -> None:
    for call in calls:
        assert not any(word in f" {call}" for word in FORBIDDEN), call


def test_update_with_a_pr_builds_and_restarts_only_its_own_project(staging):
    checkout, _, run, calls = staging
    before = head(checkout)

    result = run("update", "--pr", "7")

    assert result.returncode == 0, result.stderr
    assert (checkout / "feature.txt").read_text() == "seven\n"
    changing = [c for c in calls() if " build " in f" {c} " or " up " in f" {c} "]
    assert changing == [
        "compose -p iris-staging build iris",
        "compose -p iris-staging up -d --no-build iris",
    ]
    assert git(checkout, "rev-parse", "refs/staging/previous") == before
    assert_nothing_destructive(calls())


def test_refuses_without_a_staging_project_name(staging):
    checkout, _, run, calls = staging
    for env_text in ("IRIS_PORT=8502\n", "COMPOSE_PROJECT_NAME=iris\n"):
        (checkout / ".env").write_text(env_text)
        result = run("update")
        assert result.returncode != 0
        assert "refusing" in result.stderr
    assert calls() == []


def test_refuses_when_its_data_directory_belongs_to_another_installation(staging):
    _, fake, run, calls = staging
    (fake / "inspect-abc.txt").write_text("/srv/staging/data\n")
    before = calls()

    result = run("update")

    assert result.returncode != 0
    assert "/srv/staging/data" in result.stderr and "another installation" in result.stderr
    assert not any(" build " in f" {c} " for c in calls()[len(before):])


def test_refuses_when_its_port_is_published_by_another_installation(staging):
    _, fake, run, calls = staging
    (fake / "ps-ports.txt").write_text("iris\t0.0.0.0:8502->8501/tcp\n")

    result = run("update")

    assert result.returncode != 0 and "port 8502" in result.stderr
    assert not any(" build " in f" {c} " for c in calls())


def test_refuses_when_tracked_files_have_local_changes(staging):
    checkout, _, run, calls = staging
    (checkout / "app.txt").write_text("edited on the server\n")

    result = run("update")

    assert result.returncode != 0 and "local changes" in result.stderr
    assert (checkout / "app.txt").read_text() == "edited on the server\n"
    assert calls() == []


def test_dry_run_checks_everything_and_changes_nothing(staging):
    checkout, _, run, calls = staging
    before = head(checkout)

    result = run("update", "--pr", "7", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "dry run" in result.stdout
    assert head(checkout) == before
    assert not any(" build " in f" {c} " or " up " in f" {c} " for c in calls())


def test_a_conflicting_pr_puts_the_checkout_back_and_builds_nothing(staging):
    checkout, _, run, calls = staging
    before = head(checkout)

    result = run("update", "--pr", "8", "--pr", "9")

    assert result.returncode != 0 and "does not merge cleanly" in result.stderr
    assert head(checkout) == before
    assert git(checkout, "status", "--porcelain", "--untracked-files=no") == ""
    assert not any(" build " in f" {c} " for c in calls())


def test_a_failed_build_puts_the_checkout_back_and_leaves_the_container(staging):
    checkout, _, run, calls = staging
    before = head(checkout)

    result = run("update", "--pr", "7", FAKE_BUILD_STATUS="1")

    assert result.returncode != 0 and "build failed" in result.stderr
    assert head(checkout) == before
    assert not any(" up " in f" {c} " for c in calls())


def test_rollback_returns_to_the_previous_deployment(staging):
    checkout, _, run, _ = staging
    first = head(checkout)
    assert run("update", "--pr", "7").returncode == 0
    with_pr = head(checkout)

    result = run("rollback")

    assert result.returncode == 0, result.stderr
    assert head(checkout) == first
    # And back again: the PR deployment is kept even though it is on no branch.
    assert run("rollback").returncode == 0
    assert head(checkout) == with_pr


def test_rejects_a_pr_that_is_not_a_number(staging):
    _, _, run, calls = staging
    result = run("update", "--pr", "7;rm -rf /")
    assert result.returncode != 0 and "PR number" in result.stderr
    assert calls() == []
