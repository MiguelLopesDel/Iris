#!/usr/bin/env python3
"""Block pushes that contain likely secrets or personal/local artifacts."""
from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable

ZERO_SHA = "0" * 40
MEDIA_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".mp4", ".mov",
    ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".aac",
}
PRIVATE_PATH = re.compile(
    r"(^|/)(data|media|uploads|import_uploads|sync_uploads|thumbnails)/|^\.env$|"
    r"(^|/)([^/]+\.(db|sqlite|faiss|index|pem|key|p12|pfx))$",
    re.IGNORECASE,
)
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|DSA) PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(
        r"(?i)(?:api[_-]?key|secret(?:[_-]?key)?|password|access[_-]?token|refresh[_-]?token)"
        r"\s*[:=]\s*[\"'][^\"'\s]{12,}"
    ),
]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)


def pushed_commits(lines: Iterable[str]) -> list[str]:
    commits: set[str] = set()
    for line in lines:
        parts = line.strip().split()
        if len(parts) != 4:
            continue
        local_sha, local_ref, remote_sha, _remote_ref = parts
        if local_sha == ZERO_SHA or local_ref == "(delete)":
            continue
        revision = local_sha if remote_sha == ZERO_SHA else f"{remote_sha}..{local_sha}"
        commits.update(git("rev-list", revision).splitlines())
    return sorted(commits)


def is_allowed_media_path(path: str) -> bool:
    return path.startswith(("static/", "docs/", "tests/fixtures/"))


def inspect_blob(commit: str, path: str) -> list[str]:
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if PRIVATE_PATH.search(path):
        return [f"private/local artifact: {path}"]
    if suffix in MEDIA_SUFFIXES and not is_allowed_media_path(path):
        return [f"media file outside approved asset folders: {path}"]
    try:
        content = subprocess.check_output(
            ["git", "show", f"{commit}:{path}"], stderr=subprocess.DEVNULL
        ).decode("utf-8", errors="replace")
    except subprocess.CalledProcessError:
        return []
    findings = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(content):
            findings.append(f"possible secret in {path}")
            break
    return findings


def main() -> int:
    findings: list[str] = []
    for commit in pushed_commits(sys.stdin):
        paths = git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        for path in paths:
            findings.extend(inspect_blob(commit, path))
    if not findings:
        return 0
    print("Push blocked by Iris privacy check:", file=sys.stderr)
    for finding in sorted(set(findings)):
        print(f"  - {finding}", file=sys.stderr)
    print("Remove the file/secret or use an approved non-private fixture before pushing.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
