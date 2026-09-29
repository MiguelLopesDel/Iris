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

No Android, ative o diagnóstico local em **Configurações** antes de reproduzir uma
abertura lenta. Para álbuns, compare `collection.members_data` (requisição + decode),
`collection.first_content` (toque até o primeiro frame com itens),
`network.collection_members.total` (HTTP) e `preview.image`/`preview.video`
(miniaturas). Se apenas o primeiro conteúdo for alto, o custo está na composição; se
HTTP e dados subirem juntos, está no servidor/rede; se previews forem altos, está no
cache ou geração de miniaturas.

Para medir sincronização de mídia, compare `sync.upload_throughput` (bytes
confirmados divididos pelo tempo de fila, incluindo descoberta/espera) com a taxa
`MB/s durante chamadas PUT` (união do tempo em que pelo menos uma chamada de envio
de payload estava em andamento). O segundo
exclui intervalos de varredura sem envio e é mais útil para comparar a capacidade do
caminho de transferência com a conexão. Ele ainda inclui leitura do MediaStore e
backpressure local, não é uma medida pura do enlace WAN. Consulte também
`sync.media_scan.total`, `sync.media_hash.total`, `sync.upload.first_job_start`,
`sync.upload_chunk.body`, `sync.upload_chunk.ack_wait` e `sync.upload_complete.total`.
O diagnóstico também exibe `sync.confirmed_items` em itens/s, contando itens cujo
resultado foi confirmado pelo servidor — inclusive duplicatas que não precisaram
transmitir bytes. Isso é essencial para comparar uma coleção de arquivos pequenos
com poucos arquivos grandes.
Um diagnóstico sem PUT ativo pode indicar que o trabalho ainda não começou ou foi
adiado pelo Android; essa tela não mede o tempo desde o agendamento do WorkManager.

No servidor, cada fase concluída registra `phase_ms` e, quando aplicável, bytes,
itens e estado: `chunk_durable` (recepção + flush/fsync + atualização do offset),
`verify_hash`, `durable_move` (validação/movimentação durável) e
`catalog_registration` (registro da mídia; sem IA quando desativada). Correlacione
com `request_id` da linha HTTP correspondente. Esses logs não incluem nome de
arquivo, ID de upload nem caminho local. Com `IRIS_LOG_FORMAT=json`, os campos são
estruturados; no formato texto os valores também ficam visíveis na mensagem. Os
tempos separam persistência/processamento da espera percebida pelo cliente, mas não
medem rádio/Wi-Fi nem o custo do MediaStore.

## Benchmark Android de lote sintético

O teste de instrumentação
`AccountScopedUploadQueueTest#upload_throughput_benchmark_reports_hash_and_transfer_cost_by_file_count`
envia 32 MiB sintéticos em três formatos (1, 8 ou 32 arquivos), por um
`MockWebServer` local. No AVD API 35 deste ambiente, uma execução observou:

| Arquivos | Vazão ponta a ponta | Itens confirmados/s | Hash + inclusão na fila |
| ---: | ---: | ---: | ---: |
| 1 | 103,4 MB/s | 3,12 | 84 ms |
| 8 | 111,3 MB/s | 26,60 | 101 ms |
| 32 | 56,2 MB/s | 53,69 | 168 ms |

Isso valida que o diagnóstico distingue bytes/s de itens/s e mostra o custo
adicional de muitos arquivos. **Não estima o desempenho do seu celular, Wi-Fi,
Tailscale ou servidor:** o destino é um mock no próprio AVD e não representa
persistência do Iris. Portanto, esses números não justificam mudar concorrência nem
confirmam a meta de 45–50 MB/s. Para isso, rode o mesmo perfil com mídia sintética
contra um servidor de teste isolado e, depois, repita no aparelho/rede reais sem
usar sua biblioteca principal.

## Benchmark da API retomável e armazenamento durável

Para exercitar o protocolo real do Android contra uma instância isolada do Iris
(reserva em lote, PUT retomável, SHA-256, movimento durável e catálogo sem IA), use
um laboratório marcado e somente `localhost`:

```bash
python scripts/load_lab.py prepare --users 1 --records-per-user 1
# Terminal 1
python scripts/load_lab.py serve --port 8851
# Terminal 2
python scripts/benchmark_sync_pipeline.py --url http://127.0.0.1:8851 \
  --total-mib 32 --files 1,8,32 --concurrency 1 --output .iris-load/reports/c1.json
```

Repita com `--concurrency 4` e `--concurrency 8` para comparar a concorrência;
cada perfil usa bytes sintéticos exclusivos e exige recibo durável de todos os
itens. O benchmark recusa URLs fora de loopback e só roda se encontrar o marcador
do laboratório. Os resultados são gravados dentro de `.iris-load/`, nunca na
biblioteca real.

Uma execução neste ambiente de desenvolvimento (servidor local, HTTP loopback,
armazenamento local, 32 MiB por perfil, sem IA) mediu:

