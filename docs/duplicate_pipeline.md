# Pipeline de Detecção e Limpeza de Duplicatas

## Visão geral

```
Arquivos de mídia
      │
      ▼
┌─────────────────────┐
│  Indexação (CLIP)   │  ← core/indexer.py
│  SHA256 + embedding │
└─────────────────────┘
      │
      ▼
┌─────────────────────┐
│  SQLite + FAISS     │  ← engine.records + engine.image_matrix
└─────────────────────┘
      │
      ▼
┌─────────────────────┐
│  find_duplicate_    │  ← core/duplicates.py
│  groups()           │
│                     │
│  1. Exact hash      │
│  2. FAISS kNN       │
│  3. DSU             │
│  4. Anchor filter   │
│  5. Centroid filter │
│  6. Centroid merge  │
│  7. Media type split│
└─────────────────────┘
      │
      ▼
┌─────────────────────┐
│  Cleanup dialog     │  ← app/ui_duplicates.py
│  (fragment)         │
└─────────────────────┘
      │
      ▼
┌─────────────────────┐
│  move_to_trash +    │  ← core/file_ops.py
│  deleted_registry   │  ← core/deleted_registry.py
└─────────────────────┘
```

---

## 1. Indexação — geração dos embeddings

**Arquivo:** `core/indexer.py`

Cada arquivo de mídia passa por `load_media_preview()` que extrai uma imagem representativa:

| Tipo | Extensões | Imagem para o CLIP |
|---|---|---|
| Imagem estática | `.jpg .png .webp .gif .bmp .tiff` | Abre direto com PIL |
| SVG | `.svg` | Rasteriza para 512×512 via cairosvg |
| Vídeo | `.mp4 .webm .mkv .mov` | Frame do meio (com fallback 1/4, 1/2, 1/8) |
| Áudio puro | `.mp3` | `_audio_placeholder()` — retângulo sólido `(25,25,45)` |
| OGG | `.ogg` | Tenta extrair frame via cv2; se o frame não passar em `_is_meaningful_frame()` (std ≤ 5 ou mean fora de 5–250) → `_audio_placeholder()` |

### `_is_meaningful_frame(frame_bgr)`

OGG OPUS (mensagens de voz) frequentemente reporta `CAP_PROP_FRAME_COUNT > 0` no cv2 mas devolve frames corrompidos ou completamente pretos. Esse check impede que esses frames sejam usados como embedding, forçando o placeholder para todos os arquivos de áudio puro. Resultado: todos os `.ogg` de áudio-só recebem o **mesmo** embedding CLIP e ficam naturalmente no mesmo grupo.

### Embeddings gerados

- **`image_embedding`** — vetor CLIP ViT-L-14, 768 dimensões, `float32`
- **`content_hash`** — SHA256 dos bytes do arquivo; usado para deduplicação exata no banco e no `deleted_registry`

Os embeddings são gravados no SQLite (`memes.image_embedding`) e carregados no FAISS (`engine.image_matrix`) pelo `MemeSearchEngine`.

---

## 2. Detecção de duplicatas

**Arquivo:** `core/duplicates.py` — função `find_duplicate_groups(engine, threshold, max_neighbors)`

### Etapa 1 — Deduplicação exata por hash

```python
exact_hash_groups(engine.records)
```

Agrupa todos os registros com o mesmo `content_hash`. Cada par dentro de um grupo recebe `score = 1.0` e é unido via DSU imediatamente. Não passa pelo FAISS.

### Etapa 2 — Busca de vizinhos (FAISS)

```python
matrix = normalizar_L2(engine.image_matrix)
scores, neighbors = IndexFlatIP.search(matrix, max_neighbors + 1)
```

Para cada registro, busca os `max_neighbors` mais próximos por similaridade cosseno (produto interno após normalização L2). Padrão: `max_neighbors = 50` (ajustável na UI até 100).

**Filtro de tipo de mídia:** antes de unir dois vizinhos no DSU, verifica se pertencem ao mesmo tipo (`_media_type(arquivo)` → `"image"`, `"video"` ou `"audio"`). Pares de tipos diferentes são ignorados, impedindo que um MP4 entre no mesmo grupo que um PNG mesmo que seus embeddings sejam idênticos.

```
_VIDEO_EXTS_DUP  = {.mp4, .webm, .mkv, .mov, .avi, .flv}
_AUDIO_EXTS_DUP  = {.mp3, .ogg, .og, .opus, .flac, .wav, .aac, .m4a}
default          = "image"
```

### Etapa 3 — DSU (Union-Find)

Estrutura `DisjointSet` com path compression e union by rank. Conecta qualquer par `(i, j)` onde `cosine(i, j) >= threshold`. O resultado é um conjunto de componentes conexas — cada componente é um candidato a grupo de duplicatas.

