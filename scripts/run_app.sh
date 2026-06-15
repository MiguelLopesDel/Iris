#!/usr/bin/env bash
set -euo pipefail

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

echo "Iniciando Iris..."
export HOME="${HOME:-/tmp}"
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
export PYTHONWARNINGS="ignore::FutureWarning:transformers,ignore::UserWarning:torch"
if [ ! -w "$HOME" ]; then
    export HOME=/tmp
fi

exec python3 -m uvicorn server:app \
    --host "${IRIS_HOST:-127.0.0.1}" \
    --port "${IRIS_PORT:-8501}" \
    "$@"
