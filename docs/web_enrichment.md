# Enriquecimento Web de Imagens

Pipeline que identifica externamente imagens já indexadas no Iris usando busca
reversa de imagem (Google Lens via SerpApi) e converte os resultados em
sugestões de metadados (personagem, obra, estilo, arquétipo, tags, resumo).

Código principal: `core/web_enrichment.py`. Endpoints: `server.py`
(seção `# ── Web enrichment ──`). UI: `static/app.js` / `templates/index.html`.

## Visão geral do fluxo

```
imagem local
   │
   ▼  (1) publica temporariamente numa URL pública
S3TemporaryImagePublisher ──► URL https://… (bucket S3/R2/MinIO)
   │
   ▼  (2) busca reversa por URL
SerpApiLensProvider ──► Google Lens (engine=google_lens) ──► JSON
   │
   ▼  (3) normaliza resultados em fontes
normalize_serpapi_sources() ──► list[WebSource]
   │
   ▼  (4) destila em sugestão estruturada
HybridDistiller (LLM opcional) → HeuristicDistiller (fallback)
   │
   ▼  (5) grava como sugestão pendente para revisão humana
web_enrichment_suggestions / web_enrichment_sources
   │
   ▼  (6) usuário aplica/rejeita na UI
apply_suggestion() escreve em memes + concepts/concept_media
```

Por que essas etapas: a SerpApi (e o próprio Google Lens) só aceita imagem por
**URL pública**, não por upload de arquivo local. Por isso a etapa (1) sobe a
imagem para um bucket S3 antes de pesquisar.

## Componentes

### 1. `S3TemporaryImagePublisher`
Faz upload da imagem local para um bucket compatível com S3 (AWS S3,
Cloudflare R2, Backblaze B2, MinIO…) e devolve a URL pública. Assina a request
com AWS Signature V4 manualmente (sem boto3, só `urllib`). A chave do objeto é
`{prefix}{uuid}.{ext}`, então cada execução gera um arquivo novo.

Configuração via variáveis de ambiente (`S3Config.from_env`):

| Variável | Obrigatória | Descrição |
|---|---|---|
| `IRIS_S3_ENDPOINT_URL` | sim | Endpoint do provedor (ex.: `https://<acct>.r2.cloudflarestorage.com`) |
| `IRIS_S3_BUCKET` | sim | Nome do bucket |
| `IRIS_S3_ACCESS_KEY_ID` | sim | Access key |
| `IRIS_S3_SECRET_ACCESS_KEY` | sim | Secret key |
| `IRIS_S3_PUBLIC_BASE_URL` | sim | Base pública para servir o objeto (ex.: domínio do R2/CDN) |
| `IRIS_S3_PREFIX` | não | Prefixo das chaves (padrão `iris-enrichment/`) |
| `IRIS_S3_REGION` | não | Região (padrão `auto`) |

> ⚠️ A imagem **sai do seu PC** e fica publicamente acessível na URL durante a
> busca. Não há limpeza automática do bucket — configure uma regra de
> *lifecycle* (expiração) no provedor se quiser apagar os temporários.

### 2. `SerpApiLensProvider`
Chama `https://serpapi.com/search?engine=google_lens&url=…`. Requer
`SERPAPI_KEY`. Parâmetros ajustáveis por env: `IRIS_SERPAPI_HL` (idioma, padrão
`en`), `IRIS_SERPAPI_COUNTRY` (padrão `us`), `IRIS_SERPAPI_SAFE` (padrão
`active`). `provider_name = "serpapi_google_lens"`.

### 3. `normalize_serpapi_sources`
Achata vários blocos do JSON do Lens em uma lista uniforme de `WebSource`
(`title`, `url`, `source_url`, `domain`, `match_type`, `score`). Grupos lidos,
em ordem de prioridade: `knowledge_graph`, `about_this_image`, `exact_matches`,
`visual_matches`, `image_results`, `inline_images`, `organic_results`.
Deduplica por `(título, url)` e limita a 30 fontes.

### 4. Destiladores (sources → sugestão)
- **`HeuristicDistiller`** (sempre disponível, sem rede): infere `style`,
  `meme_archetype` por palavras-chave; `character` pela palavra mais frequente
  nos títulos (`_candidate_from_titles`); `source_work` por lista de obras
  conhecidas ou pela 2ª parte do título. Calcula `confidence` por quantidade de
  evidências e nº de fontes (teto 0.9).
