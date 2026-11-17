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

# Code and image always come from the same release. Releases are the vX.Y.Z
# tags: main is where changes are integrated, and only what is tagged reaches
# a server. `update` moves this checkout to the release tag and runs that
# version's image; IRIS_VERSION=latest (the default) means the newest stable
# tag, a pinned version (IRIS_VERSION=0.4.0 in .env or the environment) that
# one, pre-releases such as 0.6.0-rc.1 included.
# Without git (an unpacked archive) the version is the one in pyproject.toml.
requested_version="${IRIS_VERSION:-}"
configured_version() {
    local configured="$requested_version"
    if [ -z "$configured" ] && [ -f .env ]; then
        configured="$(sed -n 's/^IRIS_VERSION=//p' .env | tail -n 1)"
    fi
    printf '%s\n' "${configured:-latest}"
}
checkout_version() {
    local version=""
    [ -f pyproject.toml ] && version="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
    printf '%s\n' "${version:-latest}"
}
release_tag() {
    # The tag `update` should be on, or nothing when there is no release yet.
    local configured
    configured="$(configured_version)"
    if [ "$configured" = latest ]; then
        # Stable releases only: exactly vX.Y.Z. A pre-release (v0.6.0-rc.1) is
        # never picked by itself -- git even sorts it after v0.6.0 -- and runs
        # only when pinned explicitly with IRIS_VERSION=0.6.0-rc.1.
        git tag --list 'v*' --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true
    else
        git rev-parse --quiet --verify "refs/tags/v$configured" >/dev/null && printf 'v%s\n' "$configured"
    fi
}
if [ "$(configured_version)" = latest ]; then
    IRIS_VERSION="$(checkout_version)"
else
    IRIS_VERSION="$(configured_version)"
fi
export IRIS_VERSION

require_compose() {
    command -v docker >/dev/null 2>&1 || { echo "Docker is required." >&2; exit 1; }
    if [ "$(id -u)" != 0 ] && ! command docker info >/dev/null 2>&1; then
        # Not in the docker group (which is root in all but name): go through sudo.
        # shellcheck disable=SC2033
        docker() { sudo docker "$@"; }
    fi
    docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required." >&2; exit 1; }
    command -v curl >/dev/null 2>&1 || { echo "curl is required for health checks." >&2; exit 1; }
}

as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

# The folders are the service user's once `harden` ran, not the administrator's:
# host-side commands that read them go through sudo then.
as_data_owner() {
    if [ -O "$(data_dir)" ] || [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

service_user() {
    # Production and staging each get their own user, so neither can read the other.
    local configured
    configured="$(env_value IRIS_SERVICE_USER)"
    if [ -n "$configured" ]; then
        printf '%s\n' "$configured"
    else
        case "$(env_value COMPOSE_PROJECT_NAME)" in
            *staging*) printf 'iris-staging\n' ;;
            *) printf 'iris\n' ;;
        esac
    fi
}

set_env() {
    if grep -q "^$1=" .env; then
        sed -i "s#^$1=.*#$1=$2#" .env
    else
        printf '\n%s=%s\n' "$1" "$2" >> .env
    fi
}

