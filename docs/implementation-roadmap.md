# Plano de implementacao do Iris

> Estado: plano de execucao para a [visao de produto](product-vision.md)
> confirmada em 2026-09-23. As secoes de linha de base abaixo preservam o
> historico; este resumo separa o que ja esta no codigo das validacoes ainda
> pendentes.

## Estado observado em 2026-09-29

- **Servidor e Spaces:** existem contas privadas, membros/papeis, catalogo e
  originais independentes, lixeira, armazenamento com cota, albuns e busca de
  Spaces. Backups de instancia incluem catalogos e originais e validam as
  referencias antes de publicar. Testes cobrem isolamento e migracao de
  esquema; a prova ponta a ponta de upgrade/restore em Docker esta disponivel
  em `scripts/test_upgrade_recovery.sh`, mas ainda precisa ser executada.
- **Android:** o codigo inclui credenciais e fila por servidor/conta,
  sincronizacao em segundo plano, galeria local e remota unificada, navegacao
  de Spaces e metricas de sincronizacao. Testes JVM e compilacao dos APKs
  debug/instrumentacao passaram neste ambiente; os testes instrumentados e o
  uso real com dois perfis ainda sao validacoes pendentes.
- **Operacao:** backup/restauracao de instancia, verificacao, rotina de
  atualizacao e um benchmark descartavel de sincronizacao estao implementados.
  A conclusao de producao ainda depende de executar o ensaio de upgrade e
  restauracao isolado, exercitar Android em emulador e revisar resultados com
  hardware/dispositivo identificados.

O codigo presente nao prova por si so estabilidade em escala ou comportamento
de segundo plano em todo fabricante Android. Nao trate compilacao como teste
de instrumentacao, nem o ensaio sintetico como uma garantia sobre uma
instalacao real.

## Meta do primeiro produto completo

Uma instancia auto-hospedada atende contas privadas de uma familia ou equipe.
Cada conta pode sincronizar seu Android para a biblioteca pessoal. Contas
autorizadas entram, por suas proprias credenciais, em espacos compartilhados
independentes, com galeria, albuns, busca e permissoes. A instalacao, a
atualizacao e a recuperacao dos dados sao verificaveis sem usar o servidor
pessoal como ambiente de testes.

## Ponto de partida observado no codigo

| Area | Ja existe | Lacuna para a visao |
| --- | --- | --- |
| Contas privadas | `core/users_db.py`, `routers/auth.py` e o middleware de `server.py` criam contas, autenticam e vinculam cada requisicao a uma biblioteca por conta. | Desativacao/exclusao recuperavel de conta e provas de acesso para espacos compartilhados. |
| Biblioteca e busca | `server.py` oferece registros, busca, colecoes e midia da biblioteca autenticada. | Catalogo de espacos, albuns compartilhados e busca federada com origem identificada. Colecoes pessoais nao equivalem a espacos. |
| Android | Fila de upload, catalogo local, login por dispositivo e feed de mudancas ja existem. | Hoje URL, credenciais, catalogo e fila sao unicos por instalacao do app; faltam perfis isolados e navegacao/compartilhamento de espacos. |
| Exclusao | `/api/trash` move arquivos para a lixeira do sistema. | Isso nao comprova lixeira Iris navegavel, restauracao e expurgo automatico em 30 dias para itens, contas e espacos. |
| Operacao | `scripts/server.sh`, Compose, `scripts/test_release.sh` e `scripts/verify_server.py` cobrem instalacao e verificacoes basicas. | Falta ensaio de atualizacao/migracao e restauracao com os novos dados, sem tocar na instalacao real. |

## Ordem de entrega

### 0. Contratos e linha de base

- Criar cenarios de teste descartaveis com varias contas e midia sintetica,
  cobrindo API web, Android e tentativa de acesso cruzado a miniaturas e
  originais. Nao usar `data/` nem `media/` reais.
- Medir tempos de primeira pagina, busca, miniaturas e sincronizacao nos
  cenarios atuais. Guardar tamanho do acervo e hardware junto das medidas;
  nao transformar meta de escala em capacidade alegada sem teste.
- Seguir o [contrato de migracao e recuperacao](migration-and-recovery-contract.md)
  antes de alterar esquemas de contas ou bibliotecas; exercitar backup e
  restauracao em copia temporaria.

Pronto quando: suite repetivel mede a base atual e falha ao detectar vazamento
entre duas contas. Instalacao limpa continua passando.

### 1. Primeiro espaco compartilhado, ponta a ponta no servidor

- Criar identidade propria para cada espaco, seus membros e seus itens. O
  conteudo compartilhado nao pode depender da existencia da copia pessoal.
  Manter a fronteira da biblioteca privada: acesso a um espaco exige
  verificacao explicita de participacao e nivel de permissao.
