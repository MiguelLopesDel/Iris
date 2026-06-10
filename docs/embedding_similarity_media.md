# Embedding e Similaridade por Tipo de Mídia

## Situação atual

| Tipo | Embedding gerado | Qualidade para deduplicação |
|---|---|---|
| Imagem | CLIP ViT-L-14 do pixel direto | ✓ Excelente |
| Vídeo | ~~CLIP do frame do meio~~ → **CLIP média de 6 frames** | ✓ Bom (após melhoria) |
| Áudio `.mp3` / `.ogg` puro | Placeholder sólido `(25,25,45)` | ✗ Sem semântica — todos idênticos |
| OGG com vídeo real | CLIP do frame do meio | △ Depende do frame |

---

## Vídeo — embedding multi-frame (implementado)

### Por que um frame só era insuficiente

```
Vídeo original:   [intro_preta] [cena_A] [cena_B] [cena_C] [credits]
Frame do meio:              ↑ cena_B

Cópia trimada:    [cena_A] [cena_B] [cena_C]
Frame do meio:         ↑ cena_A ou cena_B (diferente!)
```

Se o frame do meio cai em posições diferentes → embeddings diferentes → **vídeos idênticos não são detectados como duplicatas**.

Adicionalmente: muitos vídeos de memes têm títulos/intros pretos ou telas escuras no meio. Um frame ruim gera um embedding sem semântica.

### Solução — `_compute_video_multi_frame_embedding`

```
┌─────────────────────────────────────────────────────┐
│  Vídeo (N frames total)                             │
│  [10%]  frame_1  frame_2  frame_3  frame_4  [90%]  │
│         ↓         ↓         ↓         ↓             │
│       CLIP      CLIP      CLIP      CLIP            │
│         └─────────┴─────────┴─────────┘             │
│                       ↓                             │
│                    média                            │
│                       ↓                             │
│               normalizar L2                         │
│                       ↓                             │
│            embedding final (768-dim)                │
└─────────────────────────────────────────────────────┘
```

- Extrai `n_frames = 6` frames espaçados uniformemente
- Descarta os 10% iniciais e finais (fade-in/out, telas pretas, créditos)
- Filtra frames sem conteúdo (`_is_meaningful_frame`: std > 5, mean 5–250)
- CLIP-codifica todos os frames úteis de uma vez (batch)
- Faz média aritmética dos embeddings → normaliza

**Resultado:** vídeos trimados/recodificados têm embeddings muito mais próximos. O threshold de similaridade 0.985 passa a funcionar para:
- Mesma gravação, bitrate diferente
- Clip com intro/outro removidos
- Resolução diferente (480p vs 1080p do mesmo vídeo)

### Integração no batch do indexer

Vídeos **não** entram na codificação em lote com imagens. O fluxo é:

```python
# Para cada arquivo no batch:
if é_video:
    precomp = _compute_video_multi_frame_embedding(path, clip_model)
    batch_images.append(None)           # sentinel para manter índice alinhado
    batch_metadata[-1]["precomp_embedding"] = precomp
else:
    batch_images.append(image)          # PIL normal
    batch_metadata[-1]["precomp_embedding"] = None

# Após o loop do batch:
# 1. Codifica apenas as imagens reais (sem None)
_clip_out = clip_model.encode([img for img in batch_images if img is not None])

# 2. Monta array final mesclando pré-computados com saída do batch
image_embeddings = merge(precomp_or_clip_batch_output)
```

---

## Áudio — o problema e o plano

### Por que o placeholder não funciona como embedding

```
Voz_A.ogg  →  placeholder(25,25,45)  →  CLIP  →  [0.12, -0.34, ...]
Voz_B.ogg  →  placeholder(25,25,45)  →  CLIP  →  [0.12, -0.34, ...]  ← idêntico!
Música.mp3 →  placeholder(25,25,45)  →  CLIP  →  [0.12, -0.34, ...]  ← idêntico!
```

Todos os arquivos de áudio puro têm exatamente o mesmo embedding. Consequências:
- FAISS coloca todos em um único grupo de "duplicatas"
- Não há como distinguir "mesma gravação" de "áudio totalmente diferente"
- A solução atual (unir todos os grupos de áudio incondicionalmente) é um paliativo

### Camada 1 — Chromaprint (deduplicação exata, sem ML)

**O que é:** fingerprinting acústico. Converte o áudio em um array de inteiros que representa o conteúdo acústico. Dois arquivos com o mesmo áudio, mesmo que em containers/bitrates diferentes, produzem fingerprints com distância de Hamming próxima de zero.

**Para que serve:**
- Encontrar cópias exatas e quase-exatas (mensagens de voz reenviadas)
- Mais robusto que SHA256: pega recodificações que SHA256 não pega
- Muito rápido (< 1s por arquivo), sem GPU, sem modelo

**Dependências:**
```bash
# Sistema (Linux):
apt-get install libchromaprint-tools   # instala fpcalc

# Python:
pip install pyacoustid
```

**Como integrar:**