harden_files() {
    # Three owners, so a compromised neighbour (another container, another
    # account) cannot read the library:
    #   - the checkout (scripts, compose files): the administrator, read-only to Iris;
    #   - .env (secrets): the administrator, mode 600;
    #   - data, media, backups: a system user with no shell or login, mode 700,
    #     which is what the container runs as.
    # Root owns nothing: a root-owned checkout makes every update need sudo.
    local user admin uid gid entry dir
    user="$(service_user)"
    admin="${SUDO_USER:-$(id -un)}"
    if ! getent passwd "$user" >/dev/null; then
        as_root useradd --system --user-group --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin "$user"
        echo "Created system user $user (no shell, no login)."
    fi
    entry="$(getent passwd "$user")"
    uid="$(printf '%s' "$entry" | cut -d: -f3)"
    gid="$(printf '%s' "$entry" | cut -d: -f4)"
    if [ "$admin" != root ] && [ "$(stat -c %U .)" != "$admin" ]; then
        # Everything in the checkout except the library folders kept inside it.
        as_root find . -xdev \( -path ./data -o -path ./media -o -path ./backups \) -prune \
            -o -exec chown -h "$admin:" {} +
        echo "The checkout now belongs to $admin."
    fi
    set_env IRIS_UID "$uid"
    set_env IRIS_GID "$gid"
    [ "$admin" != root ] && as_root chown "$admin:" .env
    as_root chmod 600 .env
    for dir in "$(data_dir)" "$(media_dir)" "$(backup_dir)"; do
        as_root chown -R "$uid:$gid" "$dir"
        as_root chmod 700 "$dir"
    done
    # The model caches are named volumes, filled so far by the previous user.
    docker compose run --rm --no-deps --user 0:0 --entrypoint chown iris -R "$uid:$gid" \
        /home/iris/.cache/huggingface /home/iris/.cache/whisper /home/iris/.insightface /home/iris/.EasyOCR
    echo "Data, media and backups belong to $user ($uid:$gid), mode 700; .env is 600."
}

same_library() {
    # Is folder $1 the library at $2? Union filesystems (mergerfs) give the same
    # folder another inode, so the instance id inside it decides as well.
    # Once hardened the library is mode 700, so the comparison reads as root.
    as_root test -d "$1" || return 1
    [ "$(as_root stat -c %d:%i "$1")" = "$(as_root stat -c %d:%i "$2")" ] && return 0
    as_root test -f "$2/instance_id" && as_root cmp -s "$1/instance_id" "$2/instance_id"
}

doctor() {
    # Who else on this host can read the library: containers of other
    # projects that mount it, a folder containing it, or a file inside it.
    local project uid real name user puid source rel tail found=0
    project="$(env_value COMPOSE_PROJECT_NAME)"
    uid="$(env_value IRIS_UID)"
    real="$(realpath "$(data_dir)")"
    [ "$uid" = "$(id -u "${SUDO_USER:-$(id -un)}")" ] && \
        echo "Warning: Iris runs as your own user ($uid); run: $0 harden" >&2
    [ "$(stat -c %U .)" = root ] && echo "Warning: the checkout belongs to root; run: $0 harden" >&2
    # A non-blank separator: tabs are whitespace to read, so an empty field would vanish.
    while IFS='|' read -r name user puid source; do
        [ -n "$source" ] || continue
        user="${user:-root}"
        # linuxserver.io images start as root and drop to PUID.
        [ -n "$puid" ] && user="uid $puid (PUID)"
        case "$source/" in
            "$real"/*)
                found=1
                echo "$name mounts $source, inside the library, and runs as $user"
                continue ;;
        esac
        # The library's path seen from this mount: /a/b/data -> data, b/data, a/b/data.
        rel="" tail="$real"
        while [ "$tail" != / ] && [ -n "$tail" ]; do
            rel="/$(basename "$tail")$rel"
            tail="$(dirname "$tail")"
            if same_library "$source$rel" "$real"; then
                found=1
                echo "$name sees the library at $source$rel and runs as $user"
                break
            fi
        done
    done < <(docker ps --format '{{.Names}}' | while read -r name; do
        docker inspect "$name" --format \
            '{{range .Mounts}}{{$.Name}}|{{$.Config.User}}|{{range $.Config.Env}}{{$kv := split . "="}}{{if eq (index $kv 0) "PUID"}}{{index $kv 1}}{{end}}{{end}}|{{.Source}}{{"\n"}}{{end}}' \
            | sed 's#^/##'
    done | grep -v "^$project-" || true)
    if [ "$found" = 0 ]; then
        echo "No container of another project mounts the library."
    else
        echo "Root, or the same user as Iris, reads it; another user is stopped by mode 700." \
            "Mount only the folders a service needs."
    fi
}

prepare_env() {
    if [ ! -f .env ]; then
        cp .env.example .env
        sed -i "s/^IRIS_UID=.*/IRIS_UID=$(id -u)/; s/^IRIS_GID=.*/IRIS_GID=$(id -g)/" .env
        # Compose names a project after its folder, so two installs in folders
        # with the same name (say /tmp/Iris and ~/Iris) would drive the same
        # container. A name derived from this path keeps installs apart.
        printf '\nCOMPOSE_PROJECT_NAME=iris-%s\n' "$(printf '%s' "$project_root" | sha256sum | cut -c1-8)" >> .env
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
    local backups data media
    backups="$(env_value IRIS_BACKUP_DIR)"
    data="$(data_dir)"
    media="$(media_dir)"
    local dir
    for dir in "$data" "$media" "${backups:-backups}"; do
        [ -e "$dir" ] || mkdir -p "$dir"
        # After `harden` the service user owns them; leave its mode alone.
        if [ -O "$dir" ]; then chmod 700 "$dir"; fi
    done
    # Shared spaces copy between the two with reflinks, which work only
    # within one filesystem; across two, every copy takes space again.
    if [ "$(stat -c %d "$data")" != "$(stat -c %d "$media")" ]; then
        echo "Warning: $data and $media are on different filesystems; shared spaces will store full copies." >&2
    fi
    IRIS_BACKUP_HOST_DIR="$(realpath "${backups:-backups}")"
    export IRIS_BACKUP_HOST_DIR
}