- Entregar criacao por qualquer conta, convite de contas da mesma instancia,
  papeis de visualizador/colaborador/gestor, galeria paginada, adicionamento
  explicito de itens e remocao conforme autoria/permissao.
- Expor API de leitura e miniaturas/originais do espaco sem aceitar IDs de
  outra biblioteca ou outro espaco. Nao reutilizar um endpoint pessoal
  mudando somente o ID no URL.
- Testar a matriz de acesso: anonimo, conta fora do espaco, visualizador,
  colaborador, gestor e administrador da instalacao. O administrador nao
  ganha leitura de midia por ser administrador.

Pronto quando: Alice cria um espaco, Bob entra como colaborador, Carol como
visualizadora; Bob contribui uma foto, Carol pode ve-la e salvar uma copia,
mas nao altera o espaco; Dave, sem convite, nao obtem nem metadados nem bytes.
Apagar a copia privada de Bob nao apaga a copia compartilhada.

### 2. Governanca, recuperacao e busca

- Permitir mais de um gestor, transferencia de gestao, saida/revogacao de
  membro e recuperacao administrativa do ultimo gestor sem leitura da midia.
  Contribuicoes permanecem apos a saida do autor.
- Criar estados recuperaveis e expurgo apos 30 dias para itens, espacos e
  contas. Exclusao de conta revoga acesso imediatamente; exclusao de espaco
  exige confirmacao reforcada. Expurgo so ocorre depois de testes de
  restauracao e integridade de referencias a conteudo compartilhado.
- Fazer a busca consultar a biblioteca privada e somente os espacos
  acessiveis, reunindo resultados paginados com origem explicita e filtro
  por acervo. Nao carregar todos os resultados na memoria para mescla.
- Oferecer no web navegacao separada para biblioteca pessoal e espacos,
  criacao/gestao do espaco, albuns, compartilhamento explicito, lixeira e
  recuperacao. A grade pessoal nao inclui itens compartilhados por acidente.

Pronto quando: retirar Bob revoga o acesso dele imediatamente sem apagar suas
contribuicoes; a busca de Carol inclui apenas seus acervos autorizados;
excluir e restaurar item, espaco e conta preserva dados durante o prazo.

### 3. Android com perfis isolados e espacos

- Separar URL, sessao, credenciais, preferencias, catalogo, miniaturas e
  fila de envio por perfil de conta/servidor. Um perfil fica ativo por vez;
  trocar de perfil interrompe trabalho do anterior antes de iniciar o novo.
- Manter o backup automatico restrito a biblioteca privada do perfil ativo.
  Compartilhar com um espaco exige acao explicita. Mostrar espacos em
  navegacao separada, com autoria/permissao e origem nos resultados de busca.
- Testar com dois servidores locais e duas contas no mesmo servidor: nenhuma
  requisicao, token, miniatura ou upload pode cruzar perfis. Rodar testes JVM
  e instrumentados no emulador existente, incluindo rede offline e retomada.

Pronto quando: alternar entre perfis nao mistura grades nem filas e nao envia
fotos automaticamente a um espaco compartilhado.

### 4. Operacao e desempenho do primeiro lancamento

- Estender o ensaio de release para upgrade de banco existente, backup,
  restauracao e rollback ensaiado em dados sinteticos. O comando de update
  deve preservar `.env`, bibliotecas e midia sem intervencao manual obscura.
- Medir cargas com mais contas e acervos crescentes; localizar gargalos de
  consulta, indices, miniaturas e permissao por traces/percentis. Otimizar
  apenas os maiores custos observados. Avaliar dispositivos Android fracos.
- Documentar limites demonstrados, requisitos de armazenamento, seguranca da
  exposicao externa, recuperacao e caminho de atualizacao. Nao prometer
  milhoes de itens por conta antes de validar esse patamar.

Pronto quando: uma instalacao descartavel sai de uma versao anterior, atualiza
e restaura sem perda de arquivos; web e Android passam pelos cenarios do MVP;
latencias e erros sao registrados com carga e hardware conhecidos.

## Ordem imediata

1. Rodar `scripts/test_upgrade_recovery.sh` em ambiente Docker isolado e
   corrigir regressao de migracao, snapshot ou restore antes de atualizar o
   servidor pessoal.
2. Executar a suite instrumentada no emulador e validar alternancia entre
   duas contas/servidores, retomada offline e origem dos itens na galeria.
3. Executar `scripts/test_release.sh` depois que a arvore estiver limpa; ele
   valida a versao registrada em `HEAD`, nao mudancas locais.
4. Revisar os seis limites de responsabilidade levantados na auditoria de
   2026-09-29 em
   mudancas pequenas, preservando os contratos e testes ja registrados.

Nao migrar automaticamente albuns pessoais ou arquivos privados para um
espaco compartilhado. O fluxo de copiar para um espaco e sempre explicito.

