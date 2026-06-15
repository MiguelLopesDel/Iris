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
Orquestra publisher → provider → distiller. `missing_config()` reúne o que
falta de S3 + SerpApi. `enrich_path(path)` roda o pipeline para um arquivo.

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
| POST | `/api/enrichment/jobs` | Cria job para `db_ids` (CSV). Valida config; roda em thread daemon |
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