nvidia_gpu_present() {
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1
}

docker_reaches_nvidia() {
    # The NVIDIA Container Toolkit registers a Docker runtime, or a CDI spec.
    docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia ||
        [ -e /etc/cdi/nvidia.yaml ] || [ -e /var/run/cdi/nvidia.yaml ]
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
}

disable_gpu() {
    sed -i 's|^COMPOSE_FILE=\(.*docker-compose.gpu.yml.*\)$|#COMPOSE_FILE=\1|' .env
    unset COMPOSE_FILE
}

choose_image() {
    # The NVIDIA image only starts where Docker can hand the GPU to it, so the
    # choice is made here; using or not using the GPU is then a setting in
    # System > Installation.
    case "$1" in
        --gpu)
            enable_gpu
            echo "Image: NVIDIA (requested with --gpu)."
            ;;
        --cpu)
            disable_gpu
            echo "Image: CPU (requested with --cpu)."
            ;;
        *)
            if nvidia_gpu_present && docker_reaches_nvidia; then
                enable_gpu
                echo "Image: NVIDIA — found $(nvidia-smi -L | head -n 1 | sed 's/ (UUID.*//')."
            elif nvidia_gpu_present; then
                disable_gpu
                echo "Image: CPU — an NVIDIA GPU is present, but Docker cannot use it." >&2
                echo "  Install the NVIDIA Container Toolkit, then run install again to use it." >&2
            else
                disable_gpu
                echo "Image: CPU — no NVIDIA GPU found."
            fi
            ;;
    esac
}

