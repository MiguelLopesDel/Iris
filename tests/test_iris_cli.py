"""./iris: the shipping and server commands, against a throwaway origin and fake gh/ssh."""
from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def repo(tmp_path: Path):
    env = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "EDITOR": "true",
    }

    def git(*args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    work = tmp_path / "Iris"
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    shutil.copy(ROOT / "iris", work / "iris")
    (work / ".gitignore").write_text(".iris.local\n")
    (work / "pyproject.toml").write_text('[project]\nname = "iris"\nversion = "0.8.0"\n')
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "base", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "gh", f"""echo "gh $*" >> "{tmp_path}/gh.log"
case "$1 $2" in
    "pr view") [ -f "{tmp_path}/pr" ] || exit 1; echo https://example.invalid/pull/1 ;;
    "pr create") touch "{tmp_path}/pr" ;;
esac
exit 0
""")
    _stub(bin_dir, "ssh", f'echo "ssh $*" >> "{tmp_path}/ssh.log"\n')
    (work / ".iris.local").write_text(
        "IRIS_SERVER=box\nIRIS_PROD_DIR=/srv/iris/prod\nIRIS_STAGING_DIR=/srv/iris/staging\n"
    )

    def run(*args, ok=True):
        result = subprocess.run(["bash", "iris", *args], cwd=work, env=env,
                                capture_output=True, text=True, timeout=60)
        assert (result.returncode == 0) == ok, result.stdout + result.stderr
        return result

    return work, run, lambda *a: git(*a, cwd=work), tmp_path


def test_ship_from_main_opens_a_self_merging_pr_and_returns_to_main(repo) -> None:
    work, run, git, tmp = repo
    (work / "feature.txt").write_text("new\n")

    run("ship", "fix(ui): Keep the menu open!", "--no-check")

    assert git("branch", "--show-current") == "main"
    assert git("log", "-1", "--format=%s", "origin/fix/keep-the-menu-open") == "fix(ui): Keep the menu open!"
    assert git("status", "--porcelain") == ""
    calls = (tmp / "gh.log").read_text()
    assert "gh pr create --base main" in calls
    assert "gh pr merge --auto --merge --delete-branch" in calls


def test_ship_refuses_a_message_without_a_commit_type(repo) -> None:
    work, run, git, _ = repo
    (work / "feature.txt").write_text("new\n")

    result = run("ship", "keep the menu open", "--no-check", ok=False)

    assert "Conventional Commit type" in result.stderr
    assert git("branch", "--show-current") == "main"


def test_release_bumps_first_and_tags_once_main_has_the_version(repo) -> None:
    work, run, git, _ = repo

    run("release", "0.9.0")
    assert git("tag", "--list") == ""
    assert 'version = "0.9.0"' in git("show", "origin/chore/release-0.9.0:pyproject.toml")
    assert (work / "docs" / "releases" / "0.9.0.md").exists() is False  # it lives on the branch
    assert git("branch", "--show-current") == "main"

    run("release", "0.8.0")  # main already says 0.8.0: tag it
    assert "refs/tags/v0.8.0" in git("ls-remote", "--tags", "origin")


def test_server_commands_run_inside_the_right_installation(repo) -> None:
    _, run, _, tmp = repo

    run("staging", "--pr", "12")
    run("deploy")

    calls = (tmp / "ssh.log").read_text().splitlines()
    assert calls[0] == "ssh -t box cd /srv/iris/staging && ./scripts/staging.sh update --pr 12"
    assert calls[1] == "ssh -t box cd /srv/iris/prod && ./scripts/server.sh update"


def test_save_commits_each_change_on_a_local_branch_without_pushing(repo) -> None:
    work, run, git, tmp = repo
    (work / "a.txt").write_text("a\n")
    run("save", "fix(ui): Keep the menu open")
    (work / "b.txt").write_text("b\n")
    run("save", "feat(search): filter by year")

    assert git("branch", "--show-current") == "fix/keep-the-menu-open"
    assert git("log", "--format=%s", "origin/main..HEAD").splitlines() == [
        "feat(search): filter by year", "fix(ui): Keep the menu open",
    ]
    assert "fix/keep-the-menu-open" not in git("ls-remote", "origin")
    assert not (tmp / "gh.log").exists()


def test_try_sends_the_working_copy_to_staging_without_touching_the_branch(repo) -> None:
    work, run, git, tmp = repo
    staging = tmp / "staging"
    subprocess.run(["git", "clone", "-q", str(tmp / "origin.git"), str(staging)], check=True)
    # git reaches the server through ssh; this one answers git locally and logs the rest.
    _stub(tmp / "bin", "ssh", f"""for arg; do
    case "$arg" in git-receive-pack*) exec git-receive-pack "{staging}" ;; esac
done
echo "ssh $*" >> "{tmp}/ssh.log"
""")
    (work / "pyproject.toml").write_text('[project]\nname = "iris"\nversion = "0.8.1"\n')
    (work / "new.txt").write_text("untracked\n")
    head = git("rev-parse", "HEAD")

    run("try")

    sent = subprocess.run(["git", "-C", str(staging), "show", "refs/staging/try:new.txt"],
                          check=True, capture_output=True, text=True).stdout
    assert sent == "untracked\n"
    assert git("rev-parse", "HEAD") == head
    assert git("diff", "--cached", "--name-only") == ""  # the index was not touched
    assert git("status", "--porcelain").splitlines() == ["M pyproject.toml", "?? new.txt"]
    calls = (tmp / "ssh.log").read_text().splitlines()
    assert calls[-1] == "ssh -t box cd /srv/iris/staging && ./scripts/staging.sh update --ref refs/staging/try"
