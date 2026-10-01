# Servidor privado Iris

Este guia instala o Iris como biblioteca privada no seu próprio servidor. Ele não
abre uma porta para a Internet: o Docker atende somente em `127.0.0.1`.

O Iris não escolhe como você alcança esse endereço de outros aparelhos, e não
depende de nenhuma rede em particular. Ele expõe HTTP em `127.0.0.1` e trata
todo cliente igual; quem publica esse endereço é uma camada à sua escolha —
Tailscale, ZeroTier, Nebula, um túnel WireGuard próprio, um proxy reverso com
TLS, ou exposição direta se você souber o que está fazendo. Os exemplos abaixo
usam Tailscale por ser o caminho mais curto para quem não quer administrar
certificado, mas nada no servidor sabe disso.

## Pré-requisitos

- Linux x86_64 com Docker Engine, Docker Compose v2, `git` e `curl`. Não é
  preciso Python, PyTorch nem CUDA no host: tudo vem na imagem
  `ghcr.io/miguellopesdel/iris`;
- opcional, para GPU NVIDIA: placa RTX 20 ou mais nova, driver >= 580 e o
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html);
- uma forma de alcançar `127.0.0.1:8501` do host a partir dos seus aparelhos
  (ver "Acesso remoto"); o guia usa Tailscale como exemplo, mas qualquer VPN,
  malha ou proxy reverso serve;
- disco local com espaço para mídia, banco, índices e cache dos modelos;
- um backup externo. RAID não substitui backup.

## Instalação

