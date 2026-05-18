#!/bin/bash

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== Instalador Automático do Meme Compass ===${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Erro: Python 3 não encontrado. Por favor, instale o Python 3 antes de continuar.${NC}"
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Criando ambiente virtual (venv)..."
    python3 -m venv venv
else
    echo "Ambiente virtual já existe."
fi

echo "Ativando ambiente e instalando bibliotecas..."
source venv/bin/activate

pip install --upgrade pip

if pip install -r requirements.txt; then
    echo -e "${GREEN}✅ Instalação concluída com sucesso!${NC}"
    echo ""
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
