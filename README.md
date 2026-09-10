# Iris

**Local multimodal AI media intelligence.** Index, search, and organize your entire media collection with AI — the models run on your own machine and your media never leaves it.

> One exception, stated plainly: semantic search translates your query to English > before encoding it, because the embedding model is English-trained, and that > translation call goes to an external service. Your files stay put; the words you > type in the search box do not. Set `IRIS_TRANSLATE_QUERIES=0` to keep everything > local, at the cost of weaker results for non-English queries.

🌐 **[Project page → miguellopesdel.github.io/Iris](https://miguellopesdel.github.io/Iris/)**

Iris grew out of a meme search tool and became something bigger: a self-hosted AI librarian for images, videos, GIFs, audio, and SVGs. It transcribes speech, reads text in images, describes scenes, and lets you find any file in seconds using natural language, visual queries, or named visual concepts.

---

## What Iris does

| Capability | Description |
|---|---|
| **Semantic search** | "sad frog in a suit" finds the right image even without matching keywords |
| **Visual search** | Upload an image to find visually similar files in your library |
| **Face / person search** | Upload a photo of a person to find every image and video they appear in; faces are grouped into named people (InsightFace / ArcFace) |
| **Semantic audio search** | Describe a sound ("male voice speaking", "electronic music") — CLAP bridges text and audio |
| **OCR** | Extracts printed and handwritten text from images automatically |
| **AI captions** | Florence-2 describes scene content; used as a search signal |
| **Speech transcription** | Whisper transcribes audio and video files |
| **Concept recognition** | Teach Iris to recognize people, characters, or objects by showing reference images |
| **Web enrichment** | Reverse-image lookup (Google Lens) + LLM distills character, source work, and tags |
| **Auto-metadata & albums** | Reads EXIF date/GPS at import and suggests collections (great for photo libraries) |
| **Collections / albums** | Organize files into named albums during or after import |
| **Duplicate detection** | Hash-exact, perceptual (images/video), and Chromaprint (audio) deduplication with an import-review quarantine |
| **Background indexing** | Import thousands of files without interrupting search or browsing |

## Supported formats

| Type | Extensions |
|---|---|
| Images | PNG, JPG, JPEG, WEBP, GIF, SVG |
| Video | MP4, WEBM, MKV, MOV, OGG |
| Audio | MP3, OGG |

---

## Quick start

### Requirements

- Python 3.10 or newer
- Linux (primary platform) — macOS works with CPU; Windows untested
- NVIDIA GPU with CUDA 12.6 recommended (RTX 3060+ for comfortable speed)
- 16 GB RAM or more
- ~10 GB disk for AI model weights (downloaded on first run)

CPU search is fully supported. For an older APU such as a Ryzen 3 3200G, use the
low-resource profile below; its integrated GPU is not a CUDA device, so Iris uses
the CPU and system RAM deliberately.

### Development / contributing

For a local disposable two-account sandbox with hot reload, see
[CONTRIBUTING.md](CONTRIBUTING.md). It does not require Docker, Tailscale, a second
computer, personal media, or an AI model download.

### Install

```bash
git clone https://github.com/MiguelLopesDel/Iris.git
cd iris

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

This installs CPU PyTorch by default, so an NVIDIA GPU is not required. On
NVIDIA/CUDA 12.6 machines, use `pip install --force-reinstall -r requirements-cuda.txt`
afterwards to replace the CPU runtime.

### Run

```bash
./scripts/run_app.sh
# or:
python3 -m uvicorn server:app --host 127.0.0.1 --port 8501
```

Open **http://localhost:8501** in your browser.

By default Iris opens `data/iris_v1.db` (falling back to a legacy
`data/meme_compass_full_v1.db` if that's the only catalog present) and resolves media under `media/`.
Use the **Sistema** tab to switch databases and media roots, import/index folders or
uploaded files, choose CPU/CUDA/MPS, and manage versioned catalog snapshots (point-in-time
backup/restore to an external folder, plus media reconcile/export).

You can also override the startup paths through environment variables:

```bash
IRIS_DB=data/library.db IRIS_MEDIA_ROOT=/path/to/media ./scripts/run_app.sh
```

### Servidor privado para família ou equipe

Iris começa no modo de biblioteca única para preservar instalações existentes. Para
ativar contas e bibliotecas realmente isoladas, faça backup de `data/` e `media/`,
pare o Iris e execute uma única vez:

```bash
python scripts/bootstrap_admin.py --username administrador --display-name "Seu nome"
```

O script move o catálogo, índices FAISS, miniaturas e mídia atuais para a primeira
biblioteca privada. Ao reiniciar, o Iris ativa login; somente essa conta administradora
cria contas adicionais. Cada conta recebe seu próprio SQLite, índices FAISS, mídia,
miniaturas e fila de importação.

Para acesso remoto, mantenha o Iris em `127.0.0.1` e publique esse endereço com a
camada privada que você preferir — Tailscale, ZeroTier, WireGuard, um proxy reverso
com TLS. O servidor não depende de nenhuma delas. Com Tailscale, por exemplo:

```bash
sudo tailscale serve --bg http://127.0.0.1:8501
```

Não exponha a porta diretamente à Internet sem TLS e rate limiting. O servidor processa originais para busca,
pessoas e duplicatas; portanto, o operador do host pode tecnicamente ler os arquivos.
Permissões de conta isolam usuários entre si, mas não substituem criptografia ponta a
ponta contra quem controla o servidor.

Para validar uma instalação real e medir navegação/busca com clientes concorrentes,
consulte [docs/testing.md](docs/testing.md) e
[docs/performance-testing.md](docs/performance-testing.md).

---

## Docker

Para instalar como servidor privado multiusuário, siga o guia completo em
[docs/server-deployment.md](docs/server-deployment.md). O Compose padrão publica
somente em `127.0.0.1`; para alcançá-lo de outros aparelhos, ponha na frente a VPN,
malha ou proxy reverso da sua escolha.

### CPU (no GPU required)

```bash
docker compose up
```

Open http://localhost:8501.

### GPU (NVIDIA)

Requires [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html):

```bash
docker compose -f docker-compose.gpu.yml up
```

### Data persistence

Mount your media and database directories:

```yaml
volumes:
  - ./data:/app/data
  - /path/to/your/media:/media:ro
```

---

## CLI indexing

Index a folder directly from the terminal (useful for large initial imports):

```bash
source venv/bin/activate

# Basic index
python -m core.indexer --dir /path/to/media --db data/library.db

# With GPU, recursive, skip captions for speed
python -m core.indexer --dir /path/to/media --db data/library.db \
  --device cuda --recursive --caption-model none

# Rebuild FAISS index only (after manual DB edits)
python -m core.indexer --db data/library.db --rebuild-faiss-only

# Older CPU / integrated graphics: creates a smaller, CPU-only semantic index.
python -m core.indexer --dir /path/to/media --db data/iris_light.db \
  --recursive --low-resource
IRIS_DB=data/iris_light.db IRIS_MODEL=sentence-transformers/clip-ViT-B-32 \
  ./scripts/run_app.sh
```

The **Sistema → Importar** panel also has **Modo PC fraco**. It applies the same
profile: CPU, batch 1, smaller CLIP, and no caption/transcription/face models.
Use it for a new catalog (or reindex an old one): different CLIP models cannot be
mixed in a database.

### SigLIP 2 (optional, requires a full reindex)

`IRIS_MODEL` also accepts a SigLIP 2 checkpoint, e.g.
`google/siglip2-base-patch16-224` or `google/siglip2-so400m-patch14-384`. It is a
stronger retrieval model than `clip-ViT-L-14` and it is multilingual, but the two
embedding spaces are unrelated: **an existing catalog must be reindexed**, and the
taxonomy thresholds in `core/taxonomy.py` were calibrated for the CLIP similarity
distribution, so they need recalibrating too. Point `IRIS_MODEL` at SigLIP without
reindexing and search refuses to run rather than returning a meaningless ranking —
`siglip2-base` happens to have the same 768 dimensions as CLIP, so the shape check
alone would not catch the mistake.

The environment variable is a deployment-wide override. It applies to the server,
existing multi-user accounts, newly created accounts, and `python -m core.indexer`
when `--model` is omitted. Restart Iris after changing it. Until every embedding in
an existing catalog has been rebuilt with the selected model, search deliberately
rejects that catalog, including catalogs that contain a mixture of model names.

Note for anyone extending this: SigLIP is loaded through `core/embedding_models.py`,
not through `sentence-transformers`. `sentence-transformers` pads a batch to its
longest sequence, while SigLIP's text tower was trained with a fixed 64-token
padding and is not invariant to it — the same query encoded alongside a longer
sentence comes out as a different vector (measured cosine 0.76). That silently
makes an index depend on the order the files were processed in.

---

## Concept recognition

Teach Iris to recognize visual entities — people, characters, places, objects:

1. Go to the **Conceitos** tab
2. Click **Criar novo conceito**, choose a category and name
3. Upload 2–5 reference images
4. Click **Encontrar matches automáticos** — Iris scans the library and proposes candidates
5. Deselect false positives, click **Aplicar**
6. Search by concept name: Iris uses visual similarity, not text matching

---

## Face & person search

Find every image and video where a specific person appears — independent of the rest of the
scene (CLIP visual search looks at the whole frame; this looks at the **face**).

1. Open the **Pessoas** tab and click **Agrupar rostos** — Iris clusters detected faces into people
2. Name a person once; click their card to browse all their media
3. Or, in the gallery, use **Buscar pessoa** to upload a photo and find that person across the library

Faces are detected during indexing. For a library indexed **before** this feature existed, run the
one-time backfill (it does **not** recompute CLIP embeddings):

```bash
python scripts/backfill_faces.py --db data/library.db
```

> First run downloads the InsightFace `buffalo_l` models (~300 MB). Disable face extraction during
> indexing with `--no-faces` if you don't need it.

---

## Architecture

```
core/indexer.py          — indexing pipeline: OCR → captions → Whisper → CLIP (+ CLAP/Chromaprint for audio) → SQLite/FAISS
core/search_engine.py    — hybrid ranking: visual CLIP + description embeddings + lexical bonus + CLAP audio
core/duplicates.py       — exact-hash, perceptual, and Chromaprint clustering with single-linkage merge
core/concepts.py         — concept store: reference embeddings, auto-tagging, confirmed/rejected
core/faces.py            — face detection + ArcFace embeddings (InsightFace), person clustering
core/taxonomy.py         — zero-shot CLIP classification (style, source work, humor, context)
core/web_enrichment.py   — reverse-image lookup (Google Lens) + LLM distillation of metadata
core/media_metadata.py   — EXIF/ffprobe extraction (date, GPS, source app) at import
core/import_suggestions.py — groups freshly imported media into suggested collections
server.py                — FastAPI application and REST API
templates/index.html     — browser application shell
static/                  — CSS and JavaScript frontend modules
```

**Visual embedding**: `sentence-transformers/clip-ViT-L-14` (768-dim, stored in FAISS)  
**Audio embedding**: `laion/clap-htsat-unfused` (512-dim; optional, for semantic audio search and dedup)  
**Face embedding**: InsightFace `buffalo_l` / ArcFace (512-dim; in-memory FAISS index, models auto-downloaded on first use)
**Caption model**: `microsoft/Florence-2-large` (can be disabled with `--caption-model none`)  
**Transcription**: OpenAI Whisper (default: `tiny` model; disable with `--whisper-model none`)  
**Database**: SQLite (schema v4) + FAISS flat indices for image, description, and audio embeddings

---

## Evaluation pipeline

Measure search quality before indexing everything:

```bash
# Check GPU
python scripts/gpu_probe.py --require-cuda

# Sample 100 files for evaluation
python scripts/sample_media.py --dir media --sample-size 100 --seed 42 \
  --output data/eval/samples/sample_100.json

# Build sample index
python scripts/build_sample_index.py \
  --manifest data/eval/samples/sample_100.json \
  --db data/eval/indexes/sample_100.db

# Edit queries.json, then evaluate
python scripts/evaluate_search.py \
  --db data/eval/indexes/sample_100.db \
  --queries data/eval/packs/sample_100/queries.json
```

Target: **Recall@10 ≥ 90%**, **Recall@20 ≥ 95%** on a 30-image golden set.

---

## Development

```bash
source venv/bin/activate

pytest                              # full suite
./scripts/run_tests.sh              # standard suite; also: db | model | integration | golden | all | menu
ruff check core scripts tests
python -m compileall -q core scripts tests server.py
```

### Commit style

Conventional Commits: `feat(search): ...`, `fix(ui): ...`, `refactor(core): ...`

UI changes should include screenshots. DB/FAISS-impacting changes should note required rebuild steps.

---

## License

Iris Non-Commercial Personal-Use License v1.0 — see [LICENSE](LICENSE).

Iris is **source-available**, not open source. You may use it and create your own
forks for **personal, non-commercial** use, as long as you **credit the author**.
Selling Iris or any fork, or offering it as a paid product/service, is not
permitted. The author retains full ownership and may revoke the license for any
fork at any time. For commercial use, contact the author.
