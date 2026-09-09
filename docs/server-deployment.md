# Servidor privado Iris

Este guia instala o Iris como biblioteca privada no seu próprio servidor. Ele não
abre uma porta para a Internet: o Docker atende somente em `127.0.0.1` e o Tailscale
Serve é a única camada que publica o serviço para dispositivos autorizados da tailnet.

## Pré-requisitos

- Linux com Docker Engine e Docker Compose v2;
- Tailscale instalado e conectado na mesma tailnet de notebook e celular;
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

No host, depois de configurar HTTPS na tailnet quando o Tailscale solicitar:

```bash
tailscale serve 8501
tailscale serve status
```

Abra o endereço `.ts.net` mostrado no notebook ou celular que esteja na mesma
tailnet. Não use Tailscale Funnel e não mude o mapeamento Docker para `0.0.0.0`
sem antes projetar exposição pública, TLS, rate limiting e recuperação de incidentes.

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
