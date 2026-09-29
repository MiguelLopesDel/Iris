# Perfis de dependencias

O Iris tem dois perfis de instalacao. `requirements.txt` e o perfil CPU padrao;
`requirements-cuda.txt` substitui os runtimes de inferencia em maquinas NVIDIA
com CUDA 12.6. Os pacotes diretos estao fixados por versao, e
`constraints-common.txt` fixa as versoes transitivas compartilhadas resolvidas
no ambiente CPU testado. `pyproject.toml`
repete as versoes publicas para o pacote Python; ONNX Runtime fica nos extras
`cpu-runtime`/`nvidia-runtime` para nao exigir os dois wheels em um perfil GPU.
A variante `+cpu` ou `+cu126`
de PyTorch vem somente do arquivo de perfil. Para desenvolvimento, instale
primeiro `requirements.txt` e use `pip install --no-deps -e '.[dev]'` depois.

Python suportado pelo pacote: 3.11 e 3.12. O CI usa 3.12; a validacao local
isolada de 2026-09-23 usou 3.11.15. Python 3.10 nao atende ao NumPy 2.4.3
fixado. O arquivo comum fixa versoes, mas nao hashes ou wheels por plataforma;
um perfil GPU ainda pode acrescentar bibliotecas CUDA especificas. Verificar
`pip check`, imports e suite apos cada atualizacao continua necessario.

## CPU

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip check
venv/bin/python -m pytest -q tests/test_dependency_contract.py
venv/bin/python -m pytest -q
```

O Dockerfile CPU executa `pip check` e importa os quatro runtimes principais
durante o build, para falhar cedo se um wheel nao combinar com o ambiente.

## NVIDIA CUDA 12.6

```bash
venv/bin/python -m pip uninstall -y onnxruntime
venv/bin/python -m pip install --force-reinstall -r requirements-cuda.txt
venv/bin/python -m pip check
venv/bin/python -c "import torch, torchvision, torchaudio, onnxruntime"
```

Nao deixe `onnxruntime` e `onnxruntime-gpu` instalados juntos. A versao CPU do
ONNX Runtime e 1.20.1, e a variante GPU e 1.20.2 porque nao ha wheel GPU
1.20.1 para o alvo. O Dockerfile GPU usa Ubuntu 24.04 com Python 3.12 em venv
e copia ambos os arquivos de perfil. Esse perfil ainda precisa de um build e
teste em maquina com Docker/NVIDIA; os testes locais desta data validaram CPU.

As trincas oficiais de PyTorch 2.7.1, torchvision 0.22.1 e torchaudio 2.7.1
para CPU e CUDA 12.6 constam na
[documentacao do PyTorch](https://docs.pytorch.org/get-started/previous-versions/).
