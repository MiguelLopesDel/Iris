# Perfis de dependencias

As dependencias do Iris sao divididas por funcao, sem repetir pins entre os
perfis:

- `requirements-common.txt`: dependencias diretas compartilhadas pela
  aplicacao.
- `requirements.txt`: runtime de producao CPU (PyTorch CPU + ONNX Runtime CPU).
- `requirements-cuda.txt`: runtime de producao NVIDIA CUDA 12.6. Deve ser
  instalado diretamente em um ambiente limpo; nao instala primeiro as wheels
  CPU.
- `requirements-dev.txt`: perfil CPU mais pytest, httpx, Ruff e pip-audit.
- `constraints-common.txt`: pins transitivos compartilhados. Dependencias
  diretas ficam fora dele para cada versao ter uma unica declaracao.

`pyproject.toml` guarda metadados, comandos e configuracao de build/lint/teste,
mas nao declara dependencies: Iris e um aplicativo com runtimes mutuamente
exclusivos, nao uma biblioteca Python com uma instalacao universal. `pip
install -e . --no-deps` registra os comandos locais sem deixar o resolvedor do
PyPI trocar as wheels explicitamente escolhidas pelo perfil.

## Desenvolvimento CPU (recomendado)

Use sempre um virtualenv dentro do repositorio; nao instale o Iris no Python
global nem reutilize um ambiente CUDA para testes CPU.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q tests/test_dependency_contract.py
.venv/bin/python -m pytest -q
```

Python suportado: 3.11 e 3.12. O CI usa Python 3.12. O `requirements-dev.txt`
e `requirements.txt` escolhem wheels CPU explicitamente e fixam o conjunto de
dependencias diretas e transitivas; use o `python` do mesmo virtualenv para
pytest, Ruff e os comandos `iris-*`.

## NVIDIA CUDA 12.6

Crie outro ambiente, sem instalar o perfil CPU antes:

```bash
python3.12 -m venv .venv-cuda
.venv-cuda/bin/python -m pip install --upgrade pip
.venv-cuda/bin/python -m pip install -r requirements-cuda.txt
.venv-cuda/bin/python -m pip check
.venv-cuda/bin/python -c "import torch, torchvision, torchaudio, onnxruntime"
```

Nao combine `requirements.txt` e `requirements-cuda.txt`, nem mantenha
`onnxruntime` e `onnxruntime-gpu` instalados juntos. `requirements-cuda.txt`
instala PyTorch 2.7.1/torchvision 0.22.1/torchaudio 2.7.1 com CUDA 12.6 e
ONNX Runtime GPU 1.20.2. O CPU usa as mesmas versoes base de PyTorch com wheel
`+cpu` e ONNX Runtime 1.20.1. As combinacoes correspondem as trincas oficiais
listadas na
[documentacao do PyTorch](https://docs.pytorch.org/get-started/previous-versions/).

O perfil GPU tem Dockerfile e Compose separados (`Dockerfile.gpu` e
`docker-compose.gpu.yml`). A imagem GPU requer Docker com suporte NVIDIA e
precisa ser validada numa maquina NVIDIA; a compilacao CPU nao comprova esse
perfil.

## Ao atualizar pins

1. Altere a dependencia direta somente em `requirements-common.txt`, no perfil
   CPU, no CUDA, ou em `requirements-dev.txt`, conforme sua funcao.
2. Regere `constraints-common.txt` num ambiente limpo para Python 3.11/3.12,
   preservando nele apenas pins transitivos compartilhados.
3. Rode `pip check`, `tests/test_dependency_contract.py`, imports reais e a
   suite dentro do perfil; teste CPU e CUDA separadamente.
4. Atualize os perfis do Docker e CI junto com os arquivos de requisitos.

As constraints nao incluem hashes de artefato; os pins fixam versoes, mas nao
garantem bytes identicos entre indexadores ou plataformas. Se a reproducao da
cadeia de fornecimento passar a ser requisito, adote locks com hashes por
plataforma em uma mudanca propria.
