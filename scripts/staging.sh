#!/usr/bin/env bash
# Staging installation: run main, or main plus open PRs, on the staging server.
#
# Run it on the server, inside the staging checkout (a second installation
# next to production, see docs/process.md). It never touches another
# installation: every check below runs before anything changes, and it only
# ever builds and starts the `iris` service of this checkout's own Compose
# project. It never removes containers, volumes, images or directories, and
# never discards git work.
#
#   ./scripts/staging.sh status
#   ./scripts/staging.sh update [--pr N]... [--ref REF] [--dry-run]
#   ./scripts/staging.sh rollback [--dry-run]
#
# `update` checks out REF (default origin/main) detached, merges each PR on
# top in a throwaway commit that is never pushed, builds this project's image
# and restarts this project's container. A merge conflict or a failed build
# puts the checkout back where it was and leaves the running container alone.
set -euo pipefail

# `update` moves the checkout, which rewrites this file while bash reads it.
# Run from a private copy instead.
if [ -z "${IRIS_STAGING_REEXEC:-}" ]; then
    copy="$(mktemp "${TMPDIR:-/tmp}/iris-staging.XXXXXX")"
    cp -- "${BASH_SOURCE[0]}" "$copy"
    export IRIS_STAGING_REEXEC=1
    IRIS_STAGING_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
    export IRIS_STAGING_ROOT
    status=0
    bash "$copy" "$@" || status=$?
    rm -f -- "$copy"
    exit "$status"
fi

cd "$IRIS_STAGING_ROOT"

die() { echo "staging: $*" >&2; exit 1; }
say() { echo "staging: $*"; }

usage() {
    sed -n '2,/^set -euo/p' "$0" | sed -n 's/^# \{0,1\}//p' | sed '/^set -euo/d'
}

env_value() {
    [ -f .env ] || return 0
    sed -n "s/^$1=//p" .env | tail -n 1
}

# Docker, with sudo only when the user cannot reach the daemon directly.
docker_cmd() {
    if [ -n "${IRIS_STAGING_DOCKER:-}" ]; then
        # shellcheck disable=SC2086
        $IRIS_STAGING_DOCKER "$@"
    elif docker info >/dev/null 2>&1; then
        docker "$@"
    else
        sudo -n docker "$@"
    fi
}

project=""
compose() { docker_cmd compose -p "$project" "$@"; }

# --- Guards: nothing changes before every one of these passes. -------------

guard_project() {
    project="$(env_value COMPOSE_PROJECT_NAME)"
    [ -n "$project" ] || die "COMPOSE_PROJECT_NAME is not set in .env; refusing (it would default to the production project)."
    case "$project" in
        *staging*) ;;
        *) die "COMPOSE_PROJECT_NAME=$project does not look like staging (it must contain \"staging\"); refusing." ;;
    esac
}

guard_clean_checkout() {
    local changes
    changes="$(git status --porcelain --untracked-files=no)"
    [ -z "$changes" ] || die "tracked files have local changes; commit or move them first:
$changes"
}

# The bind-mount sources of this project's iris service, as "target<TAB>source".
own_mounts() {
    compose config --format json | python3 -c '
import json, sys
service = json.load(sys.stdin)["services"]["iris"]
for volume in service.get("volumes", []):
    if volume.get("type") == "bind":
        print(volume["target"] + "\t" + volume["source"])
'
}

own_port() {
    compose config --format json | python3 -c '
import json, sys
for port in json.load(sys.stdin)["services"]["iris"].get("ports", []):
    print(port.get("published", ""))
'
}

# Containers of every other Compose project (or of none), one id per line.
foreign_containers() {
    docker_cmd ps -a --format '{{.ID}}	{{.Label "com.docker.compose.project"}}' \
        | awk -F '\t' -v own="$project" '$2 != own { print $1 }'
}

guard_isolation() {
    local mounts sources id foreign target source port
    mounts="$(own_mounts)" || die "could not read this project's Compose configuration."
    for target in /app/data /app/media; do
        grep -q "^$target	" <<<"$mounts" \
            || die "$target is not bind-mounted to a host directory; refusing to guess where the data lives."
    done
    foreign="$(foreign_containers)"
    while IFS=$'\t' read -r target source; do
        [ -n "$source" ] || continue
        for id in $foreign; do
            sources="$(docker_cmd inspect --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' "$id")"
            if grep -Fxq -- "$source" <<<"$sources"; then
                die "$source ($target) is also mounted by container $id of another installation; refusing."
            fi
        done
    done <<<"$mounts"
    for port in $(own_port); do
        [ -n "$port" ] || continue
        if docker_cmd ps --format '{{.Label "com.docker.compose.project"}}	{{.Ports}}' \
            | awk -F '\t' -v own="$project" '$1 != own { print $2 }' | grep -Eq "[:]$port->"; then
            die "port $port is published by another installation; refusing."
        fi
    done
}

