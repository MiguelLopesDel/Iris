#!/usr/bin/env bash
set -euo pipefail

if (($# < 3)); then
  echo "Usage: $0 <scenario.yaml> <empty-lab-root-on-target-disk> <report-output-dir> [python]" >&2
  exit 2
fi

scenario=$1
lab_root=$2
report_dir=$3
python_bin=${4:-python3.13}
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

exec "$python_bin" -m scripts.perf_lab run "$repo_root/$scenario" \
  --root "$lab_root" --output "$report_dir"
