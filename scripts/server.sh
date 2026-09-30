#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
cd "$project_root"

# Recorded in every backup, so a backup says which Iris wrote it. The image
# has no .git, so a local build bakes the commit in as a build argument;
# published images carry the commit they were built from.
IRIS_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || true)"
export IRIS_COMMIT

require_compose() {
    command -v docker >/dev/null 2>&1 || { echo "Docker is required." >&2; exit 1; }
    docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required." >&2; exit 1; }
    command -v curl >/dev/null 2>&1 || { echo "curl is required for health checks." >&2; exit 1; }
}

prepare_env() {
    if [ ! -f .env ]; then
        cp .env.example .env
        sed -i "s/^IRIS_UID=.*/IRIS_UID=$(id -u)/; s/^IRIS_GID=.*/IRIS_GID=$(id -g)/" .env
        echo "Created .env for Linux user $(id -u):$(id -g)."
    fi
    if ! grep -q '^IRIS_BACKUP_TIMEZONE=' .env || grep -q '^IRIS_BACKUP_TIMEZONE=UTC$' .env; then
        local zone
        zone="$(host_timezone)"
        if grep -q '^IRIS_BACKUP_TIMEZONE=' .env; then
            sed -i "s#^IRIS_BACKUP_TIMEZONE=.*#IRIS_BACKUP_TIMEZONE=$zone#" .env
        else
            printf '\nIRIS_BACKUP_TIMEZONE=%s\n' "$zone" >> .env
        fi
    fi
    local backups
    backups="$(env_value IRIS_BACKUP_DIR)"
    mkdir -p data media "${backups:-backups}"
    chmod 700 data media "${backups:-backups}"
    IRIS_BACKUP_HOST_DIR="$(realpath "${backups:-backups}")"
    export IRIS_BACKUP_HOST_DIR
}

enable_gpu() {
    # docker compose reads COMPOSE_FILE from .env, so every later command
    # (status, update, backup...) keeps using the NVIDIA image.
    local files="docker-compose.yml:docker-compose.gpu.yml"
    if grep -q '^#\?COMPOSE_FILE=' .env; then
        sed -i "s|^#\?COMPOSE_FILE=.*|COMPOSE_FILE=$files|" .env
    else
        printf '\nCOMPOSE_FILE=%s\n' "$files" >> .env
    fi
    export COMPOSE_FILE="$files"
    if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
        echo "Warning: Docker reports no NVIDIA runtime; install the NVIDIA Container Toolkit." >&2
    fi
    echo "Using the NVIDIA image (COMPOSE_FILE in .env)."
}

host_timezone() {
    # The schedule's clock should be the owner's, not the container's UTC.
    local zone=""
    if command -v timedatectl >/dev/null 2>&1; then
        zone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    fi
    if [ -z "$zone" ] && [ -L /etc/localtime ]; then
        zone="$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')"
    fi
    printf '%s' "${zone:-UTC}"
}

configured_port() {
    if [ -f .env ]; then
        local configured
        configured="$(sed -n 's/^IRIS_PORT=//p' .env | tail -n 1)"
        if [ -n "$configured" ]; then
            printf '%s' "$configured"
            return
        fi
    fi
    printf '%s' "8501"
}

set_port() {
    local port="$1"
    if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1024 ] || [ "$port" -gt 65535 ]; then
        echo "Port must be an integer between 1024 and 65535." >&2
        exit 2
    fi
    prepare_env
    if grep -q '^IRIS_PORT=' .env; then
        sed -i "s/^IRIS_PORT=.*/IRIS_PORT=$port/" .env
    else
        printf '\nIRIS_PORT=%s\n' "$port" >> .env
    fi
    start_server
    wait_for_health
    echo "Iris is now available locally at http://127.0.0.1:$port"
    echo "To reach it from other devices, put a private layer in front of that"
    echo "address — a mesh VPN, a tunnel, or a reverse proxy with TLS."
    echo "With Tailscale, for example:"
    echo "  sudo tailscale serve --bg http://127.0.0.1:$port"
}

start_server() {
    # Run the published image; build it from this checkout only when that tag
    # was never published (a development branch, or before the first release).
    if ! docker compose pull --quiet iris; then
        echo "No published image for this version; building it locally."
        docker compose build iris
    fi
    docker compose up -d
}

show_storage() {
    # Probe the data/ mount as the container sees it: that is where shared
    # spaces and private libraries live, so it decides whether reflinks work.
    docker compose run --rm --no-deps iris python -m core.fs_clone /app/data/spaces \
        "$(sed -n 's/^IRIS_SPACE_STORAGE=//p' .env | tail -n 1)"
}

env_value() {
    [ -f .env ] && sed -n "s/^$1=//p" .env | tail -n 1
}

