#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== Instalador Automático do Iris ===${NC}"
echo "For a private Docker server, use ./scripts/server.sh install instead."

# The dependency locks target CPython 3.13 on Linux x86_64.
PYTHON=python3.13
command -v "$PYTHON" &> /dev/null || PYTHON=python3
if ! command -v "$PYTHON" &> /dev/null; then
    echo -e "${RED}Erro: Python 3.13 não encontrado. Instale o Python 3.13 antes de continuar.${NC}"
    exit 1
fi

if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 13))'; then
    echo -e "${RED}Iris requer Python 3.13.${NC}"
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Criando ambiente virtual (venv)..."
    "$PYTHON" -m venv venv
else
    echo "Ambiente virtual já existe."
fi

echo "Ativando ambiente e instalando bibliotecas..."
source venv/bin/activate

pip install --upgrade pip

if pip install --no-deps --require-hashes -r requirements.txt && \
    python scripts/check_deps.py; then
    echo -e "${GREEN}✅ Instalação concluída com sucesso!${NC}"
    echo ""
    echo "This installer is for local development or a single-user local run."
    echo "Para indexar suas imagens, execute:"
    echo "  source venv/bin/activate"
    echo "  python -m core.indexer"
    echo ""
    echo "Para abrir o programa, execute:"
    echo "  ./scripts/run_app.sh"
else
    echo -e "${RED}❌ Falha na instalação das dependências.${NC}"
    exit 1
fi
