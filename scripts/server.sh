#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
cd "$project_root"

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
    mkdir -p data media
    chmod 700 data media
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
    docker compose up -d --build
    wait_for_health
    echo "Iris is now available locally at http://127.0.0.1:$port"
    echo "For private Tailscale access, run:"
    echo "  sudo tailscale serve --bg http://127.0.0.1:$port"
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
        prepare_env
        docker compose up -d --build
        wait_for_health
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
        git diff --quiet || { echo "Commit or stash local changes before updating." >&2; exit 1; }
        git pull --ff-only
        docker compose up -d --build
        wait_for_health
        echo "Update completed. Run ./scripts/server.sh status to confirm."
        ;;
    port)
        require_compose
        [ "$#" -eq 2 ] || { echo "Usage: $0 port <1024-65535>" >&2; exit 2; }
        set_port "$2"
        ;;
    *)
        echo "Usage: $0 {install|create-admin|status|logs|update|port <1024-65535>}" >&2
        exit 2
        ;;
esac
