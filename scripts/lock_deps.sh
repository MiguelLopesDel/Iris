#!/usr/bin/env bash
# Regenerate the fully pinned dependency locks from the requirements*.in inputs.
#
#   scripts/lock_deps.sh             # re-resolve keeping current pins where possible
#   scripts/lock_deps.sh --upgrade   # move every package to its newest compatible version
#
# Each lock lists the whole dependency graph and is installed with
# `pip install --no-deps`, so pip never pulls a package the lock left out.
# That matters because insightface declares opencv-python and onnxruntime,
# which install the same `cv2` / `onnxruntime` modules as the
# opencv-python-headless / onnxruntime-gpu we actually use; installed side by
# side, the last one written wins and the GPU image can silently lose CUDA.
#
# The locks target the supported runtime: Linux x86_64, CPython 3.13.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null 2>&1 || { echo "uv is required: pip install uv" >&2; exit 2; }

common=(
    --python-version 3.13
    --python-platform x86_64-manylinux_2_28
    --default-index https://pypi.org/simple
    --index-strategy unsafe-best-match
    --emit-index-url
    --generate-hashes
    --no-emit-package opencv-python
    --custom-compile-command "scripts/lock_deps.sh"
    --quiet
    "$@"
)

uv pip compile requirements-cpu.in -o requirements.txt \
    --index https://download.pytorch.org/whl/cpu "${common[@]}"
uv pip compile requirements-dev.in -o requirements-dev.txt \
    --index https://download.pytorch.org/whl/cpu "${common[@]}"
uv pip compile requirements-cuda.in -o requirements-cuda.txt \
    --index https://download.pytorch.org/whl/cu130 "${common[@]}" \
    --no-emit-package onnxruntime

echo "Locks updated: requirements.txt requirements-dev.txt requirements-cuda.txt"
