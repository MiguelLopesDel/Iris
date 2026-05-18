#!/bin/bash
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
set -euo pipefail

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

echo "Iniciando Meme Compass..."
export HOME="${HOME:-/tmp}"
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
if [ ! -w "$HOME" ]; then
    export HOME=/tmp
fi
streamlit run app/main.py "$@"