require_python3() {
    command -v python3 >/dev/null 2>&1 || { echo "python3 is required on the host for restore/verify." >&2; exit 1; }
}

backup_dir() {
    local dir
    dir="$(env_value IRIS_BACKUP_DIR)"
    realpath "${dir:-backups}"
}

run_backup() {
    # The same path as the scheduled backup: recorded in the history shown in
    # the interface and followed by the retention policy. Inside the
    # container, so paths match the server's; SQLite uses its online backup
    # API, so Iris keeps running.
    prepare_env
    docker compose run --rm --no-deps iris python -m core.backup_scheduler run "$@"
}

restore_snapshot() {
    local snapshot="$1"
    [ -d "$snapshot" ] || { echo "Not a backup folder: $snapshot" >&2; exit 2; }
    require_python3
    python3 -m core.instance_backup verify "$snapshot"
    echo "This replaces data/ and media/ with the backup. The current folders are"
    echo "kept as data.before-restore-* and media.before-restore-*, not deleted."
    read -r -p "Type 'restore' to continue: " answer
    [ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }
    docker compose stop iris
    if ! python3 -m core.instance_backup restore "$snapshot" \
        --target data=data --target media=media; then
        echo "Restore failed; nothing was swapped. Starting the previous state." >&2
        docker compose up -d iris
        exit 1
    fi
    # Restore swaps the host data/media directories atomically. Recreate the
    # stopped service so Docker binds the newly restored directory inodes.
    docker compose up -d --force-recreate iris
    wait_for_health
    echo "Restored. Indexes and thumbnails are rebuilt as they are used."
}

wait_for_health() {
    local attempt
    local port
    port="$(configured_port)"
    for attempt in $(seq 1 30); do
        if curl --fail --silent --show-error "http://127.0.0.1:$port/healthz" >/dev/null; then
            return 0
        fi
        sleep 2
    done
    docker compose logs --tail=100 iris >&2
    echo "Iris did not become healthy." >&2
    return 1
}

case "${1:-}" in
    install)
        require_compose
        case "${2:-}" in
            "") ;;
            --gpu) ;;
            *) echo "Usage: $0 install [--gpu]" >&2; exit 2 ;;
        esac
        prepare_env
        [ "${2:-}" = "--gpu" ] && enable_gpu
        start_server
        wait_for_health
        echo "Daily backups go to $IRIS_BACKUP_HOST_DIR at $(env_value IRIS_BACKUP_TIME)" \
            "($(env_value IRIS_BACKUP_TIMEZONE)); change it in .env or in System > Installation."
        echo "Shared-space storage (IRIS_SPACE_STORAGE in .env):"
        show_storage || true
        echo "Iris is running locally. Create the first account with:"
        echo "  ./scripts/server.sh create-admin --username administrator --display-name 'Your name'"
        ;;
    create-admin)
        require_compose
        shift
        docker compose run --rm -it iris python scripts/bootstrap_admin.py "$@"
        docker compose up -d iris
        wait_for_health
        ;;
    status)
        require_compose
        docker compose ps
        curl --fail --silent "http://127.0.0.1:$(configured_port)/healthz"; echo
        ;;
    logs)
        require_compose
        docker compose logs -f iris
        ;;
    update)
        require_compose
        if [ -d .git ]; then
            git diff --quiet || { echo "Commit or stash local changes before updating." >&2; exit 1; }
        fi
        echo "Backing up before updating..."
        run_backup || { echo "Backup failed; not updating." >&2; exit 1; }
        if [ -d .git ]; then
            git pull --ff-only
        fi
        start_server
        wait_for_health
        echo "Update completed. Run ./scripts/server.sh status to confirm."
        ;;
    backup)
        require_compose
        shift
        run_backup "$@"
        ;;
    backups)
        require_python3
        python3 -m core.instance_backup list "$(backup_dir)"
        ;;
    verify-backup)
        require_python3
        [ "$#" -eq 2 ] || { echo "Usage: $0 verify-backup <backup folder>" >&2; exit 2; }
        python3 -m core.instance_backup verify "$2"
        ;;
    restore)
        require_compose
        [ "$#" -eq 2 ] || { echo "Usage: $0 restore <backup folder>" >&2; exit 2; }
        restore_snapshot "$2"
        ;;
    storage)
        require_compose
        prepare_env
        show_storage
        ;;
    port)
        require_compose
        [ "$#" -eq 2 ] || { echo "Usage: $0 port <1024-65535>" >&2; exit 2; }
        set_port "$2"
        ;;
    *)
        echo "Usage: $0 {install [--gpu]|create-admin|status|logs|update|backup [--pin]|backups|verify-backup <folder>|restore <folder>|storage|port <1024-65535>}" >&2
        exit 2
        ;;
esac
