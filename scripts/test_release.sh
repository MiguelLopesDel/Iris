#!/usr/bin/env bash
# Validate a clean private-server installation using the exact Docker Compose path.
#
# This builds from `git archive HEAD` in a temporary directory: no local data,
# media, .env, caches, or untracked files can affect the result. It exercises OS
# packages, Python dependencies, FastAPI startup, sessions, and account bootstrap.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

command -v docker >/dev/null 2>&1 || { echo "Docker is required." >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required." >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 2; }

release_dir="$(mktemp -d "${TMPDIR:-/tmp}/iris-release.XXXXXX")"
project_name="iris-release-$(date +%s)-$$"
port="${IRIS_RELEASE_TEST_PORT:-18501}"
base_url="http://127.0.0.1:${port}"
password="release-test-password-123"

cleanup() {
    docker compose --project-name "$project_name" --project-directory "$release_dir" down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$release_dir"
}
trap cleanup EXIT

git diff --quiet || { echo "Commit or stash tracked changes before testing." >&2; exit 2; }

echo "Creating clean source archive..."
git archive HEAD | tar -x -C "$release_dir"

printf '%s\n' \
    "IRIS_UID=$(id -u)" \
    "IRIS_GID=$(id -g)" \
    "IRIS_PORT=$port" \
    "IRIS_LOAD_MODEL=0" \
    "IRIS_SERVER_MODE=private" \
    "IRIS_MULTIUSER=auto" \
    "IRIS_SESSION_HTTPS_ONLY=false" >"$release_dir/.env"
mkdir -p "$release_dir/data" "$release_dir/media"
chmod 700 "$release_dir/data" "$release_dir/media"

compose=(docker compose --project-name "$project_name" --project-directory "$release_dir")

echo "Building clean CPU image..."
"${compose[@]}" build iris
echo "Starting private server..."
"${compose[@]}" up -d iris

for attempt in $(seq 1 45); do
    health="$(curl --silent --show-error "$base_url/healthz" || true)"
    if printf '%s' "$health" | grep -q '"status":"setup_required"'; then break; fi
    sleep 2
done
if ! printf '%s' "$health" | grep -q '"status":"setup_required"'; then
    "${compose[@]}" logs --tail=120 iris >&2
    echo "Clean private server did not reach setup_required." >&2
    exit 1
fi

status="$(curl --silent --output /dev/null --write-out '%{http_code}' "$base_url/api/info")"
if [ "$status" != "401" ]; then
    echo "Anonymous /api/info should return 401, got $status." >&2
    exit 1
fi

echo "Creating first account..."
printf '%s\n%s\n' "$password" "$password" |
    "${compose[@]}" run --rm -T iris python scripts/bootstrap_admin.py \
        --username release-admin --display-name "Release Admin"

"${compose[@]}" up -d --force-recreate iris
for attempt in $(seq 1 45); do
    health="$(curl --silent --show-error "$base_url/healthz" || true)"
    if printf '%s' "$health" | grep -q '"status":"ok"'; then break; fi
    sleep 2
done
if ! printf '%s' "$health" | grep -q '"status":"ok"'; then
    "${compose[@]}" logs --tail=120 iris >&2
    echo "Server did not become ready after bootstrap." >&2
    exit 1
fi

python3 "$release_dir/scripts/verify_server.py" \
    --url "$base_url" \
    --username release-admin \
    --password "$password" \
    --expect-private

echo "PASS: clean Docker installation, startup, authentication, and bootstrap verified."
