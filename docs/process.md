# Processo de desenvolvimento e de lançamento

Como uma mudança sai de uma ideia e chega ao servidor e ao celular de quem usa o
Iris: que ambientes existem, o que é cada etapa (commit, pull request, `main`,
tag), que barreira cada uma precisa passar e onde entra cada teste.

> Itens marcados **(a construir)** descrevem o processo-alvo e ainda não têm
> ferramenta pronta. Até existirem, siga o restante e faça esses passos à mão.

## Ambientes

Três ambientes, cada um com um dono e um tipo de dado. Nenhum deles se mistura com
outro.

| Ambiente | Servidor | Celular | Dados | O que roda |
|---|---|---|---|---|
| **Desenvolvimento** | local (`scripts/dev.py`) ou CI | emulador | descartáveis, sintéticos | qualquer branch |
| **Homologação** | uma segunda instalação no servidor de produção, com porta e pasta de dados próprias **(a construir)** | app `lab` (`com.iris.app.lab`), instalado ao lado do app normal | cópia reflink da produção, renovável | a `main` |
| **Produção** | a instalação de uso real | o app normal (`com.iris.app`) | os dados reais | só releases com tag |

- **Desenvolvimento** é onde se escreve e se quebra código. Nunca aponta para
  dados reais nem para o servidor de produção.
- **Homologação** responde "como a próxima versão se comporta de verdade?". Usa uma
  cópia reflink dos dados de produção: sai em segundos, não ocupa espaço até algo
  mudar e mostra o comportamento com a biblioteca real, sem risco para ela. O app
  `lab` tem pacote, preferências, credenciais e fila próprios, então testar nele
  nunca mexe no app de uso diário.
- **Produção** só muda por decisão explícita: um release com tag, aplicado com
  `./scripts/server.sh update` e com o APK daquele release.

Testes automatizados de dispositivo (instrumentação, Maestro, benchmarks) rodam só
em emulador ou no app `lab`; nunca no app nem nos dados de produção.

## Etapas e barreiras

```
commit ──► pull request ──► main ──► homologação ──► tag ──► produção
         (CI verde,        (candidata)  (checklist     (release)  (update)
          revisão)                       no uso real)
```

### Commit

**É** uma mudança lógica, com um porquê. Não precisa estar pronta para o usuário;
precisa estar correta e explicada.

