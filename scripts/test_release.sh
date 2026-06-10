#!/bin/bash
# test_release.sh - Simula uma nova instalação em um ambiente limpo

# 1. Configuração do Diretório Temporário
PROJECT_NAME="Iris_Release_Test"
# Mudamos para um diretório local para evitar estourar o /tmp (RAM)
TEMP_DIR="./build_test_release"

echo "=========================================="
echo "🧪 INICIANDO TESTE DE RELEASE (AMBIENTE ISOLADO)"
echo "=========================================="
echo "Diretório de Teste: $TEMP_DIR"

# 2. Limpeza prévia
if [ -d "$TEMP_DIR" ]; then
    echo "Limpando teste anterior..."
    rm -rf "$TEMP_DIR"
fi
mkdir -p "$TEMP_DIR"

# 3. Cópia dos Arquivos (Simulando 'git clone')
# Copiamos APENAS o código fonte e configs essenciais
# Ignoramos venv, cache, builds antigos e db locais
echo "Copiando arquivos do projeto..."

# Copia pastas de código
cp -r app "$TEMP_DIR/"
cp -r core "$TEMP_DIR/"
cp -r utils "$TEMP_DIR/"
cp -r scripts "$TEMP_DIR/"

# Copia arquivos da raiz
cp requirements.txt "$TEMP_DIR/"
cp pyproject.toml "$TEMP_DIR/"
cp README.md "$TEMP_DIR/"
cp .gitignore "$TEMP_DIR/"

# Cria uma pasta vazia de imagens só para o teste não quebrar se for rodado
mkdir -p "$TEMP_DIR/minhas_imagens"

# 4. Executa a Instalação no Ambiente Isolado
echo ">>> Executando install.sh no ambiente isolado..."
cd "$TEMP_DIR" || exit

# Torna executável caso tenha perdido permissão na cópia
chmod +x scripts/install.sh
chmod +x scripts/run_app.sh

# Roda a instalação
if ./scripts/install.sh; then
    echo "✅ Instalação (pip install) concluída com sucesso no ambiente isolado."
else
    echo "❌ FALHA CRÍTICA: O script install.sh falhou."
    exit 1
fi

# 5. Teste de Execução (Dry Run)
# Ativa o venv criado no temp dir
source venv/bin/activate

echo ">>> Verificando se os módulos principais importam corretamente..."
# Tenta importar as libs mais pesadas para ver se quebra
python -c "import torch; import cv2; import easyocr; import sentence_transformers; print('Módulos carregados com sucesso!')"

if [ $? -eq 0 ]; then
    echo "✅ Teste de Importação: SUCESSO. O ambiente está funcional."
    echo ""
    echo "🎉 O PROJETO ESTÁ PRONTO PARA O GIT!"
    echo "Pode commitar com segurança."
else
    echo "❌ FALHA CRÍTICA: O ambiente instalou, mas o Python não conseguiu carregar as bibliotecas."
    exit 1
fi

# Limpeza opcional (comentada para debug)
# rm -rf "$TEMP_DIR"