print_setup_instructions() {
    local code_file
    code_file="$(data_dir)/setup_code"
    if ! as_data_owner test -f "$code_file"; then
        echo "Setup is complete. Sign in at $(local_url)/"
        return
    fi
    echo
    echo "Finish setup in the browser: $(local_url)/setup"
    echo "Installation code: $(as_data_owner head -n 1 "$code_file")"
    echo "(Show it again with: ./scripts/server.sh setup-code)"
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

configured_bind() {
    local configured
    configured="$(env_value IRIS_BIND)"
    printf '%s' "${configured:-127.0.0.1}"
}

local_url() {
    # Where this host reaches Iris: the listening address, or loopback when it is all of them.
    local host
    host="$(configured_bind)"
    [ "$host" = "0.0.0.0" ] && host="127.0.0.1"
    printf 'http://%s:%s' "$host" "$(configured_port)"
}

host_has_address() {
    command -v ip >/dev/null 2>&1 || return 0  # cannot check here; Docker will refuse an absent one
    ip -o -4 addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -qxF "$1"
}

set_listen() {
    local address="$1" option="${2:-}"
    [ "$address" = "localhost" ] && address="127.0.0.1"
    if ! [[ "$address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        echo "Give an IPv4 address: 127.0.0.1 (default), an address of this host, or 0.0.0.0." >&2
        exit 2
    fi
    if [ "$address" = "0.0.0.0" ] && [ "$option" != "--all-interfaces" ]; then
        echo "0.0.0.0 opens Iris on every network this host is on, the local network included." >&2
        echo "If that is the decision, run: $0 listen 0.0.0.0 --all-interfaces" >&2
        exit 2
    fi
    if [ "$address" != "0.0.0.0" ] && [ "${address%%.*}" != "127" ] && ! host_has_address "$address"; then
        echo "No network interface of this host has $address. Check it with: ip -4 addr" >&2
        exit 2
    fi
    prepare_env
    if grep -q '^IRIS_BIND=' .env; then
        sed -i "s/^IRIS_BIND=.*/IRIS_BIND=$address/" .env
    else
        printf '\nIRIS_BIND=%s\n' "$address" >> .env
    fi
    start_server
    wait_for_health
    echo "Iris listens on $address:$(configured_port)."
    if [ "${address%%.*}" = "127" ]; then
        echo "Only this host reaches it; put a mesh VPN, a tunnel or a reverse proxy in front of it."
        return
    fi
    echo "Devices that reach $address connect over plain HTTP: use it only on a network you"
    echo "trust or one that encrypts traffic itself (a mesh VPN), and confirm HTTP in the app."
    if [ "$(env_value IRIS_SESSION_HTTPS_ONLY)" = "true" ]; then
        echo "IRIS_SESSION_HTTPS_ONLY=true in .env: browsers cannot sign in over plain HTTP." >&2
        echo "Set it to auto (Secure cookie only over HTTPS) and run: $0 update" >&2
    fi
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
    echo "Iris is now available at $(local_url)"
    echo "To reach it from other devices, put a private layer in front of that"
    echo "address — a mesh VPN, a tunnel, or a reverse proxy with TLS."
    echo "With Tailscale, for example:"
    echo "  sudo tailscale serve --bg http://127.0.0.1:$port"
}

follow_release() {
    # Put this checkout on the release tag and select that version's image.
    # The image is pulled first: if the release is still being published, the
    # checkout and the running server stay exactly as they were.
    local target
    target="$(release_tag)" || true
    if [ -z "$target" ]; then
        echo "No release $(configured_version) to run (no matching vX.Y.Z tag)." >&2
        return 1
    fi
    IRIS_VERSION="${target#v}"
    if ! docker compose pull --quiet iris; then
        echo "The image for $target is not available yet (a release is published a few minutes" \
            "after its tag); nothing was changed. Try again shortly." >&2
        return 1
    fi
    git -c advice.detachedHead=false checkout --quiet "$target"
    IRIS_COMMIT="$(git rev-parse --short HEAD)"
    echo "Release: $target"
}

start_server() {
    # Run the published image; build it from this checkout only when that
    # version was never published (no git and no release, or before the first one).
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

# Where the library lives on the host: IRIS_DATA_DIR and IRIS_MEDIA_DIR in
# .env, by default data/ and media/ in this folder. docker-compose.yml mounts
# the same paths.
data_dir() {
    local dir
    dir="$(env_value IRIS_DATA_DIR)"
    printf '%s\n' "${dir:-data}"
}

media_dir() {
    local dir
    dir="$(env_value IRIS_MEDIA_DIR)"
    printf '%s\n' "${dir:-media}"
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
    as_data_owner python3 -m core.instance_backup verify "$snapshot"
    local data media owner
    data="$(data_dir)"
    media="$(media_dir)"
    echo "This replaces $data and $media with the backup. The current folders are"
    echo "kept next to them as *.before-restore-*, not deleted."
    read -r -p "Type 'restore' to continue: " answer
    [ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }
    owner="$(stat -c %u:%g "$data")"
    docker compose stop iris
    if ! as_data_owner python3 -m core.instance_backup restore "$snapshot" \
        --target data="$data" --target media="$media"; then
        echo "Restore failed; nothing was swapped. Starting the previous state." >&2
        docker compose up -d iris
        exit 1
    fi
    [ -O "$data" ] || as_root chown -R "$owner" "$data" "$media"
    # Restore swaps the host data/media directories atomically. Recreate the
    # stopped service so Docker binds the newly restored directory inodes.
    docker compose up -d --force-recreate iris
    wait_for_health
    echo "Restored. Indexes and thumbnails are rebuilt as they are used."
}

wait_for_health() {
    local attempt
    for attempt in $(seq 1 30); do
        # Connection errors are expected while the server starts; only the outcome matters.
        if curl --fail --silent "$(local_url)/healthz" >/dev/null 2>&1; then
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
        shift
        image_choice="" harden=1
        for arg in "$@"; do
            case "$arg" in
                --gpu|--cpu) image_choice="$arg" ;;
                --no-harden) harden=0 ;;
                *) echo "Usage: $0 install [--gpu|--cpu] [--no-harden]" >&2; exit 2 ;;
            esac
        done
        prepare_env
        choose_image "$image_choice"
        if [ -d .git ] && [ -n "$(release_tag)" ]; then
            git diff --quiet || { echo "Commit or stash local changes before installing." >&2; exit 1; }
            follow_release || exit 1
        fi
        [ "$harden" = 1 ] && harden_files
        start_server
        wait_for_health
        echo "Daily backups go to $IRIS_BACKUP_HOST_DIR at $(env_value IRIS_BACKUP_TIME)" \
            "($(env_value IRIS_BACKUP_TIMEZONE)); change it in .env or in System > Installation."
        echo "Shared-space storage (IRIS_SPACE_STORAGE in .env):"
        show_storage || true
        print_setup_instructions
        ;;
    setup-code)
        print_setup_instructions
        ;;
    create-admin)
        require_compose
        shift
        docker compose run --rm -it iris python scripts/bootstrap_admin.py "$@"
        docker compose up -d iris
        wait_for_health
        ;;
    attach-library)
        # An old catalog copied into data/ becomes the library of an empty
        # account. The server must be stopped meanwhile; it comes back either way.
        require_compose
        shift
        docker compose stop iris
        attach_status=0
        docker compose run --rm -it --no-deps iris python scripts/attach_library.py "$@" || attach_status=$?
        docker compose up -d iris
        wait_for_health
        exit "$attach_status"
        ;;
    status)
        require_compose
        docker compose ps
        health=$(curl --fail --silent "$(local_url)/healthz")
        echo "$health"
        # The code the app shows before trusting this server: they must match.
        code=$(printf '%s' "$health" | sed -n 's/.*"identity_code":"\([A-F0-9-]*\)".*/\1/p')
        if [ -n "$code" ]; then echo "Server identity code: $code"; fi
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
            git fetch --quiet --tags origin
            follow_release || exit 1
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
    harden)
        require_compose
        prepare_env
        harden_files
        # The container's user changed: recreate it.
        docker compose up -d --force-recreate iris
        wait_for_health
        ;;
    doctor)
        require_compose
        doctor
        ;;
    backups)
        require_python3
        as_data_owner python3 -m core.instance_backup list "$(backup_dir)"
        ;;
    verify-backup)
        require_python3
        [ "$#" -eq 2 ] || { echo "Usage: $0 verify-backup <backup folder>" >&2; exit 2; }
        as_data_owner python3 -m core.instance_backup verify "$2"
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
    listen)
        require_compose
        [ "$#" -ge 2 ] || { echo "Usage: $0 listen <127.0.0.1|host address|0.0.0.0 --all-interfaces>" >&2; exit 2; }
        set_listen "$2" "${3:-}"
        ;;
    port)
        require_compose
        [ "$#" -eq 2 ] || { echo "Usage: $0 port <1024-65535>" >&2; exit 2; }
        set_port "$2"
        ;;
    *)
        echo "Usage: $0 {install [--gpu|--cpu] [--no-harden]|harden|doctor|setup-code|create-admin|attach-library --user <conta> --from data/<pasta>|status|logs|update|backup [--pin] [--accept-missing]|backups|verify-backup <folder>|restore <folder>|storage|port <1024-65535>|listen <address>}" >&2
        exit 2
        ;;
esac