- Mensagem em inglês no formato [Conventional Commits](https://www.conventionalcommits.org/)
  (`fix(sync): resume uploads after a network change`), com o corpo explicando o
  porquê quando ele não for óbvio. Sem rodapés de atribuição (`Co-Authored-By` e
  afins).
- Um assunto por commit: não misture comportamento, refatoração, dependências e
  implantação.
- **Barreira, antes do commit:** lint limpo e os testes da área mexida passando.
  Isso leva segundos; a suíte completa é trabalho da CI (ver
  [Onde entra cada teste](#onde-entra-cada-teste)). Todo commit da `main` precisa
  compilar e passar nos testes, para que qualquer ponto da história funcione e o
  `git bisect` sirva.

### Pull request

**É** uma funcionalidade ou correção completa e revisável, numa branch curta a
partir da `main` atualizada, com prefixo do tipo (`feat/`, `fix/`, `docs/`,
`chore/`, `build/`, `ci/`, `refactor/`).

- Contém: o código, testes do comportamento novo (todo bug corrigido ganha um teste
  que falha sem a correção), documentação atualizada e, se mexe em tela, evidência
  (screenshots do emulador).
- A descrição diz o que muda em relação à `main` e por quê, como foi testado e o que
  exige na implantação (migração, rebuild, passo manual). Ela descreve o diff, não
  o caminho de quem escreveu.
- **Barreira, antes do merge:** CI verde (suíte Python, teste no navegador, build e
  testes JVM do Android, smoke da instalação limpa) e revisão do diff. Projeto solo:
  a revisão é a sua, mas é obrigatória.
- Depois do merge, a branch é apagada.

### `main`

**É** a candidata ao próximo release: sempre instalável, mas ainda não validada no
uso real. Um merge na `main` não muda nada na produção.

- **Barreira automática:** cada merge publica a imagem `:main` e o APK `lab`
  **(a construir)**. A homologação recebe essa versão com
  `./scripts/staging.sh update`, rodado no servidor, dentro do checkout da
  homologação: um comando manual, você decide quando (ver
  [O comando da homologação](#o-comando-da-homologação)).

### Homologação

**É** onde se descobre se a aplicação se comporta como esperado: servidor de
homologação mais app `lab` no celular, com a cópia dos dados reais.

- **Barreira para criar a tag:** a checklist de homologação (abaixo) passou, e não
  houve regressão visível em pelo menos um dia de uso.

### Tag (release)

**É** a decisão "isto vai para produção". A tag `vX.Y.Z` vai num commit da `main`
cujo `version` em `pyproject.toml` é `X.Y.Z`. Para isso, um PR
`chore(release): bump version to X.Y.Z` antes da tag.

- Versão ([SemVer](https://semver.org/)):
  - **correção** sobe o último número (`0.6.0 → 0.6.1`);
  - **funcionalidade compatível** sobe o do meio (`0.6.x → 0.7.0`);
  - **mudança incompatível** sobe o primeiro: formato de dados que não volta atrás,
    protocolo do app que versões antigas não entendem.
- A tag dispara o workflow de release, que roda o smoke e publica as imagens
  `X.Y.Z`, `X.Y` e `latest` (CPU e `-cuda`). **(A construir:)** o APK de release
  assinado com a chave fixa do projeto, anexado ao GitHub Release com as notas, e o
  app com `versionName` igual à versão do servidor.
- Pré-lançamentos (`v0.7.0-rc.1`) nunca são escolhidos automaticamente; só rodam
  quando fixados com `IRIS_VERSION`.

### Produção

- Servidor: `./scripts/server.sh update`. Ele faz backup, coloca o checkout na tag
  do release e sobe a imagem dessa mesma versão.
- Celular: o APK do release, instalado por cima do anterior (mesma chave de
  assinatura, então login e dados ficam).
- **Conferência depois de atualizar** (dois minutos):
  - versão certa: `docker exec iris-iris-1 grep '^version' /app/pyproject.toml`;
  - `./scripts/server.sh status` saudável;
  - um backup concluído;
  - o app abre e sincroniza.

Nunca rode `docker compose up` direto na produção: sem o `server.sh`, o compose
pode construir uma imagem local com a etiqueta de um release que não existe.

### Correção urgente

Branch a partir da tag em produção, PR para a `main`, tag de correção
(`vX.Y.(Z+1)`), sem esperar o que mais estiver na `main`.

## Fluxo de trabalho: descobertas, PRs e espera

Corrigir uma coisa revela outra; é sinal de que se está testando de verdade. O
fluxo só fica lento ou bagunçado pelo que se faz com cada descoberta e pelo
tempo gasto esperando.

### Descobrir não é fazer

O que aparece no meio de outro trabalho vira primeiro **uma anotação**: um issue
no GitHub com o sintoma e como reproduzir. Depois, uma pergunta só: **bloqueia o
que estou fazendo, ou é grave?**

- **Não:** fica no issue, e o trabalho atual continua.
- **Sim** (perda de dados, falha de segurança, algo que impede o trabalho
  atual): vira o próximo trabalho, num PR **próprio**, nunca misturado ao PR em
  andamento.

### Pare de começar, comece a terminar

- **No máximo 3 PRs abertos.** Bateu o limite, não se abre outro: revisa-se e
  faz-se o merge de um dos que existem. PR aberto é trabalho que ainda não
  entregou nada, e cada um deixa os próximos mais difíceis.
- **PRs pequenos, merge assim que validados:** CI verde, diff revisado e
  homologação quando fizer sentido. Não se espera juntar vários.
- **Branch sempre a partir da `main` atualizada.** Empilhar uma branch sobre
  outra só quando mexem no mesmo código e não há como evitar; nesse caso, o
  merge do PR de baixo vem antes de começar o de cima.
- **Primeiro deixe a mudança fácil, depois faça a mudança fácil.** Se, para
  fazer algo, é preciso mexer em outro lugar, essa preparação é um PR separado e
  anterior.
- **Funcionalidade grande entra em partes**, desligada por uma configuração até
  estar completa, em vez de viver meses numa branch.

### Não esperar parado

- Enquanto um PR está na CI ou em revisão, o próximo trabalho independente já
  começa, numa branch nova a partir da `main`. Ficam várias coisas em
  andamento, cada uma num estágio: uma sendo escrita, uma em CI ou revisão,
  uma esperando merge.
- A revisão é assíncrona: o que ela encontra entra como commit pequeno no PR,
  sem refazer o ciclo inteiro.
- **Merge automático** do GitHub: aprovado, o merge acontece sozinho quando a CI
  fica verde **(a construir: ativar no repositório)**.
- **Testar junto, fazer merge separado.** Para testar vários PRs abertos ao
  mesmo tempo (no servidor de homologação e no celular), monta-se uma branch de
  integração só para teste, que junta os PRs escolhidos. Ela nunca vai para o
  GitHub nem para a `main`; os PRs continuam independentes e entram um a um.
  No servidor, `./scripts/staging.sh update --pr N --pr M` monta essa versão. O
  APK combinado para o celular ainda se gera à mão **(a construir)**.

### O comando da homologação

`scripts/staging.sh` roda no servidor, dentro do checkout da homologação:

```bash
./scripts/staging.sh status                       # o que está rodando, montagens, versão anterior
./scripts/staging.sh update                       # a main
./scripts/staging.sh update --pr 25 --pr 26       # a main mais esses PRs, só para teste
./scripts/staging.sh update --ref <commit>        # um commit específico
./scripts/staging.sh update --pr 25 --dry-run     # só verifica e mostra o plano
./scripts/staging.sh rollback                     # volta para a versão anterior
```

Ele só age sobre a própria homologação. Antes de mudar qualquer coisa, recusa se:

- o `COMPOSE_PROJECT_NAME` do `.env` não contém `staging`; vazio, ele seria o
  da produção;
- a pasta de dados, mídia ou backup está montada num contêiner de outra
  instalação, ou a porta está publicada por outra;
- `/app/data` e `/app/media` não estão montados em pastas do servidor;
- há arquivos versionados alterados no checkout.

Ao agir, só usa `build` e `up` do serviço `iris` do próprio projeto: nunca
`down`, `rm`, `prune` nem volumes, nunca apaga pastas nem descarta trabalho do
git. A versão com PRs é um commit de merge local, nunca enviado. Um PR que não
entra limpo ou um build que falha devolvem o checkout ao commit anterior, sem
tocar no contêiner em execução. A versão anterior fica guardada em
`refs/staging/previous`, para o `rollback`.

## Onde entra cada teste

| Momento | O quê | Quem |
|---|---|---|
| antes do commit | lint e testes rápidos da área mexida (segundos, não a suíte inteira) | quem desenvolve |
| no PR | suíte completa do servidor, navegador, Android JVM e build, smoke da instalação | CI (GitHub Actions) |
| antes do push de um PR do app | testes instrumentados no emulador | quem desenvolve, enquanto não estão na CI |
| depois do merge, antes da tag | comportamento real: homologação e app `lab` | quem lança, pela checklist |
| depois da tag | conferência da produção | quem administra |

Teste na camada certa:

- **lógica pura** (parsing, ranqueamento, regras): teste unitário rápido, sem rede;
- **integração**: o servidor de verdade com `TestClient`/ASGI, `IRIS_LOAD_MODEL=0` e
  banco temporário;
- **rede e TLS**: servidores locais reais (MockWebServer, certificados gerados);
- **comportamento de tela**: emulador ou navegador real (Playwright);
- o que depende de serviço externo (busca reversa, LLM) fica atrás de uma costura
  injetável e é validado ao vivo, não na CI.

A CI ainda **compila, mas não executa** os testes instrumentados do Android;
até estarem nela **(a construir)**, quem abre um PR do app os roda no emulador
antes do push. A suíte do servidor também pode ficar mais rápida na CI rodando
em paralelo (`pytest -n auto`) **(a construir)**.

Um build que passa não substitui um teste de comportamento. Quando uma verificação
não pôde ser feita (sem dispositivo, sem rede), isso é dito no PR.

## Checklist de homologação

Antes de criar a tag, na homologação:

- [ ] a atualização a partir do release anterior funciona;
- [ ] o backup conclui (e a restauração de um backup recente, quando o release mexe
      em dados ou no backup);
- [ ] login, galeria e busca funcionam com a biblioteca real;
- [ ] o app `lab` conecta, sincroniza fotos novas e mostra o status certo;
- [ ] pareamento e confiança de certificado, se o release mexe em conectividade;
- [ ] o que o próprio release mudou foi exercitado de ponta a ponta.

## Definição de pronto

Uma mudança está pronta para o PR quando:

- [ ] faz uma coisa só, em commits coerentes;
- [ ] tem teste (de regressão para bug, de comportamento para funcionalidade), e a
      suíte passa;
- [ ] `ruff check` está limpo e o projeto compila;
- [ ] o caminho de quem instala do zero não quebrou;
- [ ] não há segredos, bancos, índices nem mídia no diff;
- [ ] a documentação reflete o comportamento e os comandos novos.

## Invariantes que nenhuma mudança pode quebrar

- Toda leitura e escrita de biblioteca é restrita à conta autenticada; sincronização
  de dispositivo exige também uma sessão de dispositivo válida. IDs de conta e de
  dispositivo e caminhos vindos do cliente nunca são autoridade.
- Originais são preservados. Aceitar o envio, registrar no catálogo, gerar
  miniaturas, processar IA e aplicar mudanças no cliente são estados separados; um
  item só é dado como salvo quando o servidor o aceitou de forma durável. Repetir
  uma operação é idempotente ou retomável.
- Trabalho caro de IA nunca fica no caminho do envio: o servidor padrão recebe e
  mostra fotos sem modelos e sem GPU.
- Senhas, tokens, bytes de mídia e caminhos absolutos privados nunca vão para logs.
  `.env`, mídia, bancos e configurações pessoais nunca vão para commits.
- Apagar na biblioteca usa a lixeira recuperável do produto; nada de remoção direta
  de arquivo a partir da interface.
- Migrações de banco são aditivas; abrir um banco novo deixa o sistema utilizável.
  Mudanças de esquema trazem migração, backup, compatibilidade e caminho de volta.
- Segredos só por variável de ambiente.

## Anti-padrões

- Misturar refatoração e funcionalidade no mesmo commit.
- `except:` sem tipo, ou engolir um erro em silêncio.
- Migração destrutiva, ou assumir que o banco já tem as tabelas.
- Testar rede ou serviço externo real na CI em vez de usar uma costura.
- Deixar dívida de lint para depois.
- Testar na produção o que deveria ter passado pela homologação.
- Corrigir no PR atual um problema que se descobriu no caminho e que não tem a
  ver com ele.
- Deixar PRs abertos se acumularem, ou empilhar branches sem necessidade.
- Rodar localmente, antes de cada commit, a suíte inteira que a CI já roda.

Lições técnicas aprendidas em campo (memória, caches, ranqueamento) estão em
[engineering-lessons.md](engineering-lessons.md).
