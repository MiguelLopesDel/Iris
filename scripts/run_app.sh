#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    IRIS_PYTHON="$VIRTUAL_ENV/bin/python"
elif [ -x "$PROJECT_ROOT/venv/bin/python" ]; then
    IRIS_PYTHON="$PROJECT_ROOT/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    IRIS_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    IRIS_PYTHON="$(command -v python)"
else
    echo "Erro: Python não encontrado. Crie o ambiente virtual e instale requirements.txt." >&2
    exit 1
fi

echo "Iniciando Iris..."
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONWARNINGS="ignore::FutureWarning:transformers,ignore::UserWarning:torch"
cd "$PROJECT_ROOT"

exec "$IRIS_PYTHON" -m uvicorn server:app \
    --host "${IRIS_HOST:-127.0.0.1}" \
    --port "${IRIS_PORT:-8501}" \
    "$@"
