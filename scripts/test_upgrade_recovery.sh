#!/usr/bin/env bash
# Rehearse a real container upgrade and whole-instance recovery with synthetic
# data. The old image is built from HEAD; the new one is HEAD plus tracked local
# changes. No repository data/, media/, .env, or untracked runtime source is used.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$project_root"

command -v docker >/dev/null 2>&1 || { echo "Docker is required." >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "Docker daemon is unavailable." >&2; exit 2; }
docker buildx version >/dev/null 2>&1 || { echo "Docker Buildx is required." >&2; exit 2; }

# When Docker runs outside a development container, this defaults to the real
# path. In a devcontainer, set it to the host-visible path of this workspace.
docker_workspace="${IRIS_DOCKER_HOST_WORKSPACE:-$project_root}"
test_uid="${IRIS_TEST_UID:-$(id -u)}"
test_gid="${IRIS_TEST_GID:-$(id -g)}"
port="${IRIS_UPGRADE_TEST_PORT:-$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')}"
run_id="$(date +%s)-$$"
old_image="iris-upgrade-old:$run_id"
new_image="iris-upgrade-new:$run_id"
old_container="iris-upgrade-old-$run_id"
new_container="iris-upgrade-new-$run_id"
seed_container="iris-upgrade-seed-$run_id"
release_dir="$(mktemp -d "$project_root/.iris-upgrade-test.XXXXXX")"
relative_dir="${release_dir#"$project_root"/}"
host_release_dir="$docker_workspace/$relative_dir"
base_url="http://127.0.0.1:$port"

case "$release_dir" in
    "$project_root"/.iris-upgrade-test.*) ;;
    *)
        echo "Refusing unexpected temporary directory: $release_dir" >&2
        exit 2
        ;;
esac

cleanup() {
    local status=$?
    if [ "$status" -ne 0 ] && [ "${IRIS_UPGRADE_KEEP_ON_FAILURE:-0}" = 1 ]; then
        echo "Keeping isolated failure artifacts at $release_dir for inspection." >&2
        return "$status"
    fi
    docker rm -f "$old_container" "$new_container" "$seed_container" >/dev/null 2>&1 || true
    docker image rm "$old_image" "$new_image" >/dev/null 2>&1 || true
    rm -rf -- "$release_dir"
    return "$status"
}
trap cleanup EXIT

untracked_runtime="$(git ls-files --others --exclude-standard \
    | rg '^(core|routers)/.*\.py$|^(server\.py|Dockerfile(\.gpu)?|docker-compose[^/]*\.yml|requirements[^/]*\.txt|static/|templates/|scripts/.*\.(py|sh))' \
    | rg -v '^scripts/test_upgrade_recovery\.sh$' || true)"
if [ -n "$untracked_runtime" ]; then
    echo "Untracked runtime source is not included in the test snapshot:" >&2
    printf '%s\n' "$untracked_runtime" >&2
    exit 2
fi

mkdir -p "$release_dir/head" "$release_dir/current"
echo "Preparing isolated source snapshots..."
git archive HEAD | tar -x -C "$release_dir/head"
git archive HEAD | tar -x -C "$release_dir/current"
# The snapshots live below the Git worktree, so apply must explicitly prefix
# the destination; `git -C snapshot apply` would resolve the parent .git and
# silently inspect/patch the real worktree instead of the isolated copy.
git diff --binary HEAD | git apply --binary --directory="$relative_dir/current"

# Docker's daemon must be able to see bind sources. This path is explicitly
# host-visible in devcontainer use; builds themselves use client-side contexts.
mkdir -p "$release_dir/head/data" "$release_dir/head/media" "$release_dir/head/backups"
mkdir -p "$release_dir/current/data" "$release_dir/current/media" "$release_dir/current/backups"
if [ "$(id -u)" = 0 ]; then
    chown -R "$test_uid:$test_gid" "$release_dir"
fi

build_image() {
    local tag="$1" source="$2"
    echo "Building $tag from $source..."
    docker buildx build --load --build-arg "IRIS_UID=$test_uid" \
        --build-arg "IRIS_GID=$test_gid" --tag "$tag" "$source"
}