- **`HybridDistiller`** (opcional): se `IRIS_LLM_ENDPOINT` + `IRIS_LLM_API_KEY`
  + `IRIS_LLM_MODEL` estiverem setados, manda as fontes para um endpoint
  compatível com OpenAI Chat Completions pedindo JSON estruturado. Em qualquer
  falha, cai no resultado heurístico. O endpoint pode ser local (Ollama,
  llama.cpp, LM Studio) — ver recomendações abaixo.

### 5. `WebEnrichmentService`
Orquestra provider → distiller, desacoplado do fornecedor concreto.
`enrich_path(path)` roda o pipeline para um arquivo e `missing_config()`
delega ao provider ativo.

## Abstração de providers (baixo acoplamento)

A busca reversa é uma interface (`ReverseImageProvider`, um `Protocol`):

```python
class ReverseImageProvider(Protocol):
    provider_name: str
    def missing_config(self) -> list[str]: ...
    def search_path(self, path) -> list[WebSource]: ...
```

Todo provider recebe um **caminho local** e devolve `WebSource`. Como ele chega
ao motor de busca é detalhe interno. Implementações:

- **`SerpApiLensProvider`** (padrão): encapsula o `S3TemporaryImagePublisher`
  internamente — publica a imagem, depois consulta a SerpApi. Requer
  `SERPAPI_KEY` + config S3.
- **`PlaywrightLensProvider`** (local, sem custo): dirige o Google Lens num
  browser headless e **faz upload do arquivo local direto** — não precisa de
  SerpApi nem de S3. Frágil por natureza (depende do DOM do Google) e voltado a
  volume baixo/pessoal. A interação com o browser fica isolada em
  `_scrape_lens`; o parsing puro está em `parse_lens_results` (testável sem
  browser). Requer `pip install playwright && playwright install chromium`
  (opcional: `pip install playwright-stealth` reduz detecção de bot).

> ⚠️ **CAPTCHA (testado a fundo, inclusive em IP residencial)**: o **upload
> sempre funciona** — toda tentativa gera um `vsrid` válido (o Google aceita a
> busca visual). O bloqueio é na **página de resultados**: ela redireciona para
> `/sorry/index` com reCAPTCHA ("tráfego incomum"). Isso foi reproduzido em IP
> de **fibra residencial** (não só datacenter), em **headless e em headed (com
> display real), com stealth, perfil persistente e cookies de consentimento** —
> em todos os casos o SERP do Lens (`udm=26&source=lns`) é walled. Ou seja, o
> gatilho é a automação na página de resultados, **não a reputação do IP**. O
> provider detecta o muro e lança erro claro em vez de retornar vazio.
>
> **Workaround semi-manual (único caminho grátis viável)**: rodar headed
> (`IRIS_LENS_HEADLESS=0`) com perfil persistente e **resolver o reCAPTCHA uma
> vez na janela visível**. O Google grava um cookie `GOOGLE_ABUSE_EXEMPTION`
> que persiste no perfil e libera as buscas seguintes por um tempo. Sem isso,
> para uso automatizado/sem supervisão, o caminho confiável continua sendo o
> SerpApi (ele absorve exatamente esse CAPTCHA com pool de proxies + solver).

A seleção é por env `IRIS_ENRICHMENT_PROVIDER` (`serpapi` | `playwright`),
resolvida em `build_reverse_image_provider()`. A lógica S3 permanece intacta
para quem quiser voltar ao SerpApi.

Variáveis do provider local:

| Variável | Padrão | Descrição |
|---|---|---|
| `IRIS_LENS_HEADLESS` | `1` | `0` abre o navegador visível (necessário para o fluxo semi-manual) |
| `IRIS_LENS_TIMEOUT_MS` | `45000` | Timeout das ações de navegação |
| `IRIS_LENS_PROFILE_DIR` | _(vazio)_ | Pasta de perfil persistente; guarda o cookie de isenção entre execuções |
| `IRIS_LENS_SOLVE_TIMEOUT_MS` | `180000` | Tempo de espera para você resolver o reCAPTCHA na janela visível |
| `IRIS_LENS_LOCALE` | `en-US` | Locale do contexto do navegador |

### Fluxo semi-manual (resolver o reCAPTCHA uma vez)