guard_all() {
    command -v python3 >/dev/null 2>&1 || die "python3 is required to read the Compose configuration."
    guard_project
    guard_clean_checkout
    guard_isolation
}

# --- Deploy. ----------------------------------------------------------------

# The previous deployment, kept as a ref: an integration commit is on no
# branch, and a plain note of its hash would not stop git from pruning it.
previous_ref="refs/staging/previous"

restore_checkout() {
    git merge --abort >/dev/null 2>&1 || true
    git checkout -q --detach "$1"
}

wait_for_health() {
    local bind port url
    bind="$(env_value IRIS_BIND)"; port="$(env_value IRIS_PORT)"
    url="http://${bind:-127.0.0.1}:${port:-8501}/healthz"
    for _ in $(seq 1 60); do
        if curl -fsS -m 3 "$url" >/dev/null 2>&1; then
            say "healthy: $(curl -fsS -m 3 "$url")"
            return 0
        fi
        sleep 2
    done
    return 1
}

deploy() {
    local ref="$1" dry_run="$2"; shift 2
    local prs=("$@") previous pr
    guard_all
    previous="$(git rev-parse HEAD)"
    git fetch -q origin main
    for pr in "${prs[@]}"; do
        git fetch -q origin "pull/$pr/head:refs/staging/pr-$pr" || die "could not fetch PR #$pr."
    done
    git rev-parse --quiet --verify "$ref^{commit}" >/dev/null || die "unknown ref: $ref"
    say "project $project: $(git rev-parse --short "$ref") ($ref)${prs[*]:+ + PR ${prs[*]}}"
    if [ "$dry_run" = 1 ]; then
        say "dry run: checks passed; would check out $ref, merge ${prs[*]:-nothing}, build and restart only $project/iris."
        return 0
    fi

    git checkout -q --detach "$ref"
    for pr in "${prs[@]}"; do
        if ! git -c user.name="Iris staging" -c user.email="staging@localhost" \
            merge -q --no-ff --no-edit "refs/staging/pr-$pr" >/dev/null 2>&1; then
            restore_checkout "$previous"
            die "PR #$pr does not merge cleanly onto $ref; nothing was changed."
        fi
    done

    if ! IRIS_COMMIT="$(git rev-parse --short HEAD)" compose build iris; then
        restore_checkout "$previous"
        die "build failed; the checkout is back on $(git rev-parse --short "$previous") and the running container was not touched."
    fi
    git update-ref "$previous_ref" "$previous"
    compose up -d --no-build iris
    if wait_for_health; then
        say "running $(git rev-parse --short HEAD); previous was $(git rev-parse --short "$previous") (./scripts/staging.sh rollback)."
    else
        die "not healthy after 2 minutes; see: docker compose -p $project logs iris. To go back: ./scripts/staging.sh rollback"
    fi
}

show_status() {
    guard_project
    say "project:  $project"
    say "checkout: $(git log -1 --format='%h %s')"
    local merges
    merges="$(git log --merges --format='  %s' origin/main..HEAD 2>/dev/null || true)"
    [ -z "$merges" ] || printf 'staging: merged on top of main:\n%s\n' "$merges"
    if git rev-parse --quiet --verify "$previous_ref" >/dev/null; then
        say "previous: $(git log -1 --format='%h %s' "$previous_ref")"
    fi
    compose ps iris || true
    own_mounts 2>/dev/null | sed 's/^/staging: mount /' || true
}

command="${1:-}"
[ -n "$command" ] && shift
ref="origin/main"; dry_run=0; prs=()
while [ $# -gt 0 ]; do
    case "$1" in
        --pr) [ $# -ge 2 ] || die "--pr needs a number"; [[ "$2" =~ ^[0-9]+$ ]] || die "--pr takes a PR number, not '$2'"; prs+=("$2"); shift 2 ;;
        --ref) [ $# -ge 2 ] || die "--ref needs a value"; ref="$2"; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

case "$command" in
    status) show_status ;;
    update) deploy "$ref" "$dry_run" "${prs[@]}" ;;
    rollback)
        git rev-parse --quiet --verify "$previous_ref" >/dev/null || die "no previous deployment recorded."
        [ "${#prs[@]}" -eq 0 ] || die "rollback takes no --pr"
        deploy "$(git rev-parse "$previous_ref")" "$dry_run"
        ;;
    ""|-h|--help|help) usage ;;
    *) die "unknown command: $command (status, update, rollback)" ;;
esac
