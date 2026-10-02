# Contribuindo para o Iris

Você não precisa de Docker, servidor, Tailscale, outra máquina ou fotos pessoais
para começar. O fluxo de desenvolvimento é local e usa apenas dados descartáveis.

## Começo rápido

```bash
git clone https://github.com/MiguelLopesDel/Iris.git
cd Iris
python3.13 -m venv .venv
.venv/bin/python -m pip install --no-deps --require-hashes -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps -e .
. .venv/bin/activate
./scripts/install_git_hooks.sh
python scripts/dev.py start
```

Abra `http://127.0.0.1:8501`. O comando cria `.iris-dev/` (ignorado pelo Git), inicia
o servidor em hot reload e mostra as credenciais descartáveis:

| Conta | Senha | Papel |
| --- | --- | --- |
| `admin` | `iris-dev-admin` | administrador |
| `familia` | `iris-dev-member` | usuário comum |

Cada conta já possui uma imagem diferente. Assim é possível testar login, logout,
galeria, permissões e isolamento em duas abas anônimas/incógnitas, sem tocar em
`data/`, `media/` ou qualquer biblioteca real.

O modo padrão usa `IRIS_LOAD_MODEL=0` para iniciar rapidamente e sem baixar modelos.
O `requirements-dev.txt` é o lock completo da combinação CPU testada mais as
ferramentas de desenvolvimento; por isso é instalado com `--no-deps`. Instale o
pacote editável também com `--no-deps`, para o resolvedor do PyPI não trocar as
wheels do perfil. Para mudar dependências, veja `docs/dependency-profiles.md`.
Para testar busca semântica, busca por imagem ou indexação real, pare o processo e
rode:

```bash
python scripts/dev.py start --with-model
```

Esse modo pode baixar pesos de IA e usar CPU/GPU, mas ainda mantém contas e mídia
dentro de `.iris-dev/`.

## Trabalhar em uma mudança

O que se espera de cada commit, pull request e tag, e como uma mudança passa por
homologação até chegar à produção, está em [docs/process.md](docs/process.md).

1. Atualize `main` e crie uma branch curta para uma tarefa. Use nomes como
   `fix/android-gallery-scroll`, `feat/server-invites` ou
   `docs/deployment-guide`.
2. Antes de editar, procure o contrato e os testes da área em `docs/` e
   `tests/` (ou `android/app/src/test`). Preserve os invariantes de conta,
   dispositivo e originais descritos em [docs/process.md](docs/process.md).
3. Faça commits pequenos, cada um com uma intenção, usando
   [Conventional Commits](https://www.conventionalcommits.org/):
   `fix(sync): retry interrupted uploads` ou `docs(server): clarify backup flow`.
4. Rebase/atualize sua branch com `main` antes de abrir o PR; não force push em
   branches compartilhadas. Abra um PR para `main` e aguarde CI e revisão.

Não trabalhe diretamente em `main`. Não inclua no PR dados reais, credenciais,
arquivos gerados ou mudanças sem relação com a tarefa. Se a mudança de
comportamento não estiver especificada, registre a dúvida no PR antes de ampliar
o escopo.

## Verificar uma alteração

```bash
python scripts/dev.py test
```

O comando compila, executa Ruff e roda a suíte rápida completa. Para testes de
catálogo real, modelo ou qualidade, consulte [docs/testing.md](docs/testing.md).
Para simular dezenas de contas e uploads concorrentes inteiramente na máquina local,
consulte [docs/performance-testing.md](docs/performance-testing.md).

## Reiniciar dados de desenvolvimento

```bash
python scripts/dev.py reset --yes
```

Ele só apaga um diretório que possua o marcador criado pelo próprio script; nunca
aceita apagar a raiz do repositório ou uma pasta sem esse marcador.

## Antes de abrir um PR

- Execute `python scripts/dev.py test`.
- Rode as verificações específicas da área alterada; por exemplo, testes Android
  em `android/` ou `./scripts/test_release.sh` para mudanças em instalação,
  dependências, autenticação ou bootstrap. Consulte [docs/README.md](docs/README.md)
  para escolher o conjunto correto.
- Teste a mudança na UI localmente, em uma conta comum e, quando fizer sentido, em
  uma conta administradora.
- Não inclua `.iris-dev/`, `data/`, mídia, bancos, relatórios pessoais ou segredos.
- Descreva no PR como a mudança foi testada e se exige reindexação/migração.
- Inclua screenshots para mudanças visuais e notas de compatibilidade/rollback
  para mudanças de banco, API, dependências ou implantação.

O gancho `pre-push` verifica os commits enviados por segredos comuns, bancos,
índices, arquivos de mídia fora das pastas de assets aprovadas e `.env`. Ele não
substitui revisão humana, mas evita os acidentes mais comuns antes de publicar.