### Etapa 4 — Filtro anchor-based

Para cada componente com ≥ 2 itens:

```
anchor = índice de menor valor numérico (primeiro indexado)
_min_direct = max(threshold - 0.03, 0.90)

para cada item no grupo:
    score_direto = cosine(anchor, item)  # ou pair_scores se disponível
    se score_direto < _min_direct → remove do grupo
```

Remove falsos positivos **diretos** — itens que entraram no grupo por transitividade mas têm similaridade baixa ao anchor.

### Etapa 5 — Filtro centroid-based

Aplicado sobre os sobreviventes do filtro anterior (só para grupos com ≥ 3 itens):

```
centroid = normalizar(média dos embeddings dos membros)

para cada item no grupo:
    se cosine(centroid, item) < _min_direct → remove
```

Mais robusto que o filtro de anchor porque o centroide aponta para o "núcleo denso" do cluster. Um gato que entrou por cadeia transitiva (preto_A → foto_escura → gato) pode ter `cosine(anchor, gato) = 0.960 > 0.955` e passar no filtro de anchor, mas `cosine(centroid_preto, gato) = 0.940 < 0.955` e ser removido aqui.

### Etapa 6 — Mescla por centroide (`_merge_by_centroid`)

Quando `max_neighbors` é finito, um cluster grande pode ser fragmentado em vários grupos menores (ex: 200 imagens pretas viram 3 grupos de ~67 cada). A mescla reconstrói o cluster correto.

```
para cada grupo: calcular centroide (média normalizada de todos os membros)
buscar vizinhos no espaço de centróides com FAISS
threshold_centroide = threshold * 0.97  # = 0.956 para threshold=0.985
se cosine(centroide_A, centroide_B) >= threshold_centroide → mesclar grupos
```

Usar centróides (em vez dos anchors) garante que dois clusters de imagens quase idênticas sejam reconhecidos como o mesmo cluster independente de quais itens foram escolhidos como anchor.

**Caso especial — áudio:** todos os grupos onde **todos** os itens são arquivos de áudio são unidos incondicionalmente. CLIP não tem capacidade semântica para áudio — separar grupos de áudio por similaridade de embedding não faz sentido.

### Etapa 7 — Split por tipo de mídia (`_split_by_media_type`)

Pós-processamento final: qualquer grupo que ainda contenha tipos misturados (ex: PNG + MP4 em dados indexados antes do filtro de tipo ser adicionado) é dividido em subgrupos por tipo. Cada subgrupo precisa de ≥ 2 itens para existir.

### Saída

Lista de `DuplicateGroup`, ordenada por `(len(items), score)` descendente:

```python
@dataclass(frozen=True)
class DuplicateGroup:
    group_id: int
    kind: str           # sempre "exact_or_visual"
    score: float        # min(score_to_anchor) do grupo
    items: list[DuplicateItem]

@dataclass(frozen=True)
class DuplicateItem:
    index: int          # posição em engine.records
    arquivo: str        # nome do arquivo
    resolved_path: str | None
    score_to_anchor: float
```

O **anchor** de cada grupo (o item de menor índice numérico) é o mais antigo no banco. O **original** exibido na UI é escolhido separadamente por `_iter_cleanup_groups` como o item com a **menor data de modificação** (mtime).

---

## 3. UI — Tela de limpeza

**Arquivo:** `app/ui_duplicates.py`

### Estado da sessão

| Chave | Tipo | Conteúdo |
|---|---|---|
| `_cleanup_sel` | `dict[int, bool]` | **Fonte de verdade** das seleções. `True` = marcado para remoção. Nunca deletado por navegação de página. |
| `_cleanup_flat` | `list[dict]` | Estrutura de linhas paginadas (1 linha = 1 chunk de cópias) |
| `_cleanup_grp_info` | `dict[int, dict]` | `{group_id: {orig, copies}}` — para cálculo de melhor qualidade |
| `_cleanup_copy_indices` | `list[int]` | Todos os índices de cópias (para contagem rápida) |
| `_cleanup_b64` | `dict` | Cache de base64 dos thumbnails |
| `_cleanup_mtime` | `dict` | Cache de mtimes formatados |
| `_cleanup_dims` | `dict` | Cache de dimensões (width, height) para badges |

### Por que `_cleanup_sel` e não as widget keys

Streamlit deleta automaticamente as chaves de widget (`cleanup_cb_{idx}`) quando o widget não está renderizado (ao mudar de página). Se a fonte de verdade fosse as widget keys, toda seleção feita numa página anterior seria perdida ao navegar de volta. `_cleanup_sel` é um dict normal de sessão — Streamlit nunca o toca.

