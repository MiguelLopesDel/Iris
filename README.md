# Iris

**A private, self-hosted photo and video library for people, families, and teams.** Host Iris on your own computer or server, give each person an isolated account and library, and connect from the browser or Android app. AI-assisted search and media processing are optional capabilities, not requirements for receiving and browsing a library.

> One exception, stated plainly: semantic search translates your query to English
> before encoding it, because the embedding model is English-trained, and that
> translation call goes to an external service. Your files stay put; the words you
> type in the search box do not. Set `IRIS_TRANSLATE_QUERIES=0` to keep everything
> local, at the cost of weaker results for non-English queries.

🌐 **[Project page → miguellopesdel.github.io/Iris](https://miguellopesdel.github.io/Iris/)**

Iris is designed as a familiar gallery backed by a server you control. Accounts keep private libraries separate, while shared spaces let invited people contribute to a group gallery. The server is the durable home for originals; clients can browse and synchronize without requiring a GPU or running AI work on the upload path. See the [product vision](docs/product-vision.md) for confirmed behavior and the [implementation roadmap](docs/implementation-roadmap.md) for what remains planned or unvalidated.

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

## Install the Iris server

Iris runs as one server that holds the originals and does the AI work; the
browser and the Android app connect to it. The server ships as a Docker image
(`ghcr.io/miguellopesdel/iris`), so the machine running it needs Docker, not
Python, PyTorch or CUDA. The full guide, with every option, is
[docs/server-deployment.md](docs/server-deployment.md).

### What you need

| | Minimum | Recommended |
|---|---|---|
| OS | Linux x86_64 | Linux x86_64 |
| Software | Docker Engine + Docker Compose v2, `git`, `curl` | same |
| RAM | 8 GB | 16 GB or more |
| Disk | space for your photos and videos, plus ~10 GB for AI models | a second disk for backups |
| GPU | none — everything runs on the CPU | NVIDIA RTX 20 series or newer, driver ≥ 580, [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) |

### 1. Download and install

```bash
git clone https://github.com/MiguelLopesDel/Iris.git
cd Iris
./scripts/server.sh install
```

`install` creates `.env` for your Linux user, prepares `data/`, `media/` and
`backups/`, downloads the Iris image and waits until the server is healthy. The
AI models (~10 GB) download on first use and are kept between updates.

**NVIDIA GPU:** nothing to choose. When the machine has an NVIDIA driver and
Docker can reach it (NVIDIA Container Toolkit), `install` picks the CUDA image
and gives the container the GPU; otherwise it uses the CPU image and says why.
Whether Iris actually uses the GPU is then an administrator setting in
**Sistema → Instalação → Processamento**, changed without reinstalling.
`install --gpu` / `--cpu` override the detection.

### 2. Create the administrator account

`install` ends with an address and a one-time **installation code**:

```
Finish setup in the browser: http://127.0.0.1:8501/setup
Installation code: K7QM-4XPA
```

Open that page, type the code, and choose the administrator's username and
password. The code proves you can read the server's console, not just reach
the page; it is also in the server log, and `./scripts/server.sh setup-code`
shows it again. Setup closes for good once the administrator exists; you are
signed in right away and create the other accounts in **Sistema**. Each account
gets its own private library; shared spaces are opt-in. (Headless servers can
still use `./scripts/server.sh create-admin --username admin` instead.)

### 3. Reach it from your other devices

Iris listens only on `127.0.0.1` of the server: nothing is exposed to your
network or the Internet until you choose how. Put a private layer in front of
it — Tailscale, ZeroTier, WireGuard, or a reverse proxy with TLS. With
Tailscale, for example:

```bash
sudo tailscale serve --bg http://127.0.0.1:8501
```

Then open the `https://<server>.<tailnet>.ts.net` address it prints from any
device on your tailnet, in the browser or as the server URL in the Android app
(see [android/README.md](android/README.md); the app is built from source, no
store release yet). Other options are in
[docs/server-deployment.md](docs/server-deployment.md#acesso-remoto-privado).
Do not map the port to `0.0.0.0` without TLS and rate limiting.

### 4. Day to day

```bash
./scripts/server.sh status          # health and container state
./scripts/server.sh setup-code      # the installation code, while setup is pending
./scripts/server.sh logs            # follow the logs
./scripts/server.sh backup          # back up now (a daily backup is automatic)
./scripts/server.sh update          # back up, then move to the IRIS_VERSION image
```

`IRIS_VERSION=latest` in `.env` follows each release; set a version such as
`0.4.0` to update only when you decide. Backups go to `IRIS_BACKUP_DIR`
(`./backups` by default) every day at 03:00 — point it at another disk.

---

## Run from source (development)

For a disposable two-account sandbox with hot reload, see
[CONTRIBUTING.md](CONTRIBUTING.md); it needs no Docker, personal media or model
download. To run Iris directly from a checkout, without Docker:

```bash
git clone https://github.com/MiguelLopesDel/Iris.git
cd Iris
python3.13 -m venv venv
source venv/bin/activate
pip install --no-deps --require-hashes -r requirements.txt
python scripts/check_deps.py
./scripts/run_app.sh        # or: python3 -m uvicorn server:app --host 127.0.0.1 --port 8501
```

Open **http://localhost:8501**. The `requirements*.txt` files are complete locks
for Linux x86_64 and Python 3.13, so `--no-deps` is required (see
[docs/dependency-profiles.md](docs/dependency-profiles.md)). On NVIDIA machines,
use a separate virtualenv with `requirements-cuda.txt` and check it with
`python scripts/check_deps.py --cuda`. On macOS, where the Linux locks do not
apply, `pip install -r requirements-cpu.in` resolves the newest compatible
versions (best effort, untested).

CPU search is fully supported. For an older APU such as a Ryzen 3 3200G, use the
low-resource profile under [CLI indexing](#cli-indexing); its integrated GPU is
not a CUDA device, so Iris uses the CPU and system RAM deliberately.

By default Iris opens `data/iris_v1.db` (falling back to a legacy
`data/meme_compass_full_v1.db` if that's the only catalog present) and resolves
media under `media/`. Use the **Sistema** tab to switch databases and media
roots, import/index folders or uploaded files, choose CPU/CUDA/MPS, and manage
versioned catalog snapshots. The startup paths can also come from the
environment:

```bash
IRIS_DB=data/library.db IRIS_MEDIA_ROOT=/path/to/media ./scripts/run_app.sh
```

A source install starts as a single library. To turn on accounts, back up
`data/` and `media/`, stop Iris and run once
`python scripts/bootstrap_admin.py --username admin --display-name "Your name"`:
it moves the current catalog, indexes, thumbnails and media into the first
private library and enables login on the next start.

The server processes originals for search, people and duplicates, so whoever
controls the host can technically read the files. Accounts isolate users from
each other; they are not end-to-end encryption against the server operator. To
validate a real installation and measure it under concurrent clients, see
[docs/testing.md](docs/testing.md) and
[docs/performance-testing.md](docs/performance-testing.md).

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
**Caption model**: `florence-community/Florence-2-large` (can be disabled with `--caption-model none`)  
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
python scripts/check_deps.py        # installed dependencies match the lock
ruff check core routers scripts tests server.py
python -m compileall -q core routers scripts tests server.py
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