Em resumo, são quatro passos: instalar, criar a conta administradora
(["Ativar contas"](#ativar-contas)), escolher como seus aparelhos chegam ao
servidor (["Acesso remoto privado"](#acesso-remoto-privado)) e conferir o destino
dos backups (["Backup, restauração e atualização"](#backup-restauração-e-atualização)).

```bash
git clone https://github.com/MiguelLopesDel/Iris.git
cd Iris
./scripts/server.sh install
```

O instalador verifica Docker Compose, cria `.env` com o UID/GID do usuário atual,
prepara `data/` e `media/` com permissão `0700`, baixa a imagem publicada
(`ghcr.io/miguellopesdel/iris`) e espera o health check. Se a versão pedida em
`IRIS_VERSION` não tiver imagem publicada, ele a constrói a partir do checkout.
O container roda com o UID/GID do `.env`, então `data/` e `media/` continuam
pertencendo ao seu usuário.

**GPU NVIDIA:** o instalador detecta sozinho. Se a máquina tem o driver NVIDIA e
o Docker consegue usá-lo (NVIDIA Container Toolkit), ele grava
`COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml` no `.env` e todos os
comandos seguintes (`status`, `update`, `backup`…) usam a imagem `-cuda` com
acesso à GPU. Sem GPU, ou com GPU que o Docker não alcança, ele usa a imagem
CPU e explica o motivo. Usar ou não a GPU é então uma opção do administrador em
**Sistema → Instalação → Processamento**, que vale na hora, sem reinstalar
(`IRIS_GPU=off` no `.env` muda o padrão). `install --gpu` e `install --cpu`
forçam a escolha da imagem. Os modelos de IA (~10 GB) são baixados no primeiro
uso e ficam em volumes do Docker, preservados entre atualizações.

Uma instalação nova grava também um `COMPOSE_PROJECT_NAME` derivado do caminho
da pasta: duas instalações em pastas de mesmo nome (por exemplo um teste em
`/tmp/Iris` e a de uso em `~/Iris`) não comandam o mesmo container. Para configuração avançada, edite `.env` antes ou depois da instalação.

O mesmo `.env` define os limites por conta. Os padrões são deliberadamente altos:
32 GiB por arquivo, 10 TiB por biblioteca e 500 milhões de pixels por imagem.
O envio pela interface web usa o mesmo protocolo retomável do app (arquivo por
arquivo, em pedaços), então não tem limite de quantidade; o limite de 10.000
arquivos (`IRIS_MAX_UPLOAD_FILES`) vale só para o endpoint antigo `/api/import`. Ajuste-os ao espaço disponível no servidor; eles existem para
evitar que um upload acidental esgote disco, RAM ou CPU.

As variáveis `IRIS_MAX_SEARCH_TOP_K` e `IRIS_MAX_SEARCH_CANDIDATES` também
limitam consultas excepcionalmente grandes (os padrões são 1.000 e 20.000). Elas
protegem a CPU/RAM sem restringir a busca normal da galeria.

Use `./scripts/server.sh status` e `./scripts/server.sh logs` para acompanhar o serviço.

### Uploads do celular sem inferência de IA

Por padrão, o servidor recebe o original, verifica o hash, registra a mídia no
catálogo da conta e a deixa visível na galeria sem calcular embeddings, descrições
ou dados faciais. Miniaturas são geradas quando solicitadas pela interface. Isso
mantém o backup e a navegação básicos disponíveis em servidores modestos; busca
semântica e recursos que dependem de embeddings só funcionam para itens já
indexados.

`IRIS_SYNC_AI_PROCESSING=0` é o padrão. Para também indexar uploads no servidor,
ative `IRIS_SYNC_AI_PROCESSING=1`; essa opção só tem efeito quando
`IRIS_LOAD_MODEL=1`. A indexação de upload pode usar CPU/RAM e, quando habilitada,
extrai embeddings e dados de rostos. Reinicie o serviço após alterar essas opções:

```bash
docker compose up -d --force-recreate iris
```

### Armazenamento dos espaços compartilhados

Um item adicionado a um espaço compartilhado ganha uma cópia própria, para que
apagar a foto da biblioteca pessoal não quebre o espaço (e vice-versa). O modo
dessa cópia é escolhido em `IRIS_SPACE_STORAGE`:

| Valor | Efeito | Quando usar |
| --- | --- | --- |
| `auto` (padrão) | reflink se o sistema de arquivos de `data/` suportar; senão cópia comum | quase sempre |
| `reflink` | exige reflink; o servidor não inicia sem suporte | para garantir que nada seja duplicado |
| `hardlink` | não ocupa espaço em nenhum sistema de arquivos, mas os dois nomes são o mesmo arquivo: editar um no lugar altera o outro | só se você entende o risco |
| `copy` | sempre cópia byte a byte | discos que não suportam nada melhor, ou por preferência |

Reflink é uma cópia independente que compartilha os blocos no disco até que um
dos lados mude: em btrfs, XFS formatado com `reflink=1`, bcachefs e ZFS 2.2+ com
block cloning, compartilhar uma foto não ocupa espaço extra. Em ext4 o modo
`auto` usa cópia comum. O Iris não confia no nome do sistema de arquivos: ele
testa a operação de verdade em `data/spaces/`. Para ver o resultado:

```bash
./scripts/server.sh storage
```

O mesmo relatório aparece ao fim do `install` e no log de início do servidor.
Arquivos de uma biblioteca antiga fora de `data/` (outro disco) são copiados
normalmente, mesmo no modo `reflink`, porque reflink não atravessa sistemas de
arquivos. Mantenha `data/` inteiro no mesmo volume para aproveitar o recurso.

`IRIS_SPACE_QUOTA_BYTES` limita cada espaço (padrão 10 TiB; cada arquivo distinto
conta uma vez, inclusive o que está na lixeira). `IRIS_SPACE_TRASH_DAYS` (padrão
30) é o prazo em que um item removido do espaço pode ser restaurado; depois disso
o Iris libera os bytes.

As três opções também podem ser alteradas pela interface, por uma conta
administradora, em **Sistema → Instalação**. O valor salvo ali vale na hora, sem
reiniciar, e tem prioridade sobre o `.env`; cada campo mostra de onde vem o valor
atual (definido na interface, `.env` ou padrão) e permite voltar ao do instalador.
A tela só oferece reflink ou hard link se o disco os suportar, e o servidor testa
de novo antes de salvar. Se o disco mudar depois (por exemplo, `data/` movido
para ext4 com reflink escolhido na interface), o servidor inicia no modo
automático e a tela avisa o motivo; um `.env` inválido, ao contrário, impede o
início, porque é erro de instalação. Administrar essas opções não dá acesso às
fotos de nenhuma conta ou espaço.


## Ativar contas

O `install` termina mostrando um endereço e um **código de instalação** de uso único:

```
Finish setup in the browser: http://127.0.0.1:8501/setup
Installation code: K7QM-4XPA
```

Abra a página, digite o código e escolha usuário e senha do administrador. Quem
completa o setup vira administrador; o código prova que a pessoa consegue ler o
console ou a pasta `data/` do servidor, e não apenas alcançar a página. Ele também
aparece no log (`./scripts/server.sh logs`) e `./scripts/server.sh setup-code`
mostra de novo. Depois de 10 códigos errados em 10 minutos a página recusa novas
tentativas por um tempo. Assim que o administrador existe, o código é apagado, o
setup se fecha de vez, você entra na conta direto e o servidor passa a pedir login,
sem reiniciar. A conta administradora cria as demais pelo painel **Sistema**.

Se houver uma biblioteca Iris de antes das contas em `data/` e `media/`, a página
avisa e o setup a move para a conta do administrador (`data/users/1/`). Faça
backup de `data/` e `media/` antes.

Em servidores sem navegador, o terminal continua disponível; ele pede a senha e
faz a mesma migração:

```bash
./scripts/server.sh create-admin --username administrador --display-name "Seu nome"
```

### Trazer uma biblioteca antiga para uma conta já criada

Se o servidor já foi instalado e a biblioteca antiga está em outra máquina, copie
o catálogo (com os arquivos `-wal`, `.vec` e `.faiss` ao lado dele), a pasta
`data/library/` e a pasta `media/` para uma pasta dentro de `data/` do servidor.
Rodado na máquina antiga, a partir da pasta do projeto:

```bash
rsync -a --info=progress2 data/meme_compass_full_v1[._]* data/library media \
      usuario@servidor:~/Iris/data/import/
```

Depois, no servidor:

```bash
./scripts/server.sh attach-library --user seu-usuario --from data/import
```

O comando para o servidor, mostra quantos itens serão trazidos e pede
confirmação; a conta precisa estar vazia e usar o mesmo modelo de busca do
catálogo. A mídia já está no mesmo disco, então nada é copiado de novo: os
arquivos são movidos para a conta, com OCR, legendas, embeddings, álbuns e
pessoas como estavam. O catálogo vazio anterior da conta fica guardado em
`data/users/<id>/replaced-*` e, se algo falhar no meio, tudo volta ao lugar.
As miniaturas são geradas de novo conforme a galeria abre.

## Acesso remoto privado

O Iris usa a porta `8501` por padrão, mas ela é configurável no servidor. A porta
continua privada em `127.0.0.1`; isso não abre acesso pela rede local ou Internet.
Para trocar, use:

```bash
./scripts/server.sh port 8751
```

O comando atualiza `.env`, reinicia o Iris, verifica a saúde e mostra o endereço
resultante. Escolha uma porta livre entre 1024 e 65535. Não configure essa porta
pelo painel web: ela é uma decisão do host e um processo não pode trocar a própria
porta com segurança.

### Publicando o endereço para os seus aparelhos

O que o Iris exige é apenas isto: **algo tem que levar os seus aparelhos até
`127.0.0.1:8501` do host, e esse algo é responsável pela identidade e pelo TLS.**
O servidor não tem preferência. Três caminhos comuns:

**Malha privada (Tailscale, ZeroTier, Nebula).** O mais curto, porque a rede já
autentica o aparelho e cuida do certificado. Com Tailscale:

```bash
sudo tailscale serve --bg http://127.0.0.1:8501
tailscale serve status
```

Use o endereço `https://<servidor>.<tailnet>.ts.net` que ele mostrar, no
navegador ou como URL do servidor no app Android. Não use o IP da malha seguido de
`:8501`: o Docker atende deliberadamente só no host local. Em ZeroTier ou Nebula,
onde não há equivalente do `serve`, ligue o Docker à interface da malha em vez de
`127.0.0.1` — ali o alcance já está limitado a quem entrou na rede.

**Túnel próprio (WireGuard, SSH).** Encaminhe uma porta local do aparelho para
`127.0.0.1:8501` do servidor. Nada muda no Iris.

**Proxy reverso com TLS (Caddy, nginx, Traefik).** Necessário se você for expor
na Internet. Aí a autenticação de contas do Iris passa a ser a única barreira,
então trate como serviço público: certificado válido, rate limiting, e um plano
para quando aparecer tráfego indesejado. Não é o modo para o qual este guia foi
escrito.

Seja qual for o caminho, **não** troque o mapeamento do Docker para `0.0.0.0`
sem antes decidir conscientemente por exposição pública, TLS e recuperação de
incidentes — isso abre a porta para toda a rede local de uma vez.

## Backup, restauração e atualização

O Iris faz backup da instalação inteira sozinho, todo dia às 03:00 no fuso do
servidor (o `install` detecta o fuso do host). O destino é `IRIS_BACKUP_DIR` no
`.env`, por padrão `./backups`. Aponte para outro disco sempre que possível:
no mesmo disco, o backup protege contra exclusões por engano, mas não contra a
perda do disco, e o painel avisa disso.

Tudo pode ser ajustado por uma conta administradora em **Sistema → Backup**:
ligar ou desligar, horário, fuso e retenção. A mesma tela faz um backup na hora
(com a opção de guardá-lo para sempre) e lista os backups existentes com a data,
a versão do Iris e o commit que os gravou, o tamanho e a regra de retenção.

Pela linha de comando:

```bash
./scripts/server.sh backup              # agora, com a mesma retenção do agendado
./scripts/server.sh backup --pin        # agora, e nunca apagar este
./scripts/server.sh backups             # lista com versão e retenção
./scripts/server.sh verify-backup backups/iris-backup-20260923T060000Z
```

Um backup contém contas e dispositivos (`users.db`), o segredo das sessões
(`secret_key`), a biblioteca de cada conta, os espaços compartilhados e os
originais de `data/` e `media/`. Os bancos SQLite são copiados pela API de
backup do SQLite, então o Iris continua funcionando durante o backup. Fica de
fora só o que ele reconstrói sozinho: índices FAISS, vetores, miniaturas e
envios pela metade.

O Iris só publica o backup depois de conferir os arquivos, a integridade dos
bancos e os originais referenciados pelos catálogos. Se algum original estiver
fora das raízes configuradas, estiver ausente, ou se bancos/arquivos mudarem
durante a captura, a execução aparece como **falha** no painel e não aciona a
retenção. Corrija o caminho ou tente novamente quando os envios terminarem.
Se `IRIS_SECRET_KEY` vier do ambiente, o backup guarda o valor efetivo como
`data/secret_key`; após restaurar, confira se o `.env` não o substitui por
outro valor. Proteja o destino do backup como protegeria as fotos e senhas.
O `.env` e os arquivos Compose do host não entram no snapshot; guarde uma cópia
separada dessas configurações para reconstruir a instalação após perda do host.

Cada backup é uma pasta completa, que dá para abrir sem o Iris, com um
`manifest.json` que registra o hash de cada arquivo e a versão do Iris que o
gravou. Uma foto que não mudou desde o backup anterior vira um hard link para
ele: ocupa o disco uma vez só. Em discos sem hard link (exFAT, FAT) o arquivo é
copiado. Um backup interrompido fica como `.incomplete-*` e nunca é usado.

### Retenção

Depois de cada backup, o Iris mantém o mais recente de cada um dos últimos 7
dias, 4 semanas e 6 meses (`IRIS_BACKUP_KEEP_DAILY`, `_WEEKLY`, `_MONTHLY`) e
apaga os demais. Zero nos três guarda todos. A limpeza só alcança backups
feitos sob essa política: os marcados para guardar para sempre e os feitos
antes de a política existir nunca são apagados. Apagar um backup não afeta os
outros, mesmo que compartilhem fotos por hard link.

Para restaurar:

```bash
./scripts/server.sh restore /destino/iris-backup-20260923T140000Z
```

A restauração confere todos os hashes antes de mexer em qualquer coisa, pede
confirmação, para o container, monta a cópia ao lado da atual e só então troca
os diretórios. O estado anterior não é apagado: fica em
`data.before-restore-*` e `media.before-restore-*` até você decidir. Índices e
miniaturas são reconstruídos conforme o uso. `verify-backup` e `restore` rodam no
host com `python3`, sem as dependências do Iris. Depois da troca, o serviço é
recriado para montar os diretórios restaurados, em vez de reutilizar os mounts
dos diretórios anteriores.

Os backups antigos não são apagados automaticamente; remova-os quando quiser.

```bash
./scripts/server.sh update
./scripts/server.sh status
```

Com `IRIS_BACKUP_DIR` definido, o update faz um backup antes de atualizar.

O servidor roda sempre um **release**, nunca a `main`. Releases são as tags
`vX.Y.Z`: a `main` é onde as mudanças são integradas, e só o que recebe tag chega
a um servidor. O `install` e o `update` colocam o checkout na tag do release e usam
a imagem dessa mesma versão, de modo que scripts, `docker-compose.yml` e imagem
nunca ficam de versões diferentes. Com `IRIS_VERSION=latest` (o padrão) o alvo é
a tag mais nova; para controlar quando atualizar, fixe uma versão (por exemplo
`IRIS_VERSION=0.4.0`). O update exige uma árvore Git limpa e baixa a imagem
**antes** de trocar o checkout: se o release acabou de receber a tag e a imagem
ainda está sendo publicada, nada muda e ele pede para tentar de novo em alguns
minutos. Para voltar,
fixe a versão anterior em `IRIS_VERSION`, execute `./scripts/server.sh update`
e, se o esquema dos dados tiver mudado, restaure o backup feito antes do update.

O Iris mantém fotos em texto claro no servidor para gerar busca, pessoas e
duplicatas. Contas isolam pessoas entre si, mas quem controla o host/Docker pode
tecnicamente acessar os originais. Criptografia ponta a ponta não faz parte deste
modo de servidor.