## Verificacao da linha de base em 2026-09-23

- O teste novo `tests/test_multiuser_media_boundary.py` usa duas contas e
  imagens geradas em diretorio temporario. Cobre sessao web, token Android,
  listagem, miniaturas, originais e tentativas de acessar ou alterar o envio
  pendente de outra conta. Passou junto com os testes de contas e
  sincronizacao relacionados: 5 testes aprovados.
- `python3 -m compileall` e Ruff passaram para o teste novo.
- As versoes diretas de CPU foram fixadas em `requirements.txt` e alinhadas ao
  `pyproject.toml`; 119 versoes compartilhadas tambem foram fixadas em
  `constraints-common.txt`. A combinacao PyTorch 2.7.1/torchvision
  0.22.1/torchaudio 2.7.1 foi instalada em ambiente virtual isolado. `pip
  check` passou; a suite completa retornou 464 aprovados e 18 pulados. O
  Python global continua com uma extensao
  torchvision CUDA 13 incompatível com seu torch CUDA 12.8; nao e o ambiente
  de validacao do projeto.
- Em 2026-09-29, os pins diretos foram separados em `requirements-common.txt`,
  `requirements.txt` (CPU), `requirements-cuda.txt` e `requirements-dev.txt`;
  `constraints-common.txt` passou a conter somente dependencias transitivas.
  A duplicacao de pins em `pyproject.toml` foi removida, e os ambientes de
  desenvolvimento/CI passaram a instalar o perfil CPU isolado. O ambiente
  global ainda pode conter outras wheels e nao deve ser usado para validar Iris.
- O perfil CUDA tambem foi resolvido em modo `pip --dry-run` com as mesmas
  restricoes, usando PyTorch 2.7.1/cu126 e ONNX Runtime GPU 1.20.2. A imagem
  base NVIDIA CUDA 12.6 + Ubuntu 24.04 foi confirmada no registry, mas a
  imagem GPU ainda nao foi compilada neste ambiente sem Docker/GPU.
- Em 2026-09-30, as dependencias passaram a ser declaradas em
  `requirements*.in` e travadas por `scripts/lock_deps.sh` (uv) em locks
  completos com hashes, instalados com `--no-deps`; `requirements-common.txt`,
  `constraints-common.txt` e `Dockerfile.gpu` sairam. Tudo foi levado a
  ultima versao estavel: Python 3.13, PyTorch 2.14.1/torchvision 0.29.1
  (`torchaudio` removido, sem uso), CUDA 13 (cu130), ONNX Runtime 1.30,
  insightface 2.0, opencv 5.0, sentence-transformers 6.1, transformers 5.18,
  numpy 2.5. O insightface 2.0 declara `opencv-python` e `onnxruntime`, que
  sombreiam `opencv-python-headless` e `onnxruntime-gpu`; os locks os omitem e
  `scripts/check_deps.py` valida o resultado. Validacao: suite 618 aprovados e
  18 pulados; indexacao real (EasyOCR, Florence-2, CLIP, Whisper, InsightFace)
  em CPU e numa RTX 4050 com driver 615, com as cinco sessoes ONNX do
  InsightFace em `CUDAExecutionProvider`. A imagem unica (`IRIS_PROFILE`
  cpu/cuda sobre `python:3.13-slim`) nao foi compilada aqui, sem Docker; o
  build fica a cargo do CI (`release-smoke` e `release.yml`).
- Os testes de hot-path, lixeira e enrichment foram ajustados para o contrato
  `get_record(position)`/`_record_for_db_id`, sem reverter a refatoracao do
  catalogo em andamento. O ensaio direcionado passou 35/35.
- `scripts/measure_stage0.py` mede API sem modelo em biblioteca descartavel.
  Com 1.000 itens, 10 rodadas, Python 3.11.15, Linux x86-64, 12 CPUs e 15,3 GiB
  de RAM: primeira pagina 27,2 ms; miniatura fria 6,3 ms; pagina aquecida
  mediana 2,9 ms; busca por nome 12,4 ms; miniatura aquecida 2,4 ms; envio
  start/chunk/complete 26,1 ms. Sao medidas em processo,
  sem rede, modelo ou renderizacao Android; nao servem como promessa de latencia
  em celular ou acervo de milhoes de itens.
- `tests/test_disposable_recovery.py` ensaia, com duas contas sinteticas, copia
  SQLite consistente de `users.db` e bancos privados, copia de segredo/midias,
  restauracao no mesmo caminho e acesso autorizado sem vazamento cruzado,
  incluindo a participacao em um espaco compartilhado. Isso
  **nao** e ainda um comando de backup/restauracao multiusuario em producao.

## Primeira fatia da etapa 1

