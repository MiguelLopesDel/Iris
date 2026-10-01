# Conectividade: como os aparelhos chegam ao servidor

Este guia descreve como o app Android e o navegador alcançam um servidor Iris, que
garantias cada forma de acesso oferece e como o app decide em quem confiar. Ele vale
para qualquer instalação: domínio público, malha privada, proxy reverso com
certificado próprio ou rede local.

## O modelo

O servidor Iris atende HTTP em `127.0.0.1` (porta `8501` por padrão) e não abre
nenhuma porta na rede por conta própria. Entre os aparelhos e ele sempre existe
alguma coisa que transporta o tráfego e, quase sempre, cuida do TLS:

```
aparelho ──(HTTPS ou rede privada)──► proxy / túnel / malha ──(HTTP local)──► Iris :8501
```

Quem escolhe essa peça é o administrador. O Iris não depende de nenhuma em
particular, e o app se adapta a cada uma pelas opções de confiança descritas abaixo.

## Cenários e o que cada um exige

| Cenário | Certificado apresentado ao aparelho | O que configurar no app |
|---|---|---|
| Domínio público com proxy reverso (Let's Encrypt, ZeroSSL, Cloudflare) | emitido por autoridade pública | nada |
| Malha privada com HTTPS próprio (por exemplo `tailscale serve`, que usa nomes `*.ts.net` com certificado público) | emitido por autoridade pública | nada |
| Proxy reverso com autoridade privada (CA interna do proxy, step-ca, CA da empresa) | emitido por uma CA que só os seus aparelhos conhecem | confiar nessa CA |
| Certificado autoassinado | o próprio certificado, sem autoridade | confiar nesse certificado |
| HTTP dentro de uma rede já protegida (malha WireGuard, VPN, rede local) | nenhum | permitir HTTP para esse servidor |

O certificado precisa **nomear o endereço** que o aparelho usa. Um certificado
emitido para `iris.exemplo` não serve para `https://192.168.1.10`, nem o contrário.
Nenhuma opção de confiança contorna isso: se o servidor é acessado por IP, o
certificado precisa incluir esse IP (`subjectAltName = IP:...`).

## Confiança no app

O app guarda uma política **por servidor**, identificada por host e porta. Nada é
global: confiar numa CA para um servidor não faz o app confiar nela para outro.

### Padrão: só autoridades públicas

Sem nenhuma configuração, o app aceita apenas certificados de autoridades públicas,
como o Android faz por padrão, e recusa HTTP para endereços novos. Quando a conexão
falha por causa disso, o app explica o motivo e oferece as opções abaixo.

### Certificado de uma autoridade privada ou autoassinado

Ao encontrar um certificado que não reconhece, o app mostra o certificado do
servidor e quem o emitiu, cada um com a **impressão digital SHA-256**. Antes de
aceitar, compare com a do servidor. Por exemplo:

```bash
# Impressão digital de um certificado em arquivo (CA raiz, intermediária ou autoassinado)
openssl x509 -in root.crt -noout -fingerprint -sha256

# O que o servidor está apresentando agora
openssl s_client -connect iris.exemplo:443 -servername iris.exemplo </dev/null 2>/dev/null \
  | openssl x509 -noout -fingerprint -sha256
```

São três formas de confiar, cada uma para um caso:

1. **Usar autoridades instaladas no aparelho.** O app passa a aceitar, para esse
   servidor, também as CAs que o dono do aparelho instalou (Configurações do Android
   → Segurança → Certificados). Por padrão o Android não deixa apps usarem essas CAs;
   esta opção libera isso só para o servidor escolhido. O app avisa quando a CA do
   servidor já está instalada.

2. **Importar o certificado da autoridade.** Você escolhe o arquivo da CA raiz
   (`.crt`, `.pem` ou `.der`), e o app passa a aceitar, para esse servidor, **apenas**
   cadeias que levam a ela. O app recusa o arquivo se ele não for a autoridade que
   emitiu o certificado atual do servidor. Esta é a opção recomendada para CAs
   privadas, porque não depende do armazenamento de certificados do Android.

3. **Confiar neste certificado.** Só aparece quando o topo da cadeia apresentada é
   autoassinado: um certificado autoassinado ou um servidor que envia a própria CA
   raiz. O app fixa esse certificado para o servidor.

O app sempre fixa a **autoridade**, nunca o certificado final. CAs privadas costumam
emitir certificados finais e intermediários de vida curta (horas ou dias) e não
enviam a raiz no handshake. Fixar o que chega pela rede quebraria a cada renovação.
Por isso, quando o topo da cadeia é um intermediário, a opção 3 não aparece: use a
1 ou a 2.

Onde encontrar a CA raiz depende do proxy. Alguns exemplos: proxies com CA interna
costumam gravá-la no próprio diretório de dados (no Caddy, `pki/authorities/local/root.crt`
dentro do diretório de dados dele), o step-ca a mostra com `step ca root`, e uma CA
corporativa é distribuída pela equipe que a administra.

### HTTP sem criptografia

Para usar `http://`, o app pede uma confirmação por servidor. O aviso muda conforme
o endereço:

- **Endereço de rede privada:** loopback, `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `100.64.0.0/10` (a faixa usada por malhas como Tailscale),
  link-local, IPv6 local (`fc00::/7`, `fe80::/10`) e nomes `.local`, `.lan`,
  `.home.arpa` e `.internal`. O aviso lembra que só faz sentido numa rede de
  confiança ou que já criptografa o tráfego.
- **Qualquer outro endereço:** o aviso diz que o tráfego pode passar por redes de
  terceiros e recomenda HTTPS.

A classificação é feita pelo endereço digitado, sem consultar DNS.

HTTP no app não basta para o navegador: o servidor só envia o cookie de sessão por
HTTPS enquanto `IRIS_SESSION_HTTPS_ONLY=true`. Quem decide servir a interface web
por HTTP numa rede privada precisa desligar essa opção no `.env` do servidor.

Servidores que já eram usados por HTTP antes de esta confirmação existir continuam
funcionando sem perguntar, sincronização em segundo plano incluída. Só endereços
configurados depois disso pedem a confirmação.

### Ver, revogar e restaurar

Em **Configurações do servidor → Segurança da conexão**, o app mostra a política do
servidor atual: modo de confiança, impressões digitais fixadas e se HTTP está
permitido. **Restaurar o padrão para este servidor** volta a aceitar apenas
autoridades públicas e recusar HTTP. A mudança vale na hora: o app descarta as
conexões abertas e as sessões TLS em cache, de modo que nenhuma conexão aceita pela
política antiga sobrevive.

## Diagnóstico

| Mensagem no app | O que significa | O que fazer |
|---|---|---|
| "Conexão sem criptografia" | o endereço é `http://` e ainda não foi autorizado | permitir, se a rede for de confiança, ou usar HTTPS |
| "Certificado não reconhecido" | a cadeia não leva a uma autoridade confiável para este servidor | conferir a impressão digital e escolher uma das três opções |
| "não foi emitido para *endereço*" | o certificado é confiável, mas não nomeia o endereço usado | usar o nome que consta no certificado ou emitir um que inclua o endereço |
| "respondeu, mas não com HTTPS" | algo atende nessa porta, mas fala HTTP | conferir a porta ou usar `http://` |
| "Failed to connect" / tempo esgotado | nada atende nesse endereço e porta | conferir se o proxy, o túnel ou a malha estão no ar; o Iris sozinho não atende na rede |

## Próximas etapas

- **Exposição configurável no servidor:** escolher em qual endereço o servidor
  escuta (por exemplo, só na interface de uma malha privada) e informar quais
  proxies são confiáveis para cabeçalhos `X-Forwarded-*`, com o cookie de sessão
  seguindo o modo escolhido.
- **Pareamento por QR code:** a interface web gera um código com os endereços do
  servidor, um identificador da instância e, opcionalmente, a CA a confiar. O app
  lê o código e já sai configurado. Com vários endereços (rede local, malha,
  público), o app usa o primeiro que responder como o mesmo servidor.