```python
# core/indexer.py — durante indexação:
import acoustid

def _chromaprint(path: Path) -> str | None:
    try:
        duration, fp = acoustid.fingerprint_file(str(path))
        return fp  # string base64
    except Exception:
        return None

# No loop de indexação, para arquivos de áudio:
audio_fingerprint = _chromaprint(path) if suffix in AUDIO_EXTS else None
# Gravar em nova coluna: memes.audio_fingerprint TEXT
```

**Na detecção de duplicatas (`find_duplicate_groups`):**
```python
# Adicionar ao DSU antes do FAISS, similar ao exact_hash_groups:
for índices com mesmo audio_fingerprint (ou Hamming distance < 10):
    dsu.union(i, j)
    pair_scores[(i,j)] = 1.0
```

**Schema SQL necessário:**
```sql
ALTER TABLE memes ADD COLUMN audio_fingerprint TEXT;
CREATE INDEX IF NOT EXISTS idx_audio_fp ON memes(audio_fingerprint)
    WHERE audio_fingerprint IS NOT NULL;
```

**Limitação:** Chromaprint detecta "mesmo áudio", não "áudio similar". Um remix da mesma música não seria detectado.

---

### Camada 2 — CLAP (similaridade semântica de áudio, ML)

**O que é:** CLAP (Contrastive Language-Audio Pretraining) é o equivalente de CLIP para áudio. Gera embeddings de 512 dimensões que capturam o *significado* do som.

```
"uma voz masculina falando"  →  [0.23, -0.11, ...]
voz_masculina_A.ogg          →  [0.21, -0.13, ...]  ← próximos!
voz_masculina_B.ogg          →  [0.24, -0.10, ...]  ← próximos!

"música eletrônica"          →  [0.89,  0.45, ...]  ← longe!
```

**Para que serve:**
- Busca semântica: "encontrar todos os áudios com voz feminina"
- Agrupar gravações do mesmo tipo (música, fala, efeito sonoro)
- Deduplicação de músicas mesmo em versões diferentes

**Dependências:**
```bash
pip install laion_clap
# Modelo download automático (~200MB): laion/larger_clap_music_and_speech
```

**Arquitetura necessária:**

O CLAP gera vetores de 512 dimensões (vs 768 do CLIP para imagens). Precisaria de:

```
engine.image_matrix   [N × 768]  ← imagens e vídeos (CLIP existente)
engine.audio_matrix   [M × 512]  ← áudios (CLAP, novo)
engine.audio_records  [M]        ← subset de engine.records com só áudios
```

```python
# core/search_engine.py — adicionar:
class MemeSearchEngine:
    audio_matrix: np.ndarray | None = None   # novo
    audio_index: faiss.Index | None = None   # novo

# core/indexer.py — ao indexar áudio:
import laion_clap
clap_model = laion_clap.CLAP_Module(...)

audio_embedding = clap_model.get_audio_embedding_from_filelist([str(path)])[0]
# Gravar em nova coluna: memes.audio_embedding BLOB (512 × float32 = 2KB)
```

**Busca por áudio:**
```python
# Na busca por texto:
if query e tem clap_model:
    text_emb_audio = clap_model.get_text_embedding([query])[0]
    audio_scores, audio_ids = engine.audio_index.search(text_emb_audio, top_k)
    # Mesclar com resultados visuais
```

**Schema SQL necessário:**
```sql
ALTER TABLE memes ADD COLUMN audio_embedding BLOB;
-- Índice FAISS separado: data/{db}.audio.faiss
```

---

## Roadmap de implementação

```
✅ Fase 0 (feito)    Placeholder para áudio puro
                     _is_meaningful_frame para OGG
                     Agrupamento incondicional de áudio na detecção

✅ Fase 1 (feito)    Multi-frame embedding para vídeos (6 frames, média CLIP)
                     Bypass do batch para vídeos (precomp_embedding)

🔲 Fase 2            Chromaprint fingerprinting
                     - acoustid como dep. opcional
                     - nova coluna audio_fingerprint em memes
                     - integração em find_duplicate_groups
                     - migration automática de schema

🔲 Fase 3            CLAP audio embeddings
                     - laion_clap como dep. opcional com GPU
                     - novo índice FAISS para áudio (data/*.audio.faiss)
                     - engine.audio_matrix carregado junto com image_matrix
                     - busca híbrida visual+áudio no search_engine.py
                     - UI: resultados de áudio na busca por texto
```

---

## Por que CLIP não serve para áudio

CLIP foi treinado em pares (imagem, texto). Quando damos uma imagem de grade cinza-azulada como input, ele compara com o espaço textual de forma muito arbitrária. A similaridade entre dois placeholders idênticos é 1.0 por construção matemática, não por semântica.

CLAP foi treinado em pares (áudio, texto). A distância entre dois embeddings CLAP reflete a distância semântica entre os sons. É a ferramenta certa para o problema.

Para deduplicação de arquivos de áudio *idênticos* (mesma gravação, containers diferentes), Chromaprint é mais simples, mais rápido e mais preciso que CLAP. Os dois se complementam:
- Chromaprint: "é o mesmo arquivo?"
- CLAP: "é o mesmo tipo de conteúdo?"