- `core/shared_spaces.py` acrescenta identidade e membros de espaco no
  `users.db`, sem reutilizar IDs de midia ou bancos privados. Qualquer conta
  autenticada pode criar um espaco e se torna gestora; somente gestoras
  adicionam contas da mesma instancia como visualizadoras, colaboradoras ou
  gestoras. O administrador da instancia nao recebe participacao automatica.
- `GET/POST /api/spaces` e `GET /api/spaces/{id}` e
  `GET/POST /api/spaces/{id}/members` cobrem listagem, criacao e membros.
  Quem esta fora recebe 404 mesmo conhecendo o ID do espaco.
- O teste `tests/test_shared_spaces_api.py` cobre cinco contas, incluindo
  administrador sem acesso e acesso por token de dispositivo. A API **ainda
  nao oferece galeria nem bytes compartilhados**; a proxima fatia precisa
  criar catalogo proprio e provar a independencia da copia privada.

## Segunda fatia da etapa 1: catalogo de itens do espaco

- `core/space_catalog.py` guarda cada espaco em `data/spaces/<id>/`: um
  `space.db` proprio (versao de esquema em `PRAGMA user_version`; versao mais
  nova que a conhecida e recusada), originais copiados por SHA-256 em `media/`
  e miniaturas em `thumbnails/`. Nada referencia IDs ou caminhos da biblioteca
  privada; o mesmo conteudo adicionado duas vezes ao espaco vira um item e um
  arquivo.
- `GET/POST /api/spaces/{id}/items`, `GET /api/spaces/{id}/items/{item}`,
  `.../thumbnail`, `.../original` e `DELETE .../items/{item}`. Adicionar
  recebe o ID de um item da biblioteca privada de quem chama e so o resolve
  nessa biblioteca, com a mesma lista permitida de `/media/`. Visualizador so
  le; colaborador adiciona e remove o que adicionou; gestor remove qualquer
  item. Fora do espaco, inclusive o administrador da instalacao, tudo e 404.
- Remover do espaco e exclusao logica: o item some da galeria e os bytes
  permanecem. Ainda nao ha lixeira navegavel, restauracao nem expurgo em 30
  dias (etapa 2).
- `tests/test_space_catalog.py` (7) e `tests/test_shared_space_items_api.py`
  cobrem a matriz anonimo/externo/administrador/visualizador/colaborador/
  gestor, IDs que nao atravessam bibliotecas nem espacos, paginacao, token de
  dispositivo e a copia compartilhada intacta depois de o autor mandar o
  original privado para a lixeira.
- Limites conhecidos da segunda fatia (tratados na terceira, abaixo): copia
  duplicava bytes, sem cota por espaco, sem lixeira/restauracao, sem salvar
  copia na propria biblioteca, backup sem `data/spaces/`.

## Terceira fatia da etapa 1: armazenamento, lixeira e copia pessoal

- `core/fs_clone.py` testa de verdade o sistema de arquivos de `data/spaces/`
  (reflink via `FICLONE`, hard link) e le o tipo em `/proc/self/mountinfo` para
  o relatorio. `IRIS_SPACE_STORAGE=auto|reflink|hardlink|copy` (padrao `auto`:
  reflink quando possivel, senao copia). Uma escolha impossivel impede o
  servidor de iniciar. Por arquivo, uma origem em outro volume cai para copia.
  Verificado em btrfs: origem e copia com o mesmo endereco fisico e a flag
  `FIEMAP_EXTENT_SHARED`.
- `space.db` passa a esquema 2, com migracao em lugar da versao 1: metodo de
  armazenamento por item, `purged_at` e contador incremental de bytes.
- Cota por espaco (`IRIS_SPACE_QUOTA_BYTES`, padrao 10 TiB; 507 ao exceder),
  lixeira do espaco (`GET /trash`, `POST /trash/{item}/restore`) e expurgo apos
  `IRIS_SPACE_TRASH_DAYS` (padrao 30), feito quando alguem abre a galeria ou a
  lixeira do espaco. Bytes so sao liberados quando nenhum outro item os usa.
- `POST /items/{item}/save`: qualquer membro, inclusive visualizador, guarda
  uma copia na propria biblioteca, clonada do espaco e colocada na mesma fila
  de processamento de um envio do Android. Salvar de novo nao duplica.
- `GET /storage` informa uso, cota e prazo da lixeira aos membros.
- `./scripts/server.sh storage` mostra o sistema de arquivos detectado e o modo
  efetivo; `install` imprime o mesmo relatorio.
- `tests/test_disposable_recovery.py` agora inclui um espaco com item: restaura
  `space.db` pela API de backup do SQLite e os bytes, e confere hash, miniatura
  regenerada e uso.
- Ainda faltam nesta etapa: albuns e busca dentro do espaco, expurgo agendado
  (hoje depende de acesso ao espaco) e um comando de backup de producao.