O `_scrape_lens` faz upload pelo caminho real do Lens (link "faça upload de um
arquivo" via *file chooser*, com fallback para o input legado `encoded_image`).
Quando o Google responde com `/sorry/` (reCAPTCHA):

- **headless** (`IRIS_LENS_HEADLESS=1`): não há quem resolva → lança erro claro
  pedindo para rodar headed ou usar SerpApi.
- **headed** (`IRIS_LENS_HEADLESS=0`): imprime no log a instrução e **aguarda**
  (`IRIS_LENS_SOLVE_TIMEOUT_MS`) você clicar no "I'm not a robot" na janela.
  Resolvido, ele segue e extrai os resultados.

Com `IRIS_LENS_PROFILE_DIR` apontando para uma pasta fixa, o cookie
`GOOGLE_ABUSE_EXEMPTION` gravado na resolução **persiste**, então as próximas
imagens do lote passam sem novo CAPTCHA (até o cookie expirar).

```bash
export IRIS_ENRICHMENT_PROVIDER="playwright"
export IRIS_LENS_HEADLESS=0
export IRIS_LENS_PROFILE_DIR="$HOME/.iris/lens-profile"
# resolva o reCAPTCHA uma vez na primeira imagem; o restante do lote reaproveita
```

## Cache (evita re-pesquisar e gastar de novo)

Antes de pesquisar uma imagem, `_run_web_enrichment_job` checa
`find_existing_suggestion(conn, meme_id)`: se já existe sugestão `pending` ou
`applied` para aquele registro, **pula a busca** (não republica no S3 nem chama
a API) e marca como `reaproveitado (cache)`. Sugestões já obtidas ficam
persistidas — sair antes de revisar não perde nada nem custa de novo.

Para refazer de propósito há **dois** caminhos (botões no painel, com
confirmação):

- **Re-enviar pra IA** (`force=1`, `research=0`): reaproveita as **fontes já
  encontradas** do último Lens (`load_existing_sources`) e roda só o destilador
  (`WebEnrichmentService.redistill`) — não reabre o navegador nem refaz a busca.
  É o caminho para experimentar outro backend de IA numa imagem já pesquisada.
- **Re-buscar no Lens** (`force=1`, `research=1`): refaz a busca do zero
  (`enrich_path`), reabrindo o provider.

Quando todas as imagens do lote já têm fontes e não é `research`, a config do
provider de busca nem é exigida (não há busca nova). `POST /api/enrichment/jobs`
retorna `cached` (reaproveitadas pelo cache) e `research` para a UI.

> Priorização de fontes: `parse_lens_results` **descarta** domínios ruins para
> identificar imagem (vídeo/stock/loja/social: youtube, instagram, shutterstock,
> amazon…) e **ranqueia primeiro** os explicativos (knowyourmeme, fandom, wiki,
> reddit, myanimelist…), via `_domain_tier`. Assim a IA recebe fontes com
> conteúdo relevante em vez de links de vídeo/loja.

## Persistência (tabelas)

Criadas por `create_web_enrichment_tables`:

- **`web_enrichment_jobs`** — um job por lote: `status`
  (`queued`→`running`→`completed`/`failed`), `total`, `done`, `message`.
- **`web_enrichment_suggestions`** — uma sugestão por imagem do lote, com os
  campos destilados e `status` (`pending`→`applied`/`rejected`).
- **`web_enrichment_sources`** — os links/fontes (wiki, fandom, etc.) de cada
  sugestão.

`apply_suggestion` (revisão humana) grava na tabela `memes` (`source_work`,
`style`, `context`, `tags`, `descricao_ia` recebe o resumo prefixado com
`Web:`) e cria/conecta `concepts`/`concept_media` para `character`,
`source_work` e `meme_archetype`.

## API HTTP

| Método | Rota | Função |
|---|---|---|
| POST | `/api/enrichment/jobs` | Cria job para `db_ids` (CSV). Param `force` ignora o cache. Retorna `cached`. Roda em thread daemon |
| GET | `/api/enrichment/jobs/{job_id}` | Progresso do job |
| GET | `/api/enrichment/suggestions?status=pending` | Lista sugestões (`pending`/`applied`/`rejected`/`all`) |
| POST | `/api/enrichment/suggestions/{id}/apply` | Aplica (campo `fields` CSV opcional limita o que escrever) |
| POST | `/api/enrichment/suggestions/{id}/reject` | Rejeita |

O job roda em background (`threading.Thread`), uma imagem por vez, gravando uma
sugestão por imagem mesmo em caso de erro (com `error_message`).

## Fluxo na UI
`static/app.js`: botão **Enriquecer selecionados** dispara o job, faz *polling*
do progresso e abre o painel de sugestões, onde cada cartão mostra os campos
inferidos + fontes e botões aplicar/rejeitar.

## Setup mínimo

```bash
# S3 (ex.: Cloudflare R2)
export IRIS_S3_ENDPOINT_URL="https://<acct>.r2.cloudflarestorage.com"
export IRIS_S3_BUCKET="iris-temp"
export IRIS_S3_ACCESS_KEY_ID="..."
export IRIS_S3_SECRET_ACCESS_KEY="..."
export IRIS_S3_PUBLIC_BASE_URL="https://temp.seu-dominio.com"

# SerpApi (Google Lens)
export SERPAPI_KEY="..."

# (opcional) LLM para destilar melhor as fontes
export IRIS_LLM_ENDPOINT="http://localhost:11434/v1/chat/completions"
export IRIS_LLM_API_KEY="ollama"
export IRIS_LLM_MODEL="qwen2.5:7b"
```

Sem S3 ou sem `SERPAPI_KEY`, `POST /api/enrichment/jobs` retorna 400 listando o
que falta. Sem config de LLM, o pipeline funciona só com a heurística.

### Alternativa local (sem SerpApi e sem S3)

```bash
export IRIS_ENRICHMENT_PROVIDER="playwright"
pip install playwright && playwright install chromium
# (opcional) export IRIS_LENS_HEADLESS=0  # ver o browser
```

Nesse modo não há custo de API nem upload para nuvem — o Lens é dirigido
localmente. Em troca, é mais lento (um browser por imagem) e sensível a
mudanças no DOM do Google; recomendado para volume baixo.

### Qualidade do metadado (extração + destilação)

O scraping local devolve apenas **links** (páginas que casam visualmente), não
um "entendimento" da imagem. A qualidade depende de duas etapas:

1. **Extração** (`_extract_results`): prioriza os *cards* de match visual (âncoras
   que envolvem uma miniatura `<img>`) e descarta navegação/rodapé/pesquisas
   relacionadas por uma lista de stopwords. Fontes com miniatura vêm marcadas
   como `lens_visual_match` (score 1) e são ranqueadas à frente das `lens_link`.
   Sem esse filtro, títulos de menu/rodapé entram como "fonte" e poluem tudo.
2. **Destilação** (sources → metadado): a **heurística** apenas conta palavras
   nos títulos (com peso extra para knowyourmeme/fandom/wikipedia), então acerta
   no máximo personagem/obra óbvios — **não entende** que algo é um meme nem o
   contexto/piada. Para isso é preciso o **LLM** (`HybridDistiller`).

> ⚠️ Para o resultado que se espera ("isto é a Frieren, é um meme do olhar pra
> cima, keywords: olhar/staring"), **habilite o LLM local**. Numa RTX 4050 roda
> bem um modelo 7B (ex.: `qwen2.5:7b` no Ollama). O prompt já instrui o modelo a
> identificar personagem, obra, se é meme, o contexto e as palavras-chave
> (inclusive pose/ação), dando mais peso a knowyourmeme/fandom/wiki:

```bash
export IRIS_LLM_ENDPOINT="http://localhost:11434/v1/chat/completions"
export IRIS_LLM_API_KEY="ollama"
export IRIS_LLM_MODEL="qwen2.5:7b"
```

### Backends de IA plugáveis (`LLMBackend`)

A destilação por IA é desacoplada do *transporte*: o mesmo **prompt limpo**
(`build_distill_messages` — só títulos + domínios, nunca HTML cru, para gastar
poucos tokens) é enviado por qualquer backend que implemente o `Protocol`
`LLMBackend` (`available()` + `complete(system, user)`):

| Backend | Transporte | Quando usar |
|---|---|---|
| `OpenAICompatBackend` | API OpenAI-compatível (ChatGPT, Ollama, LM Studio) | Estável, melhor; consome cota da API |
| `GeminiAPIBackend` | API Gemini `generateContent` | Estável; consome cota da API |
| `WebChatBackend` | ChatGPT logado, via browser (deep link) | Grátis; usa sua conta; **frágil** (DOM/login), precisa calibrar |

#### `WebChatBackend` — ChatGPT logado, ponta a ponta

Fluxo automático: abre uma aba já com o prompt **pré-preenchido por URL** (deep
link `chatgpt.com/?q=...&hints=search`), envia, espera a resposta terminar,
captura e preenche a sugestão (você aprova/rejeita como sempre). O *deep link*
elimina a parte mais frágil (não digita na caixa); ainda restam enviar + esperar
+ capturar, que dependem do DOM do ChatGPT — por isso precisa de **calibração ao
vivo** na sua máquina.

Decisões (com o usuário):
- **Perfil dedicado do Iris** (`IRIS_WEBCHAT_PROFILE_DIR`, padrão
  `~/.iris/webchat-profile`): você loga no ChatGPT **uma vez** nesse perfil e ele
  é reaproveitado; seu Chrome pessoal fica intacto. Alternativa: `IRIS_WEBCHAT_CDP`
  anexa ao seu Chrome já aberto (precisa subir com `--remote-debugging-port=9222`).
- **Conversa temporária por padrão** (`IRIS_WEBCHAT_TEMPORARY=1` →
  `temporary-chat=true`): a busca não fica no seu histórico do ChatGPT. Toggle na
  UI. O parâmetro temporário é um pouco instável no ChatGPT, mas como mandamos só
  uma pergunta/resposta, costuma segurar.

> ⚠️ **Use o Chrome do sistema, não o Chromium do pip.** O Chromium "pelado" do
> Playwright é barrado pela **Cloudflare** do chatgpt.com (página "Just a
> moment…"). Por isso o padrão é o canal **`chrome`** (`IRIS_WEBCHAT_CHANNEL`,
> com fallback automático para o Chromium se o Chrome não estiver instalado).

Login: antes de mandar o prompt, abre o `chatgpt.com` base e checa se o composer
(`#prompt-textarea`) está disponível. Se não estiver (não logado ou Cloudflare),
**avisa no log e aguarda** você logar/resolver na janela visível (até
`IRIS_WEBCHAT_LOGIN_TIMEOUT_MS`, padrão 300 s). Como o perfil é persistente, o
login só é pedido uma vez. Em headless não há quem logue → erro claro.

Detalhes técnicos:
- Só **ChatGPT** (Gemini não tem prefill por URL nativo — para Gemini use a API).
- Loga na base **antes** de abrir o deep link (um redirect de login descartaria o
  `?q=` pré-preenchido).
- `build_webchat_url` monta o prompt compacto + os **top ~8 matches (título +
  URL)** e **corta matches** até a URL ficar < ~6 KB, evitando o erro 414 (URI
  too long) de proxies/CDN.
- Roda **headed** (login + bem menos detecção); uma aba/contexto por imagem.

`LLMDistiller` monta o prompt, chama o backend, faz parsing tolerante do JSON
(`_extract_json`, aceita cercas markdown/prosa) e cai na heurística em qualquer
falha. `build_distiller(overrides)` escolhe o backend por `IRIS_LLM_BACKEND`
(`heuristic` | `openai` | `gemini` | `webchat`), com a UI sobrepondo o env.

Seleção por env:

```bash
export IRIS_LLM_BACKEND="gemini"      # ou openai / webchat / heuristic
export IRIS_LLM_API_KEY="..."         # APIs; web-chat não precisa
export IRIS_LLM_MODEL="gemini-2.0-flash"
# web-chat (ChatGPT logado):
export IRIS_WEBCHAT_PROFILE_DIR="$HOME/.iris/webchat-profile"
export IRIS_WEBCHAT_CHANNEL="chrome"  # usa o Chrome do sistema (passa Cloudflare)
export IRIS_WEBCHAT_TEMPORARY="1"     # 0 = conversa normal (fica no histórico)
# export IRIS_WEBCHAT_CDP="http://localhost:9222"  # alternativa: anexar ao seu Chrome
```

**Na UI**: o painel "Enriquecimento web" tem um seletor de backend (Heurística /
API ChatGPT / API Gemini / Web-chat) com campos de modelo/site/CDP. A escolha
é persistida no navegador e vale para as próximas buscas (inclusive o "Forçar
re-pesquisa"). Por segurança, **chaves de API ficam no env**, não na interface.

## Navegador compartilhado e persistente (`core/browser_session.py`)

Para não abrir/fechar o Chromium a cada imagem, há uma **sessão de navegador
única e persistente**, reusada entre jobs.

Por que não é "só guardar numa global": a API *sync* do Playwright é
**presa à thread** que criou o browser, e cada job roda numa thread nova. Então a
`BrowserSession` roda o navegador numa **thread-dona dedicada** e todo trabalho de
browser é enviado pra ela via fila — `submit(fn)` executa `fn(context)` nessa
thread e devolve o resultado. Com uma só worker, jobs concorrentes **serializam**
naturalmente (dois jobs nunca dirigem o browser ao mesmo tempo).

- **Um perfil, um browser** para Lens **e** ChatGPT: o Lens abre uma aba, extrai,
  fecha a aba; o ChatGPT abre outra aba no **mesmo** navegador. Nada de relançar.
- **Janela oculta** por padrão, **restaurada automaticamente** só quando precisa
  de você — CAPTCHA do Lens (`_await_results`) ou login do ChatGPT
  (`_ensure_ready`) — voltando a ocultar depois (`set_window_visible`).
  - **X11**: minimiza via CDP `Browser.setWindowBounds`.
  - **Wayland/Hyprland**: no Wayland o cliente não move a própria janela (quem
    manda é o compositor), então usamos `hyprctl`: a janela é lançada com uma
    `--class` única (`IRIS_BROWSER_WINDOW_CLASS`, padrão `iris-meme-browser`) e
    movida para um *special workspace* (scratchpad) para ocultar; ao precisar de
    você, volta para o workspace ativo e recebe foco. Detecção por
    `HYPRLAND_INSTANCE_SIGNATURE` + `hyprctl` no PATH; senão cai no CDP.
- Usa o **Chrome do sistema** (fallback Chromium), headed.
- Fecha no shutdown do servidor (`close_browser_session` no `lifespan`).

Variáveis:

| Variável | Padrão | Descrição |
|---|---|---|
| `IRIS_BROWSER_SHARED` | `1` | `0` volta ao modo antigo (um browser por operação) |
| `IRIS_BROWSER_PROFILE_DIR` | `~/.iris/webchat-profile` | Perfil único (Lens + ChatGPT); reusa o login já feito |

Os providers/backends mantêm o **modo standalone** como fallback
(`IRIS_BROWSER_SHARED=0` ou `scraper=`/`completer=`/`cdp_url` injetados), então a
lógica continua testável sem browser.

## Testes (rede de segurança)

Objetivo: se a suíte passa, a **lógica** em produção funciona. O que é
não-determinístico (browser/rede real) fica **isolado atrás de costuras
injetáveis** e é validado por calibração ao vivo, não na CI.

Camadas (`tests/`):

| Arquivo | Cobre |
|---|---|
| `test_web_enrichment.py` | Núcleo puro: parsing/ranqueamento (`parse_lens_results`, `_domain_tier`), destiladores e backends (via fakes), `build_webchat_url`, `_extract_json`, fallback de upload, espera de CAPTCHA/login. |
| `test_enrichment_job.py` | Orquestração `_run_web_enrichment_job`: cache, reaproveitar fontes (redistill), busca nova (research), erro → sugestão com `error_message`. |
| `test_enrichment_api.py` | Endpoints HTTP: validação, formato de resposta, flags `force`/`research` chegando ao job, gating de config do provider. |
| `test_static_wiring.py` | Fiação da UI: todo `getElementById('x').addEventListener` tem `id` no HTML; assets `/static` existem; JS/CSS têm `?v=` (cache-busting). |
| `test_browser_session.py` | Mecânica da `BrowserSession`: `submit` roda na thread-dona, devolve resultado/exceção, serializa jobs concorrentes, `close` para e limpa (via `launcher` fake, sem browser). |

**Costuras injetáveis** (mantenha-as ao editar — é o que torna o resto testável
sem rede/browser):

- `PlaywrightLensProvider(scraper=...)` — substitui a automação do Lens.
- `WebChatBackend(completer=...)` — substitui a automação do ChatGPT.
- `LLMBackend` é um `Protocol`: backends fake nos testes implementam
  `available()` + `complete()`.
- Páginas fake (`_FakePage`, `_FakeChatPage`, `_FakeUploadPage`) exercitam a
  lógica de DOM (espera de resultado, login, upload) sem um browser.

**O que os testes NÃO garantem** (e por quê): o sucesso real da automação de
browser contra o DOM do Google Lens e do ChatGPT + Cloudflare. Esses seletores
mudam sem aviso e dependem de IP/login — valida-se ao vivo na máquina do usuário
(ver os avisos de CAPTCHA/login acima), não na CI.

Rodar: `pytest tests/test_web_enrichment.py tests/test_enrichment_job.py
tests/test_enrichment_api.py tests/test_static_wiring.py`.
