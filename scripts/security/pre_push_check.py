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
    # Anchored at the repo root on purpose: these are server runtime folders.
    # Matching the segment anywhere flagged every Android source file, whose
    # Java package path contains .../com/iris/app/data/...
    r"^(data|media|uploads|import_uploads|sync_uploads|thumbnails)/|^\.env$|"
    r"(^|/)([^/]+\.(db|sqlite|faiss|index|pem|key|p12|pfx))$",
    re.IGNORECASE,
)
# High-confidence: an actual credential shape. Always checked, everywhere.
STRONG_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|DSA) PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
]

# Heuristic: a credential-ish name assigned a long literal. Catches real
# mistakes, but also ordinary code, so its matches are filtered below.
KEYWORD_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|secret(?:[_-]?key)?|password|access[_-]?token|refresh[_-]?token)"
    r"\s*[:=]\s*[\"']([^\"'\s]{12,})"
)

# A value that is nothing but lowercase snake_case is a key name, not a
# credential — e.g. KEY_ACCESS_TOKEN = "enc_access_token" in the Android
# SharedPreferences store.
IDENTIFIER_VALUE = re.compile(r"^[a-z][a-z0-9_]*$")

# A value read from the environment or substituted at runtime is the shape a
# fixed credential is supposed to be replaced *with*. Flagging it would punish
# the fix: password="${IRIS_RELEASE_TEST_PASSWORD:-...}" is correct code.
INTERPOLATED_VALUE = re.compile(r"^[$%]|^\{\{|\$\{|\$\(")

# Test code declares fake credentials by design. The strong patterns above
# still apply there; only the keyword heuristic is relaxed.
TEST_PATH = re.compile(r"(^|/)(tests?|androidTest)/")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)


def pushed_commits(lines: Iterable[str]) -> list[str]:
    commits: set[str] = set()
    for line in lines:
        parts = line.strip().split()
        if len(parts) != 4:
            continue
        # git sends: <local ref> <local sha> <remote ref> <remote sha>.
        # Reading these in the wrong order made every push inspect the empty
        # range <ref>..<ref> — the check silently passed everything — and made
        # a branch deletion crash on rev-list <ref>..(delete).
        local_ref, local_sha, _remote_ref, remote_sha = parts
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
    for pattern in STRONG_SECRET_PATTERNS:
        if pattern.search(content):
            return [f"possible secret in {path}"]
    if TEST_PATH.search(path):
        return []
    for match in KEYWORD_ASSIGNMENT.finditer(content):
        value = match.group(1)
        if IDENTIFIER_VALUE.match(value) or INTERPOLATED_VALUE.search(value):
            continue
        return [f"possible secret in {path}"]
    return []


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
