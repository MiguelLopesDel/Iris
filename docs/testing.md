# Testar e depurar o Iris

O Iris tem três camadas complementares de verificação. Comece pela primeira antes
de cada atualização; use as outras antes de disponibilizar uma versão à família.

## 1. Verificação rápida do código

Na raiz do repositório:

```bash
python -m compileall -q core routers scripts tests server.py
python -m ruff check core routers scripts tests server.py
./scripts/run_tests.sh standard
```

Isso não carrega modelos grandes e cobre regras de conta, login, sessão, isolamento
de bibliotecas, importação, busca, API e interface. Para testes que exigem CLIP ou
um catálogo real, use `./scripts/run_tests.sh menu` e escolha o grupo apropriado.

## 2. Smoke test do servidor em execução

Depois de subir o Docker, veja os logs correlacionados por `request_id`:

```bash
docker compose logs -f iris
```

Em outro terminal, valide saúde, bloqueio de visitante e uma conta real:

```bash
python scripts/verify_server.py \
  --url https://iris.sua-rede-privada.exemplo \
  --expect-private --username alice --password 'uma senha forte'
```

Para testar o fluxo completo de envio, indexação, leitura de miniatura e isolamento,
escolha uma imagem descartável com nome único. O comando altera a biblioteca da
primeira conta; não o rode com foto privada que você não queira importar.

```bash
python scripts/verify_server.py \
  --url https://iris.sua-rede-privada.exemplo \
  --expect-private --username alice --password 'uma senha forte' \
  --upload /tmp/iris-smoke-unico.jpg \
  --second-username bob --second-password 'outra senha forte'
```

O script falha se um visitante acessa `/api/info`, se caminhos internos vazam, se o
upload/indexação falha, se a miniatura não pode ser lida, ou se Bob vê o arquivo
enviado por Alice. Ele nunca registra senhas, cookies, parâmetros de busca ou corpos
de requisição.

## 3. Instalação limpa em Docker

Antes de publicar uma versão ou depois de alterar dependências, Dockerfiles,
autenticação ou o bootstrap, rode:

```bash
./scripts/test_release.sh
```

Ele usa uma cópia temporária do commit atual e uma porta local separada. Não lê,
altera ou remove a biblioteca real: constrói a imagem CPU, verifica que visitantes
não acessam a API privada, cria uma primeira conta descartável e valida login. Docker
e curl são necessários; o teste costuma demorar alguns minutos na primeira execução.

## 4. Logs e diagnóstico

No Docker, use `IRIS_LOG_FORMAT=json` (padrão no Compose). Cada linha de requisição
tem método, caminho sem query string, status, duração, ID da conta quando autenticada
e `request_id`; a resposta também devolve `X-Request-ID`. Pesquise esse ID nos logs
ao receber um relato de bug. Fotos, conteúdo de upload, cookies, tokens, senhas e
consultas de busca não entram no log de acesso.

Para uma instalação sem Docker, `IRIS_LOG_FILE=/var/log/iris/app.log` ativa arquivo
rotativo (20 MiB, cinco arquivos por padrão). Ajuste `IRIS_LOG_LEVEL` para `DEBUG`
apenas enquanto investiga um problema e volte para `INFO` depois. Defina
`IRIS_LOG_ACCESS=0` se quiser registrar apenas erros HTTP.

## Pipeline automático

O workflow em `.github/workflows/ci.yml` executa compilação, Ruff, a suíte rápida,
uma auditoria de dependências e o teste de instalação Docker em cada push e pull
request. O smoke test usa apenas uma biblioteca temporária vazia, não baixa modelos
de IA e não envia mídia a serviços externos. Os testes de modelo/catálogo real
permanecem uma decisão explícita do administrador.