run_server() {
    local container="$1" image="$2" source="$3"
    docker run --detach --name "$container" --user "$test_uid:$test_gid" \
        --publish "127.0.0.1:$port:8501" \
        --mount "type=bind,src=$host_release_dir/$source/data,dst=/app/data" \
        --mount "type=bind,src=$host_release_dir/$source/media,dst=/app/media" \
        --mount "type=bind,src=$host_release_dir/$source/backups,dst=/backup" \
        --env PYTHONPATH=/app \
        --env IRIS_SERVER_MODE=private \
        --env IRIS_MULTIUSER=auto \
        --env IRIS_LOAD_MODEL=0 \
        --env IRIS_SESSION_HTTPS_ONLY=false \
        --env IRIS_BACKUP_SCHEDULE=off \
        --env IRIS_SPACE_STORAGE=copy \
        --env IRIS_BACKUP_DEST=/backup \
        "$image"
}

wait_healthy() {
    local container="$1" body=""
    for attempt in $(seq 1 90); do
        body="$(curl --silent --show-error "$base_url/healthz" 2>/dev/null || true)"
        if printf '%s' "$body" | rg -q '"status":"ok"'; then
            echo "Healthy: $container ($body)"
            return 0
        fi
        if ! docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null | rg -q true; then
            docker logs --tail=120 "$container" >&2 || true
            echo "$container exited before becoming healthy." >&2
            return 1
        fi
        sleep 2
    done
    docker logs --tail=120 "$container" >&2 || true
    echo "$container did not become healthy; last response: $body" >&2
    return 1
}

verify_server() {
    local check_albums="$1"
    IRIS_TEST_URL="$base_url" IRIS_TEST_EXPECTED="$expected" \
        IRIS_TEST_ALBUMS="$check_albums" python3 - <<'PY'
import hashlib
import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request

base = os.environ["IRIS_TEST_URL"]
expected = json.loads(os.environ["IRIS_TEST_EXPECTED"])
check_albums = os.environ["IRIS_TEST_ALBUMS"] == "1"

def request(client, path, *, data=None):
    payload = None if data is None else urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(base + path, data=payload)
    if payload is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with client.open(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()

clients = {}
for username in ("alice", "bob"):
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    status, body = request(client, "/api/auth/login", data={
        "username": username, "password": "synthetic recovery password"
    })
    assert status == 200, f"{username} login: HTTP {status}: {body[:300]!r}"
    clients[username] = client

    status, body = request(client, "/api/records")
    assert status == 200, f"{username} records: HTTP {status}: {body[:300]!r}"
    records = json.loads(body)["records"]
    assert [item["arquivo"] for item in records] == [f"{username}.jpg"], records
    status, thumb = request(client, records[0]["thumbnail_url"])
    assert status == 200 and thumb, f"{username} thumbnail: HTTP {status}"

    status, body = request(client, "/api/spaces")
    assert status == 200, f"{username} spaces: HTTP {status}: {body[:300]!r}"
    spaces = json.loads(body)["spaces"]
    role = "manager" if username == "alice" else "viewer"
    assert [(space["name"], space["role"]) for space in spaces] == [
        ("Recovery family", role)
    ], spaces

space_id = expected["space_id"]
item_id = expected["item_id"]
for username, client in clients.items():
    status, body = request(client, f"/api/spaces/{space_id}/items")
    assert status == 200, f"{username} shared items: HTTP {status}: {body[:300]!r}"
    assert [item["name"] for item in json.loads(body)["items"]] == ["alice.jpg"]
    status, image = request(client, f"/api/spaces/{space_id}/items/{item_id}/original")
    assert status == 200, f"{username} shared original: HTTP {status}"
    assert hashlib.sha256(image).hexdigest() == expected["alice_sha256"]
    if check_albums:
        status, body = request(client, f"/api/spaces/{space_id}/albums")
        assert status == 200, f"{username} albums: HTTP {status}: {body[:300]!r}"
        assert json.loads(body)["albums"] == []

print("PASS: two logins, private library isolation, shared membership and original bytes"
      + (", migrated albums API" if check_albums else ""))
PY
}

echo "Building and starting the previous committed version..."
build_image "$old_image" "$release_dir/head"

expected="$(docker run --rm -i --name "$seed_container" --network none \
    --user "$test_uid:$test_gid" \
    --mount "type=bind,src=$host_release_dir/head/data,dst=/app/data" \
    --mount "type=bind,src=$host_release_dir/head/media,dst=/app/media" \
    --env PYTHONPATH=/app "$old_image" python - <<'PY'
import hashlib
import json
import sqlite3
from pathlib import Path

from PIL import Image
from core.auth import hash_password
from core.indexer_db import init_db
from core.shared_spaces import add_member, create_space
from core.space_catalog import add_item, space_root
from core.users_db import create_user

data = Path("data")
password_hash = hash_password("synthetic recovery password")
users = {}
hashes = {}
for username, color in (("alice", (220, 40, 40)), ("bob", (40, 40, 220))):
    user = create_user(
        data / "users.db", data, username=username,
        display_name=username.title(), password_hash=password_hash,
        is_admin=username == "alice",
    )
    image = user.media_root / f"{username}.jpg"
    Image.new("RGB", (32, 32), color).save(image)
    hashes[username] = hashlib.sha256(image.read_bytes()).hexdigest()
    init_db(user.db_path).close()
    with sqlite3.connect(user.db_path) as connection:
        connection.execute(
            "INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
            (image.name, str(image), b"\0" * 16),
        )
    users[username] = user

(data / "secret_key").write_bytes(b"synthetic test-only session secret")
space = create_space(data / "users.db", users["alice"].id, "Recovery family")
add_member(data / "users.db", space.id, users["alice"].id, "bob", "viewer")
item, _ = add_item(
    space_root(data, space.id), users["alice"].media_root / "alice.jpg",
    "alice.jpg", users["alice"].id,
)
print(json.dumps({
    "space_id": space.id, "item_id": item.id,
    "alice_sha256": hashes["alice"], "bob_sha256": hashes["bob"],
}))
PY
)"
echo "Synthetic two-account instance created."

