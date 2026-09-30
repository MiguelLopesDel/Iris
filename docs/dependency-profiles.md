# Perfis de dependencias

O Iris separa o que voce **declara** do que e **instalado**:

| arquivo | papel | editar? |
|---|---|---|
| `requirements.in` | dependencias diretas da aplicacao, com a versao minima testada | sim |
| `requirements-cpu.in` | `requirements.in` + PyTorch CPU + ONNX Runtime CPU | sim |
| `requirements-cuda.in` | `requirements.in` + PyTorch CUDA 13 + ONNX Runtime GPU | sim |
| `requirements-dev.in` | perfil CPU + pytest, httpx, Ruff, pip-audit, uv | sim |
| `requirements.txt` | lock completo do perfil CPU (producao e imagem Docker) | nao, e gerado |
| `requirements-cuda.txt` | lock completo do perfil NVIDIA | nao, e gerado |
| `requirements-dev.txt` | lock completo do perfil de desenvolvimento | nao, e gerado |

Os locks sao gerados por `scripts/lock_deps.sh` e fixam **o grafo inteiro**, com
hashes, para Linux x86_64 e CPython 3.13. Instale-os sempre assim:

```bash
pip install --no-deps --require-hashes -r requirements.txt
python scripts/check_deps.py            # ou --cuda no perfil NVIDIA
```

## Por que `--no-deps`

O `insightface` declara `opencv-python` e `onnxruntime`. Esses pacotes instalam
os mesmos modulos (`cv2`, `onnxruntime`) que o `opencv-python-headless` e o
`onnxruntime-gpu` que o Iris realmente usa. Instalados lado a lado, o ultimo a
ser gravado vence, e a imagem GPU pode perder o `CUDAExecutionProvider` sem
nenhum erro: os rostos passariam a rodar na CPU.

Por isso os locks omitem esses pacotes (`--no-emit-package`) e a instalacao nao
resolve dependencias. O `pip check` acusa as omissoes; o
`scripts/check_deps.py` roda o `pip check`, tolera **somente** elas e confere
que o runtime certo esta presente (CUDA no perfil NVIDIA, CPU no perfil CPU).

`pyproject.toml` guarda metadados, comandos e configuracao de build/lint/teste,
mas nao declara dependencies: Iris e um aplicativo com runtimes mutuamente
exclusivos, nao uma biblioteca. `pip install -e . --no-deps` registra os comandos
locais sem mexer nas wheels do perfil.

## Desenvolvimento CPU (recomendado)

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --no-deps --require-hashes -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python scripts/check_deps.py
.venv/bin/python -m pytest -q
```

## NVIDIA (CUDA 13)

Crie outro ambiente, sem instalar o perfil CPU antes:

```bash
python3.13 -m venv .venv-cuda
.venv-cuda/bin/python -m pip install --no-deps --require-hashes -r requirements-cuda.txt
.venv-cuda/bin/python scripts/check_deps.py --cuda
```

As bibliotecas CUDA e cuDNN vem como wheels do pip; o host so precisa do driver
NVIDIA >= 580. CUDA 13 cobre GPUs da serie RTX 20 (Turing) em diante; placas
mais antigas (GTX 10xx) rodam o Iris na CPU. A imagem Docker e a mesma do perfil
CPU com `--build-arg IRIS_PROFILE=cuda`, e o `docker-compose.gpu.yml` so
sobrepoe a imagem `-cuda` e o acesso a GPU.

## Atualizar dependencias

1. Para adicionar ou remover uma dependencia direta, edite o `.in` do perfil
   certo (`requirements.in` quando serve a todos).
2. Regere os locks:

   ```bash
   scripts/lock_deps.sh             # mantem os pins atuais sempre que possivel
   scripts/lock_deps.sh --upgrade   # leva tudo a ultima versao compativel
   ```

3. Instale o lock num ambiente limpo e rode `scripts/check_deps.py`, a suite e
   uma indexacao real com modelos (CPU e, numa maquina NVIDIA, `--cuda`).
4. Depois de validar, suba as versoes minimas nos `.in` para as testadas.

O CI regera os locks e falha se eles divergirem dos `.in`, entao um `.in`
editado sem regerar os locks nao passa despercebido.
