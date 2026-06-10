#!/bin/bash
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
set -euo pipefail

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

echo "Iniciando Iris..."
export HOME="${HOME:-/tmp}"
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
export PYTHONWARNINGS="ignore::FutureWarning:transformers,ignore::UserWarning:torch"
if [ ! -w "$HOME" ]; then
    export HOME=/tmp
fi
streamlit run app/main.py "$@"