**Sincronização:**
```
antes de renderizar:  st.session_state["cleanup_cb_{idx}"] = _cleanup_sel[idx]
on_change callback:   _cleanup_sel[idx] = st.session_state["cleanup_cb_{idx}"]
toggle callbacks:     _unsel_indices / _sel_indices modificam _cleanup_sel diretamente
                      e também atualizam a widget key se ela existir
```

### Layout por linha

**Primeira linha de um grupo:**
```
[col 0: Original]  [col 1: Cópia 1]  ...  [col 5: Cópia 5]
  thumbnail            thumbnail                thumbnail
  🔒 Original           Cópia [↑HD]
  nome • data          nome • data
  ✓ Preservado
  (sem checkbox)        ☑ Remover               ☑ Remover
```

**Linhas subsequentes (sem original):**
```
[col 0: Cópia 6]  [col 1: Cópia 7]  ...  [col 5: Cópia 11]
   thumbnail          thumbnail               thumbnail
   Cópia              Cópia
   nome • data        nome • data
   ☑ Remover          ☑ Remover               ☑ Remover
```

A primeira linha tem 5 cópias (col 0 = original). As linhas seguintes têm 6 cópias (col 0 livre).

### Paginação

`ROWS_PER_PAGE = 20` — pagina por **linhas** (não por grupos). Garante páginas de altura uniforme independente do tamanho dos grupos. Um grupo com 100 cópias ocupa ~17 linhas e se distribui entre páginas.

### Badges de qualidade

| Badge | Condição |
|---|---|
| **↑ ULTRA** (vermelho) | Cópia com maior resolução do grupo E >10% maior que o original |
| **↑ HD** (roxo) | Cópia com resolução >10% maior que o original (mas não é a maior do grupo) |

Calculado por `_best_copy_for_group()` usando `PIL.Image.open(path).size` (só lê o header — rápido). Cacheado em `_cleanup_dims` por toda a sessão do dialog.

---

## 4. Exclusão e registro de deletados

**Arquivo:** `core/deleted_registry.py`

### Fluxo

```
Usuário confirma remoção
        │
        ▼
register_deleted(db_path, file_paths)
  ├── file_sha256(path)          → SHA256 dos bytes
  └── _phash_str(path)           → pHash (imagehash) para imagens
        │ grava em deleted_media
        ▼
move_to_trash(file_paths)
  └── send2trash / gio trash
```

`register_deleted` é chamado **antes** de `move_to_trash` (enquanto os arquivos ainda existem no disco).

### Tabela `deleted_media`

```sql
CREATE TABLE IF NOT EXISTS deleted_media (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT UNIQUE NOT NULL,   -- SHA256
    perceptual_hash TEXT,                -- pHash hex (só imagens)
    original_path TEXT,                  -- referência
    deleted_at   TEXT DEFAULT (datetime('now'))
);
```

### Uso no indexer

No início de `run_indexer()`:

```python
_deleted_hashes = load_deleted_content_hashes(conn)  # set de SHA256
_deleted_phashes = load_deleted_phashes(conn)         # list de imagehash objects

# Para cada arquivo novo:
if content_hash in _deleted_hashes:
    continue   # já deletado antes — pular

if _deleted_phashes and is_phash_deleted(str(source_path), _deleted_phashes):
    continue   # visualmente idêntico a um arquivo já deletado
```

**SHA256** pega cópias bit-a-bit idênticas. **pHash** (threshold 8 bits de Hamming) pega versões recomprimidas ou levemente redimensionadas da mesma imagem.

### `sync_index_after_trash`

Antes de remover registros do `memes`, salva os `content_hash` em `deleted_media`:

```python
rows = conn.execute("SELECT content_hash FROM memes WHERE id IN (...)")
register_deleted_hashes(conn, [r[0] for r in rows])
conn.executemany("DELETE FROM memes WHERE id = ?", ids)
```

Isso garante que arquivos limpos pelo script também fiquem no registro de deletados.

---

## 5. Limitações conhecidas

| Limitação | Causa | Status |
|---|---|---|
| Meme templates com texto diferente agrupados | CLIP vê visual, não lê texto OCR no scoring de duplicatas | Aceitável — limitação do modelo |
| OGG com vídeo real pode ser classificado como áudio | `_is_meaningful_frame` conservador | Raro para coleção de memes |
| pHash não disponível sem `imagehash` instalado | Dependência opcional | Degrada para só SHA256 |
| Re-indexação necessária para OGGs já indexados com frame ruim | Embeddings errados já no banco | Fix parcial via `_merge_by_centroid` na detecção |
| CLIP embeddings para áudio não têm semântica | Placeholder = mesma cor sólida para todos | Todos os áudios agrupados juntos — comportamento intencional |
