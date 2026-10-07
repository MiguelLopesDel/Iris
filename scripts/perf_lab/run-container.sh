#!/usr/bin/env bash
set -euo pipefail

if (($# < 2)); then
  echo "Usage: $0 <scenario.yaml> <existing-empty-workspace-on-target-disk> [image] [extra perf_lab run options]" >&2
  exit 2
fi

scenario=$1
workspace=$(realpath "$2")
image=${3:-iris-perf-lab:local}
shift $(($# < 3 ? $# : 3))
# Anything after the image goes to "perf_lab run", e.g. --variant no_gzip.
extra=("$@")
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

if [[ ! -d "$workspace" ]] || [[ -n "$(find "$workspace" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "Workspace must be an existing empty directory: $workspace" >&2
  exit 2
fi

if [[ "$scenario" = /* ]]; then
  scenario_file=$scenario
else
  scenario_file="$repo_root/$scenario"
fi
if [[ ! -f "$scenario_file" ]]; then
  echo "Scenario not found: $scenario_file" >&2
  exit 2
fi
scenario_file=$(realpath "$scenario_file")
case "$scenario_file" in
  "$repo_root"/*) scenario_container_path="/app/${scenario_file#"$repo_root"/}" ;;
  *)
    echo "Scenario must be inside the Iris checkout so the read-only mount can access it" >&2
    exit 2
    ;;
esac

exec docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,size=512m \
  --user "$(id -u):$(id -g)" \
  --volume "$repo_root:/app:ro" \
  --volume "$workspace:/benchmark:rw" \
  --workdir /app \
  "$image" python3 -m scripts.perf_lab run \
  "$scenario_container_path" \
  --root /benchmark/data --output /benchmark/report "${extra[@]}"