| Arquivos de 32 MiB | Concorrência | MB/s até confirmação | Itens confirmados/s |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 243,2 | 7,25 |
| 1 | 4 | 212,5 | 6,33 |
| 1 | 8 | 99,3 | 2,96 |
| 8 | 1 | 149,3 | 35,60 |
| 8 | 4 | 143,1 | 34,11 |
| 8 | 8 | 140,7 | 33,55 |
| 32 | 1 | 53,6 | 51,10 |
| 32 | 4 | 53,6 | 51,10 |
| 32 | 8 | 52,0 | 49,62 |

Neste host, concorrência acima de 1 não melhorou os lotes; para 1/8 arquivos,
piorou. Para 32 itens ficou praticamente igual. Em um perfil separado de 32 itens
de 1 MiB, o servidor registrou médias de 8,5 ms em `chunk_durable`, 21,7 ms em
`verify_hash`, 33,0 ms em `durable_move` e 60,3 ms em `catalog_registration`.
As fases de finalização acontecem em lote/concorrem, portanto esses tempos se
sobrepõem e não devem ser somados como duração de parede. A amostra é pequena e
local: indica onde medir e que não se deve simplesmente elevar o limite atual; não
determina a concorrência ótima para Android, disco remoto ou Tailscale.

Depois dos testes, encerre o servidor de laboratório e remova somente o ambiente
descartável criado pelo comando `prepare`:

```bash
python scripts/load_lab.py reset --yes
```

### Android contra API e armazenamento reais (AVD)

O teste de instrumentação
`AccountScopedUploadQueueTest#isolated_real_server_upload_benchmark_measures_end_to_end_photo_video_payload_path`
leva a fila Android e o protocolo de upload até uma instância descartável do Iris:
12 fotos sintéticas de 1 MiB e 4 vídeos sintéticos de 36 MiB (156 MiB no total).
Ele mede inclusão/hash na fila e tempo até confirmação durável no catálogo. A IA fica
desligada. O teste aceita apenas loopback ou a ponte `10.0.2.2` do emulador; o comando
`android/scripts/test.sh benchmark-sync` seleciona explicitamente um `emulator-*` e
recusa executar se só houver telefone físico conectado.

Execuções no AVD API 35, contra o servidor de laboratório local, mediram:

| Execução | Workers | Fila + hash | Envio até confirmação | Vazão ponta a ponta | Vazão enquanto PUT ativo |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 4 | 391 ms | 6,55 s | 24,96 MB/s | 27,99 MB/s |
| A | 8 | 376 ms | 5,33 s | 30,70 MB/s | 31,37 MB/s |
| A | 12 | 376 ms | 5,28 s | 31,00 MB/s | 31,60 MB/s |
| A | 16 | 378 ms | 5,28 s | 31,00 MB/s | 31,61 MB/s |
| B | 4 | 579 ms | 14,22 s | 11,50 MB/s | 11,82 MB/s |
| B | 8 | 550 ms | 11,09 s | 14,75 MB/s | 14,97 MB/s |
| B | 12 | 510 ms | 10,83 s | 15,11 MB/s | 15,27 MB/s |
| B | 16 | 495 ms | 9,31 s | 17,57 MB/s | 17,84 MB/s |
| C | 4 | 481 ms | 11,50 s | 14,22 MB/s | 15,53 MB/s |
| C | 8 | 467 ms | 9,40 s | 17,40 MB/s | 17,60 MB/s |
| C | 12 | 464 ms | 9,65 s | 16,95 MB/s | 17,17 MB/s |
| C | 16 | 476 ms | 9,67 s | 16,92 MB/s | 17,17 MB/s |

As execuções discordam sobre o platô: A estabilizou após 12 workers, B ainda melhorava
em 16, e C atingiu seu melhor resultado em 8. A rodada C foi correlacionada com
amostras de recurso a cada 0,5 s, alinhadas pelos timestamps de cada transferência:

| Workers | MB/s | CPU média do Uvicorn | RSS médio / pico | Escrita do Uvicorn | CPU host média | Memória host usada | CPU cgroup média | Throttling CPU |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 14,22 | 80,7% | 429 / 529 MiB | 16,24 MiB/s | 66,2% | 84,8% | 7,08 cores | 0 |
| 8 | 17,40 | 87,2% | 379 / 382 MiB | 19,22 MiB/s | 67,6% | 84,7% | 7,49 cores | 0 |
| 12 | 16,95 | 87,9% | 380 / 383 MiB | 19,08 MiB/s | 69,5% | 84,6% | 7,57 cores | 0 |
| 16 | 16,92 | 85,5% | 379 / 384 MiB | 18,94 MiB/s | 67,9% | 84,8% | 7,49 cores | 0 |

