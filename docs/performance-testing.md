# Capacidade e teste de carga do Iris

Este guia mede o Iris como ele será usado: várias abas e pessoas navegando ou
pesquisando pela mesma rede Tailscale. Não execute testes de carga sobre a biblioteca
principal enquanto uma importação importante estiver em andamento.

## O que medir

Defina um alvo antes de escolher otimizações. Para um servidor familiar, um ponto de
partida razoável é: galeria p95 abaixo de 500 ms, busca semântica p95 abaixo de 2 s
em CPU ou 1 s em CUDA, e nenhuma resposta 5xx sob 2--5 pessoas ativas. Esses são
objetivos operacionais, não promessas universais: catálogo, modelo, disco e hardware
mudam muito o resultado.

Há quatro recursos independentes:

| Fluxo | Gargalo mais provável | Medida útil |
| --- | --- | --- |
| Galeria/coleções | SQLite, SSD, JSON e miniaturas | RPS, p95, I/O de disco |
| Busca por texto/imagem | codificador CLIP + ranking FAISS | p95, CPU/GPU, fila |
| Importação | disco, OCR, vídeo e modelos | duração, CPU/GPU, espaço livre |
| Acesso remoto | upload do servidor e Wi-Fi/Internet | latência de cliente e throughput |

## Preparação

Suba o Iris normalmente, espere o modelo terminar de carregar e mantenha uma aba de
logs aberta:

```bash
docker compose logs -f iris
docker stats iris
```

Em NVIDIA, em outro terminal, acompanhe GPU/VRAM:

```bash
nvidia-smi dmon -s pucm
```

Para medir rede Tailscale antes do Iris, use `tailscale ping nome-do-servidor` do
notebook/celular. Rode o teste a partir de um dispositivo real da tailnet, não apenas
do próprio servidor: isso inclui Wi-Fi, VPN e upload disponíveis ao usuário.

## Carga reproduzível

O script [scripts/load_test.py](../scripts/load_test.py) só faz leituras. Ele aquece
o servidor, cria clientes concorrentes autenticados, lê toda resposta e devolve RPS,
p50/p95/p99 e erros. Senhas não são gravadas no relatório.

## Laboratório local com muitas contas

Para reproduzir uma casa inteira sem usar fotos ou contas reais, use o laboratório
descartável. Ele vive em `.iris-load/`, ignorado pelo Git, e cria credenciais somente
nesse diretório com permissão `0600`.

```bash
# Terminal 1: cria 50 bibliotecas isoladas, cada uma com dados sintéticos.
python scripts/load_lab.py prepare --users 50 --records-per-user 100
python scripts/load_lab.py serve

# Terminal 2: 50 pessoas vendo galeria e 50 uploads sob pressão.
python scripts/load_lab.py run \
  --url http://127.0.0.1:8501 \
  --viewers 50 --searchers 0 --uploaders 50 \
  --upload-mib 8 --upload-mbps 10
```

O comando `run` inicia as três pressões em paralelo e grava `browse.json`,
`search.json` e `uploads.json` em `.iris-load/reports/`. Sem `--with-model`, a parte
de busca de IA não deve ser incluída e a importação responde 409 depois do parser
multipart; isso mede login, isolamento, galeria e pressão de rede sem carregar IA.
Para incluir inferência e indexação reais, reinicie o laboratório com
`python scripts/load_lab.py serve --with-model` e então use, inicialmente, um cenário
menor como `--viewers 20 --searchers 4 --uploaders 4`.

Uploads são reais: 50 usuários × 8 MiB gravam aproximadamente 400 MiB. O payload é
um JPEG minúsculo com padding e representa rede, parser multipart, armazenamento
temporário e admissão de importação, não qualidade de IA. O teto é 2 GiB por padrão;
para simular mais dados, aumente `--max-total-gib` conscientemente. Ao terminar:

```bash
python scripts/load_lab.py reset --yes
```

O perfil sem modelo rejeita a indexação com HTTP 409 após receber o multipart. Isso é
intencional: ele isola o custo de rede/parser e evita que um teste rápido carregue
CLIP ou OCR inesperadamente. Para validar upload + indexação de ponta a ponta, use
`--with-model` com poucos usuários e imagens pequenas reais; o payload preenchido do
teste de pressão não representa um arquivo de mídia que deva ser indexado.

Comece por galeria e aumente gradualmente a concorrência:

```bash
for n in 1 2 4 8; do
  python scripts/load_test.py \
    --url https://nome-do-servidor.sua-tailnet.ts.net \
    --expect-private --username alice --password 'uma senha forte' \
    --scenario browse --concurrency "$n" --requests 200 \
    --output "data/reports/load-browse-$n.json"
done
```

Depois execute busca semântica. Use no máximo 1, 2 e 4 clientes inicialmente; o Iris
tem por padrão dois workers de busca (`IRIS_SEARCH_WORKERS=2`).

```bash
for n in 1 2 4; do
  python scripts/load_test.py \
    --url https://nome-do-servidor.sua-tailnet.ts.net \
    --expect-private --username alice --password 'uma senha forte' \
    --scenario search --query 'pessoa na praia' --concurrency "$n" --requests 60 \
    --output "data/reports/load-search-$n.json"
done
```

`--scenario mixed` produz uma busca a cada quatro requisições de galeria. Para
simular contas diferentes hoje, execute o mesmo comando em dois terminais, cada qual
com a credencial de uma conta. Compare os relatórios e `docker stats`.

## Como interpretar

- Se p95 da galeria sobe com CPU baixa mas disco ocupado, use SSD/NVMe e investigue
  miniaturas/SQLite; não aumente workers de IA.
- Se busca p95 cresce depois de duas requisições simultâneas e CPU/GPU fica ocupada,
  a fila de busca é o limitador esperado. Aumentar `IRIS_SEARCH_WORKERS` só faz
  sentido depois de medir; em CPU fraca, 1 ou 2 costuma ser melhor.
- Se há RAM/VRAM crescente ao alternar usuários, o problema é o cache de engines:
  cada engine hoje pode carregar seu próprio modelo. Não aumente
  `IRIS_ENGINE_CACHE_SIZE` até medir memória.
- Se o servidor está folgado, mas o cliente remoto tem p95 alto, compare com o teste
  rodado no host. A diferença é rede/Wi-Fi/upload, não FAISS.
- Qualquer 429, 5xx, timeout ou queda de `docker stats` é falha do teste, mesmo que a
  média pareça boa. Guarde os JSONs em `data/reports/` para comparar versões.

## Limites atuais e próximo trabalho

O desenho atual é adequado para uma família pequena, não para muitos usuários ativos:
SQLite e FAISS são isolados por conta; há um processo Uvicorn e dois workers globais
de busca; uma importação por biblioteca pode rodar ao mesmo tempo que buscas. O
próximo passo baseado nos resultados deve ser, nesta ordem:

1. fila global limitada para importação/indexação, com prioridade para buscas;
2. um registro compartilhado de modelo por `(modelo, dispositivo)`, mantendo índices
   e bancos isolados por conta, para não duplicar CLIP em RAM/VRAM;
3. métricas contínuas de fila, latência e uso de processo, com alerta;
4. somente se o servidor familiar realmente saturar: proxy reverso, workers de
   inferência separados e roteamento de cada conta ao dono do índice.

Não aumente `uvicorn --workers` agora: cada processo carregaria cache, modelo e
índices próprios, piorando RAM/VRAM e tornando o estado de importação inconsistente.
