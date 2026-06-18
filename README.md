# Iris

**Local multimodal AI media intelligence.** Index, search, and organize your entire media collection with AI — all running on your own machine, with no data leaving your device.

🌐 **[Project page → miguellopesdel.github.io/Iris](https://miguellopesdel.github.io/Iris/)**

Iris grew out of a meme search tool and became something bigger: a self-hosted AI librarian for images, videos, GIFs, audio, and SVGs. It transcribes speech, reads text in images, describes scenes, and lets you find any file in seconds using natural language, visual queries, or named visual concepts.

---

## What Iris does

| Capability | Description |
|---|---|
| **Semantic search** | "sad frog in a suit" finds the right image even without matching keywords |
| **Visual search** | Upload an image to find visually similar files in your library |
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

CPU fallback exists but indexing is significantly slower.

### Install

```bash
git clone https://github.com/MiguelLopesDel/Iris.git
cd iris

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

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

---

## Docker

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
```

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

## Architecture

```
core/indexer.py          — indexing pipeline: OCR → captions → Whisper → CLIP (+ CLAP/Chromaprint for audio) → SQLite/FAISS
core/search_engine.py    — hybrid ranking: visual CLIP + description embeddings + lexical bonus + CLAP audio
core/duplicates.py       — exact-hash, perceptual, and Chromaprint clustering with single-linkage merge
core/concepts.py         — concept store: reference embeddings, auto-tagging, confirmed/rejected
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