run_server "$old_container" "$old_image" head >/dev/null
wait_healthy "$old_container"

echo "Creating and verifying a pinned whole-instance backup with the old version..."
docker exec "$old_container" python -m core.backup_scheduler run --pin
snapshot_name="$(find "$release_dir/head/backups" -mindepth 1 -maxdepth 1 -type d \
    -name 'iris-backup-*' -printf '%f\n' | sort | tail -n 1)"
if [ -z "$snapshot_name" ]; then
    echo "Backup command returned without a snapshot." >&2
    exit 1
fi
docker exec "$old_container" python -m core.instance_backup verify "/backup/$snapshot_name"
docker stop "$old_container" >/dev/null
docker rm "$old_container" >/dev/null

echo "Preparing the current code with a copy of the old instance and backup..."
cp -a "$release_dir/head/data/." "$release_dir/current/data/"
cp -a "$release_dir/head/media/." "$release_dir/current/media/"
cp -a "$release_dir/head/backups/." "$release_dir/current/backups/"
if [ "$(id -u)" = 0 ]; then
    chown -R "$test_uid:$test_gid" "$release_dir/current"
fi

echo "Building and starting the current code..."
build_image "$new_image" "$release_dir/current"
run_server "$new_container" "$new_image" current >/dev/null
wait_healthy "$new_container"
docker exec "$new_container" python -c \
    'from routers.spaces import router; print("Registered album routes:", [r.path for r in router.routes if "albums" in r.path])'
verify_server 1

schema_version="$(docker exec "$new_container" python -c \
    'import sqlite3; print(sqlite3.connect("data/spaces/1/space.db").execute("PRAGMA user_version").fetchone()[0])')"
if [ "$schema_version" != 3 ]; then
    echo "Expected space schema v3 after album API access; got $schema_version." >&2
    exit 1
fi

echo "Stopping current server and restoring the old-version snapshot..."
docker stop "$new_container" >/dev/null
docker rm "$new_container" >/dev/null
docker run --rm --name "iris-upgrade-restore-$run_id" --network none \
    --user "$test_uid:$test_gid" \
    --mount "type=bind,src=$host_release_dir/current,dst=/restore-root" \
    --mount "type=bind,src=$host_release_dir/current/backups,dst=/backup,readonly" \
    "$new_image" python -m core.instance_backup restore \
        "/backup/$snapshot_name" \
        --target data=/restore-root/data --target media=/restore-root/media
# The host restore renames data/ and media/, so mimic --force-recreate: a
# stopped container would otherwise keep its bind mounts to the old inodes.
run_server "$new_container" "$new_image" current >/dev/null
wait_healthy "$new_container"
verify_server 1

schema_version="$(docker exec "$new_container" python -c \
    'import sqlite3; print(sqlite3.connect("data/spaces/1/space.db").execute("PRAGMA user_version").fetchone()[0])')"
if [ "$schema_version" != 3 ]; then
    echo "Expected restored space to migrate to schema v3; got $schema_version." >&2
    exit 1
fi

echo "PASS: real Docker upgrade, backup verification, v2→v3 migration, restore, and post-restore startup."