Nesta rodada, aumentar de 8 para 12/16 não melhorou a vazão; Uvicorn permaneceu
próximo de um núcleo ocupado, e a escrita do próprio processo ficou perto da vazão
confirmada. Isso é consistente com um limite serial no caminho de recebimento/
persistência, mas não separa custo de cópia, flush/fsync e contenção de disco. O host
estava carregado (CPU média ~66–70%, memória usada ~85%); não houve throttling no
cgroup. Os contadores de host/cgroup incluem outros processos, então não permitem
atribuir a atividade total ao Iris. A rodada B também mostrou `chunk_durable` de
32 MiB entre ~7,3–8,4 s e blocos de 4 MiB entre ~0,3–0,8 s, mas seus logs não foram
preservados integralmente para uma agregação completa. As latências do cliente ficam
no log `IrisUploadBench`, separadas em criação, corpo do bloco, espera de ACK e
finalização em lote.

Três execuções sintéticas ainda não estabelecem uma concorrência ótima: são no mesmo
AVD/host compartilhado e a ordem dos perfis não foi randomizada, podendo haver efeitos
de aquecimento ou carga externa. Os dados já refutam a suposição simples de que mais
workers sempre ajudam, mas não justificam ainda um controlador adaptativo. Próximo
passo de medição: preservar também todos os eventos de fase do servidor em arquivo,
randomizar a ordem e repetir os perfis; então validar no aparelho/rede reais antes de
selecionar uma política. Nada disso estima telefone, Wi-Fi, Tailscale ou servidor fraco.

Para repetir sem pôr a senha na linha de comando ou no histórico do shell:

```bash
python scripts/load_lab.py prepare --users 1 --records-per-user 1
# Terminal 1: manter o servidor descartável ativo
python scripts/load_lab.py serve --port 8851
# Terminal 2: AVD inicializado; conta criada pelo laboratório
export IRIS_BENCH_USERNAME=load-001
read -rsp 'Senha descartável do laboratório: ' IRIS_BENCH_PASSWORD; echo
export IRIS_BENCH_PASSWORD
ANDROID_HOME=/app/.android-sdk ANDROID_AVD_HOME=/app/.android-avd \
  PATH=/app/.android-sdk/platform-tools:$PATH ./android/scripts/test.sh benchmark-sync
unset IRIS_BENCH_PASSWORD
```

Para correlacionar cada perfil com carga do ambiente, mantenha o servidor rodando e
inicie o sampler abaixo em outro terminal antes do benchmark Android. Ele amostra a
cada 0,5 s CPU/RSS/I/O do processo Uvicorn, CPU/memória/disco do host e contadores
CPU/I/O/memória do cgroup visível ao container. O PID é apenas o servidor descartável;
os campos de host/cgroup são agregados e podem incluir Gradle e o AVD.

```bash
server_pid="$(pgrep -n -f '[u]vicorn server:app.*--port 8851')"
test -n "$server_pid"
python3 scripts/sample_sync_resources.py --pid "$server_pid" --interval 0.5 \
  --duration 100 --output .iris-load/reports/resources.jsonl
```

Depois que a amostragem terminar, capture os logs do único AVD conectado e junte as
janelas de upload (marcadas com epoch milliseconds) aos recursos:

```bash
emulator_serial="$(adb devices | awk '$1 ~ /^emulator-/ && $2 == "device" { print $1; exit }')"
test -n "$emulator_serial"
adb -s "$emulator_serial" logcat -d -s IrisUploadBench:I '*:S' \
  > .iris-load/reports/android-upload.log
python3 scripts/summarize_sync_resources.py \
  --samples .iris-load/reports/resources.jsonl \
  --android-log .iris-load/reports/android-upload.log \
  --output .iris-load/reports/resource-summary.json
# Terminal 1: Ctrl+C para encerrar o Uvicorn descartável antes de remover seus dados.
python scripts/load_lab.py reset --yes
```

O resumo alinha cada perfil pela janela real de transferência e informa médias/picos
de CPU e RSS, taxas de I/O e delta de throttling. Se a amostragem não cobrir um
perfil, ele será reportado com zero amostras, em vez de inferir dados. As contagens
de I/O do processo são específicas do Uvicorn; host/cgroup podem conter atividade
concorrente e não devem ser atribuídos integralmente ao servidor.

O benchmark cria arquivos de teste no MediaStore do AVD e os remove ao final. Não
usa mídia pessoal. A instalação/teste deve ser serializada no emulador; não use
`gradlew connectedDebugAndroidTest` neste caso, pois o Gradle pode descobrir e tentar
instalar nos telefones físicos conectados. O comando acima fixa `adb -s` no AVD. O
teste AVD passou (1/1). Uma tentativa anterior do Gradle enumerou também um telefone,
cuja instalação foi cancelada pelo Android (`INSTALL_FAILED_USER_RESTRICTED`); zero
testes rodaram nesse aparelho, e nenhum resultado físico foi coletado.

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

Meça a rede antes de culpar o Iris: com Tailscale, `tailscale ping nome-do-servidor`
do notebook/celular; com outra malha ou VPN, o equivalente dela. Rode o teste a partir
de um dispositivo real da rede, não do próprio servidor — assim o número inclui o
Wi-Fi, a VPN e o upload que o usuário realmente tem.

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
    --url https://iris.sua-rede-privada.exemplo \
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
    --url https://iris.sua-rede-privada.exemplo \
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
