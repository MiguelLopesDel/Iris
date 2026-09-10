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

- Linux com Docker Engine e Docker Compose v2;
- uma forma de alcançar `127.0.0.1:8501` do host a partir dos seus aparelhos
  (ver "Acesso remoto"); o guia usa Tailscale como exemplo, mas qualquer VPN,
  malha ou proxy reverso serve;
- disco local com espaço para mídia, banco, índices e cache dos modelos;
- um backup externo. RAID não substitui backup.

## Instalação

```bash
git clone https://github.com/MiguelLopesDel/Iris.git
cd Iris
./scripts/server.sh install
```

O instalador verifica Docker Compose, cria `.env` com o UID/GID do usuário atual,
prepara `data/` e `media/` com permissão `0700`, constrói o container e espera o
health check. Para configuração avançada, edite `.env` antes ou depois da instalação.

O mesmo `.env` define os limites por conta. Os padrões são deliberadamente altos:
32 GiB por arquivo, 10.000 arquivos por envio, 10 TiB por biblioteca e 500 milhões
de pixels por imagem. Ajuste-os ao espaço disponível no servidor; eles existem para
evitar que um upload acidental esgote disco, RAM ou CPU.

As variáveis `IRIS_MAX_SEARCH_TOP_K` e `IRIS_MAX_SEARCH_CANDIDATES` também
limitam consultas excepcionalmente grandes (os padrões são 1.000 e 20.000). Elas
protegem a CPU/RAM sem restringir a busca normal da galeria.

Use `./scripts/server.sh status` e `./scripts/server.sh logs` para acompanhar o serviço.

## Ativar contas

Em uma instalação nova, crie a primeira conta — o comando cria uma biblioteca privada
vazia. Para migrar uma biblioteca Iris já existente, pare o container e faça backup de
`data/` e `media/`; o mesmo comando detecta e move o catálogo existente:

```bash
./scripts/server.sh create-admin --username administrador --display-name "Seu nome"
```

O bootstrap move a biblioteca antiga para `data/users/1/` e, no próximo início,
ativa a tela de login. A conta administradora cria as demais pelo painel **Sistema**.

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

Use o endereço `.ts.net` que ele mostrar. Não use o IP da malha seguido de
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

## Atualização e recuperação

Antes de atualizar, faça uma cópia consistente de `data/` e de `media/` para outro
disco. Preserve `data/secret_key` ou defina `IRIS_SECRET_KEY` estável: perder ambos
encerra todas as sessões e pode exigir novo login.

```bash
./scripts/server.sh update
./scripts/server.sh status
```

O update exige uma árvore Git limpa e usa `git pull --ff-only`, evitando merges
surpresa. Antes de atualizar, guarde o commit atual (`git rev-parse HEAD`) e uma cópia
de `data/` e `media/`; se for necessário voltar, retorne ao commit guardado e execute
`docker compose up -d --build` novamente.

O Iris mantém fotos em texto claro no servidor para gerar busca, pessoas e
duplicatas. Contas isolam pessoas entre si, mas quem controla o host/Docker pode
tecnicamente acessar os originais. Criptografia ponta a ponta não faz parte deste
modo de servidor.
