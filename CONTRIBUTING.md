# Contribuindo para o Iris

Você não precisa de Docker, servidor, Tailscale, outra máquina ou fotos pessoais
para começar. O fluxo de desenvolvimento é local e usa apenas dados descartáveis.

## Começo rápido

```bash
git clone https://github.com/MiguelLopesDel/Iris.git
cd Iris
python3 -m venv venv
source venv/bin/activate
pip install -e '.[dev]'
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
Para testar busca semântica, busca por imagem ou indexação real, pare o processo e
rode:

```bash
python scripts/dev.py start --with-model
```

Esse modo pode baixar pesos de IA e usar CPU/GPU, mas ainda mantém contas e mídia
dentro de `.iris-dev/`.

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
- Teste a mudança na UI localmente, em uma conta comum e, quando fizer sentido, em
  uma conta administradora.
- Não inclua `.iris-dev/`, `data/`, mídia, bancos, relatórios pessoais ou segredos.
- Descreva no PR como a mudança foi testada e se exige reindexação/migração.
