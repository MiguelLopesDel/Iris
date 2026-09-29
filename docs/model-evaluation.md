# Avaliação de modelos do pipeline Iris (setembro/2026)

> Nota de manutenção (2026-09-23): as referências abaixo ao pin
> `transformers==4.44.2` descrevem o estado no momento da avaliação, não a
> instalação atual. O perfil CPU agora fixa `transformers==5.17.0`; consulte
> [perfis de dependências](dependency-profiles.md) antes de executar uma troca
> de modelo ou interpretar essas recomendações como trabalho ainda pendente.

Documento técnico de decisão sobre os modelos usados na indexação e na busca do Iris.
Escrito a partir da leitura do código atual (`core/indexer.py`, `core/search_engine.py`,
`core/taxonomy.py`, `core/faces.py`, `requirements.txt`) e de fontes primárias (model cards
do Hugging Face, papers no arXiv, repositórios oficiais, documentação do `transformers`).

**Restrição de hardware assumida:** RTX 4050 Laptop, 6 GB de VRAM. Os modelos rodam em
sequência na indexação, mas o servidor de busca mantém o CLIP residente. Existe o teto
`_MAX_DIM = 1024` (`core/indexer.py:1336`) para evitar OOM.

**Acervo assumido:** ~17 mil itens. Qualquer troca do modelo de *embedding* obriga a
recomputar embeddings e recriar os índices FAISS.

---

## 1. Resumo executivo — recomendações por custo-benefício

| # | Ação | Ganho esperado | Custo | Risco |
|---|---|---|---|---|
| 1 | **Corrigir o uso do Florence-2**: trocar `<VQA>` por `<OD>` + `<DENSE_REGION_CAPTION>` e manter `<MORE_DETAILED_CAPTION>` | Elimina os 73% de descrições contaminadas; tags viram rótulos reais em vez de eco de prompt | Poucas linhas em `describe_image()`; reprocessar só as linhas contaminadas | Baixo |
| 2 | **Migrar o Florence-2 para a implementação nativa do `transformers`** (`florence-community/Florence-2-large`, `Florence2ForConditionalGeneration`) | Elimina `trust_remote_code=True` e destrava a atualização do `transformers` (hoje pinado em 4.44.2) | Troca de checkpoint e de classe; sem reindexação | Baixo |
| 3 | **Parar de usar o encoder de texto do CLIP para `desc_embedding`** | O CLIP trunca em 77 tokens (efetivos ~20); hoje boa parte da descrição+OCR é jogada fora silenciosamente | Um índice FAISS novo de texto, recomputável **só a partir do SQLite** (não relê mídia) | Médio |
| 4 | **Trocar EasyOCR por PP-OCRv5 via RapidOCR (ONNX Runtime)** | EasyOCR está sem release desde 2024-09; PP-OCRv5 latino relata 84,7% de acurácia; ONNX Runtime já é dependência (InsightFace) | Troca de biblioteca; reprocessar OCR do acervo se quiser o ganho retroativo | Médio |
| 5 | **Avaliar SigLIP 2 (`so400m-patch14-384`) como substituto do `clip-ViT-L-14`** | Melhor retrieval e classificação zero-shot, e é multilíngue (resolve consultas em português sem tradução) | **Reindexação completa** + recalibração de todos os limiares da taxonomia | Alto |
| 6 | **NÃO consolidar tudo num único VLM** | — | — | — |

O item 5 é o único que exige reindexar as 17 mil imagens. Os itens 1–4 não exigem.

---

## 2. Pergunta 1 — Vale trocar o Florence-2?

### 2.1 O bug atual está confirmado por fonte primária

O código chama, para cada imagem (`core/indexer.py:1468-1487`):

```python
visual = run_florence_task(image, "<MORE_DETAILED_CAPTION>", ...)
tags   = run_florence_task(image, "<VQA>What is the category of this meme ...", task="<VQA>")
```

O model card oficial do `microsoft/Florence-2-large` lista as tarefas suportadas e
**`<VQA>` não está entre elas**: `<CAPTION>`, `<DETAILED_CAPTION>`,
`<MORE_DETAILED_CAPTION>`, `<OD>`, `<DENSE_REGION_CAPTION>`, `<REGION_PROPOSAL>`, `<OCR>`,
`<OCR_WITH_REGION>`, `<CAPTION_TO_PHRASE_GROUNDING>`
([model card](https://huggingface.co/microsoft/Florence-2-large)).

O blog oficial do Hugging Face sobre fine-tuning do Florence-2 é explícito: os autores
relatam que o Florence-2 *pode* fazer VQA, mas **os modelos liberados não incluem a
capacidade de VQA** — é preciso fazer fine-tuning (é exatamente o que o post faz, gerando o
`Florence-2-DocVQA`)
([huggingface.co/blog/finetune-florence2](https://huggingface.co/blog/finetune-florence2)).

A documentação nova do `transformers` repete a lista de tarefas suportadas
(`<CAPTION>`, `<DETAILED_CAPTION>`, `<MORE_DETAILED_CAPTION>`, `<OD>`,
`<DENSE_REGION_CAPTION>`, `<CAPTION_TO_PHRASE_GROUNDING>`) — de novo, sem `<VQA>`
([docs/transformers/model_doc/florence2](https://huggingface.co/docs/transformers/main/model_doc/florence2)).

Ou seja: o eco do prompt e os tokens `<loc_*>` são o comportamento esperado de um prompt
fora do vocabulário de tarefas. **Não é bug do modelo; é uso incorreto.** Além disso, o
`post_process_generation(..., task="<VQA>")` não tem parser para essa tarefa, então devolve
o texto cru.

### 2.2 Recomendação: consertar antes de trocar

Antes de considerar qualquer VLM novo, o baseline honesto precisa ser o Florence-2 **usado
como projetado**:

- **Legenda**: manter `<MORE_DETAILED_CAPTION>`, ou testar `<DETAILED_CAPTION>` (mais curto,
  menos propenso a alucinar detalhe, e mais barato em tokens gerados). Vale medir os dois.
- **Tags**: usar `<OD>` (detecção de objetos, devolve `labels` + `bboxes`) e/ou
  `<DENSE_REGION_CAPTION>`. O `post_process_generation` tem parser para ambas e devolve um
  dict — os `labels` são exatamente o tipo de lista de palavras-chave que a coluna `tags`
  quer. Custo: `<OD>` gera poucos tokens, é mais barato que a chamada `<VQA>` atual com
  `max_new_tokens=512, num_beams=3`.
- **Categoria** ("reaction, comic, photo, art"): isto **não** é trabalho de VLM aqui — já
  existe classificação zero-shot por CLIP em `core/taxonomy.py`. Adicionar quatro
  `TaxonomyLabel` no campo `style` resolve sem uma segunda passada generativa. É mais
  barato e mais consistente com o resto do pipeline.

Impacto na migração: as linhas contaminadas precisam de reprocessamento do Florence-2 (relê
a mídia), mas **não** precisam recomputar o embedding de imagem. O `desc_embedding` precisa
ser recomputado porque deriva de `tags` (`core/indexer.py:874-882`) — mas isso é feito a
partir do texto no SQLite, sem reler arquivo.

### 2.3 Migrar para o Florence-2 nativo do `transformers` (recomendado)

O Florence-2 foi integrado nativamente ao `transformers` (PR "Add support for Florence-2",
mesclado em 2025-08-20). Verifiquei diretamente nas tags do repositório: o arquivo
`src/transformers/models/florence2/modeling_florence2.py` **não existe em v4.55.0 e existe a
partir de v4.56.0**. Os pesos convertidos estão em
[`florence-community/Florence-2-large`](https://huggingface.co/florence-community/Florence-2-large)
(~319 mil downloads no mês da consulta).

Consequências práticas:

- **Acaba o `trust_remote_code=True`.** Passa a ser
  `Florence2ForConditionalGeneration.from_pretrained("florence-community/Florence-2-large")`.
  Para um projeto self-hosted que o usuário pode distribuir, remover execução de código
  remoto arbitrário na primeira execução é um ganho de segurança direto.
- **Destrava o `transformers`.** O `requirements.txt` pina `transformers==4.44.2`, quase
  certamente porque o código remoto do Florence-2 quebrou com versões novas (há relatos de
  incompatibilidade do Florence-2 com `transformers` 4.50+, p.ex.
  [vladmandic/sdnext#4244](https://github.com/vladmandic/sdnext/issues/4244) — fonte
  secundária, mas consistente com o pin). Com o modelo nativo, o pin deixa de ser
  necessário. Isso importa porque SigLIP 2 exige `transformers` ≥ 4.49 e Qwen3-VL exige
  ≥ 4.57: **hoje o pin em 4.44.2 bloqueia sozinho quase toda alternativa deste documento.**
- Licença permanece **MIT**, 0,77B parâmetros (~1,6 GB em fp16).

### 2.4 Alternativas de VLM que cabem em 6 GB

| Modelo | Params | Licença | `trust_remote_code` | VRAM estimada (bf16) | Observação |
|---|---|---|---|---|---|
| [Florence-2-large](https://huggingface.co/microsoft/Florence-2-large) (atual) | 0,77B | MIT | Não, na versão `florence-community` | ~1,6 GB | Seq2seq, não conversacional; determinístico e barato |
| [Qwen3-VL-2B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) | 2B | **Apache-2.0** | Não (exige `transformers` ≥ 4.57) | ~4,2 GB de pesos + KV/ativações | OCRBench 86,9; OCR em 32 idiomas; segue instrução de formato de tag |
| [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) | 4B | **Apache-2.0** | Não | ~8 GB bf16 → **não cabe** sem quantização (GGUF/FP8 oficiais existem) | Melhor qualidade, mas quantizado o comportamento muda e não achei benchmark de legendagem quantizada |
| [moondream3-preview](https://huggingface.co/moondream/moondream3-preview) | 9B MoE (2B ativos) | **Business Source License 1.1** | Sim | ~18 GB de pesos | Descartado: pesos não cabem em 6 GB e a licença não é OSI |
| InternVL3.5 / MiniCPM-V 4.5 / Gemma 3 4B | 2–8B | variadas (Gemma tem licença própria, não-OSI) | varia | 4–9 GB | Não encontrei comparação direta com Florence-2 em legendagem de memes; não avaliei a fundo |

**Diferença estrutural que importa:** o Florence-2 é *seq2seq com vocabulário de tarefas
fechado* — dá saída curta, determinística e parseável. Um VLM instruído (Qwen3-VL) é
*conversacional* — dá saída mais rica e mais precisa em texto embutido na imagem, mas exige
prompt engineering, parsing tolerante a falha, e um guard-rail contra recusa/divagação. Para
um pipeline batch de 17 mil itens, isso é custo de manutenção real.

**Veredito da pergunta 1:** não troque agora. Conserte o uso (`<OD>`/`<DENSE_REGION_CAPTION>`
para tags), migre para o checkpoint nativo, e **meça** a qualidade em uma amostra do acervo.
Se, depois de corrigido, a legenda ainda for insuficiente, o candidato de troca é o
**Qwen3-VL-2B-Instruct** (Apache-2.0, cabe em 6 GB sozinho, forte em texto dentro da imagem,
que é justamente o caso de memes). Não encontrei nenhum benchmark público comparando
Florence-2 e Qwen3-VL-2B em legendagem de imagens com texto sobreposto em português — essa
decisão vai ter que ser tomada com avaliação própria numa amostra do acervo.

---

## 3. Pergunta 2 — `clip-ViT-L-14` ainda é a melhor escolha para retrieval?

### 3.1 Onde o modelo atual é usado

O `clip-ViT-L-14` (768-d) tem **quatro** papéis no Iris — qualquer troca atinge os quatro:

1. Embedding de imagem → índice `_image.faiss` (`core/indexer.py:860-871`).
2. Embedding de **texto de descrição** → índice `_desc.faiss` (`core/indexer.py:874-882`).
3. Embedding de vídeo: média de 6 frames (`_compute_video_multi_frame_embedding`).
4. Classificação zero-shot da taxonomia, com **limiares absolutos de cosseno** (0,18–0,19)
   em `core/taxonomy.py`.
5. Gate de deduplicação por similaridade CLIP (`dedup.nearest_clip`) — com limiar próprio.

### 3.2 O baseline: o que o CLIP L/14 entrega

A documentação do Sentence Transformers reporta **75,4%** de top-1 zero-shot no ImageNet
para `sentence-transformers/clip-ViT-L-14`
([pretrained_models.md](https://github.com/UKPLab/sentence-transformers/blob/master/docs/sentence_transformer/pretrained_models.md)).

O MIEB (Massive Image Embedding Benchmark, 130 tarefas / 38 idiomas) coloca o OpenAI CLIP
ViT-L/14 como sólido em retrieval mas explicitamente fraco em tarefas multilíngues, e
aponta que **nenhum modelo domina todas as categorias**
([arXiv:2504.10471](https://arxiv.org/abs/2504.10471)). No MIEB v1 os melhores em
image-text retrieval any-to-any são `CLIP-ViT-bigG-laion2B` e `siglip-so400m-patch14-384`.
⚠️ O SigLIP **2** não aparece nessa tabela — o MIEB é anterior ao lançamento dele.

### 3.3 Candidatos

| Modelo | Dim | Params | Licença | `trust_remote_code` | Multilíngue | Reindexação |
|---|---|---|---|---|---|---|
| `clip-ViT-L-14` (atual) | 768 | 428M | MIT (OpenAI CLIP) | Não | ❌ só inglês | — |
| [`google/siglip2-so400m-patch14-384`](https://huggingface.co/google/siglip2-so400m-patch14-384) | 1152 | ~1B (visão+texto) | **Apache-2.0** | Não (`transformers` ≥ 4.49) | ✅ | **Sim, completa** |
| [`google/siglip2-large-patch16-256`](https://huggingface.co/google/siglip2-large-patch16-256) | 1024 | ~303M (torre visual) | Apache-2.0 | Não | ✅ | Sim, completa |
| [`facebook/PE-Core-L14-336`](https://huggingface.co/facebook/PE-Core-L14-336) | 1024 | 0,32B + 0,31B | Apache-2.0 | Sim, na prática (pacote `perception_models`, não é nativo do `transformers`) | parcial | Sim, completa |
| [`facebook/metaclip-2-worldwide-*`](https://huggingface.co/facebook/metaclip-2-worldwide-giant-378) | — | H/14 e bigG | **CC-BY-NC-4.0** | Não | ✅ | Sim, completa |
| [`jinaai/jina-clip-v2`](https://huggingface.co/jinaai/jina-clip-v2) | 1024 (Matryoshka até 64) | ~0,9B | **CC-BY-NC-4.0** | Sim | ✅ 89 idiomas | Sim, completa |
| [`Qwen/Qwen3-VL-Embedding-2B`](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B) | até 2048 | 2B | Apache-2.0 | Não | ✅ 30+ idiomas | Sim, completa |

**Eliminados por licença:** MetaCLIP 2 e jina-clip-v2 são **CC-BY-NC-4.0**. Para um projeto
que o usuário quer poder distribuir, isso é bloqueante — cláusula de uso não-comercial em
pesos embarcados no produto. (O jina-clip-v2 tem licenciamento comercial via API/marketplace
da Jina, o que não ajuda um deploy local.)

**Eliminado por integração:** PE-Core exige o pacote `perception_models` do Meta com API
própria (`pe.CLIP.from_config(...)`), não é nativo do `transformers` nem do
`sentence-transformers`. Números são fortes (ImageNet zero-shot **83,5%**, COCO T→I **57,1%**
no model card do `PE-Core-L14-336`), mas o acoplamento a um repositório de pesquisa é risco
de manutenção para um projeto self-hosted.

**Eliminado por custo em runtime:** `Qwen3-VL-Embedding-2B` é carregável direto pelo
`sentence-transformers` (`SentenceTransformer("Qwen/Qwen3-VL-Embedding-2B")`, MMEB-V2 73,2),
mas é um VLM de 2B com necessidade declarada de ~6–8 GB de VRAM. Manter isso residente no
servidor de busca, em placa de 6 GB, não sobra memória para mais nada. Descarto para este
hardware.

**O candidato real é o SigLIP 2.** No paper ([arXiv:2502.14786](https://arxiv.org/abs/2502.14786),
[HTML](https://arxiv.org/html/2502.14786v1)):

- ImageNet-1k zero-shot: B/16@256 **79,1**; L/16@256 **82,5**; So400m/16@256 **83,4** — contra
  75,4 do CLIP L/14.
- COCO recall@1: L/16@256 → T→I **54,7**, I→T **71,5**.
- Flickr30k: L/16@256 → T→I **72,1**, I→T **81,7**.
- Crossmodal-3600 (36 idiomas, inclui português): So/16@256 → T→I **48,1**, I→T **84,4**.
- Licença dos checkpoints no Hub: **apache-2.0**.

O ganho **multilíngue** é o argumento mais forte para este projeto especificamente: hoje o
`core/indexer.py` chama `translate_text()` (deep-translator) para produzir `ocr_en`, porque o
CLIP só entende inglês. Um encoder multilíngue torna essa muleta desnecessária e faz busca em
português funcionar nativamente.

### 3.4 O custo de migração, honestamente

1. **Reindexação de imagem obrigatória.** Os 17 mil embeddings de imagem precisam ser
   recalculados — relendo cada arquivo, e para vídeo reamostrando 6 frames. Não existe hoje
   um modo "re-embed only": `--rebuild-faiss-only` só reconstrói o FAISS a partir dos BLOBs
   já salvos. **Seria preciso escrever um script de re-embedding** que pula OCR, Florence,
   Whisper e taxonomia. Isso é o que torna a migração viável (minutos-horas de GPU em vez de
   dias), e é pré-requisito da decisão, não detalhe.
2. **Mudança de dimensão** 768 → 1152 (ou 1024). O `_validate_dimension`
   (`core/search_engine.py:792`) já detecta descasamento e erra explicitamente — bom. A
   coluna `model_name` já existe em `memes` (`core/indexer_db.py:159`), então dá para migrar
   incrementalmente e saber quais linhas já foram convertidas. Armazenamento: 17k × 1152 × 4B
   ≈ 78 MB, irrelevante.
3. **Recalibração obrigatória da taxonomia.** Os limiares de `core/taxonomy.py` (0,18/0,19)
   são cossenos absolutos ajustados para a distribuição do CLIP. SigLIP foi treinado com
   perda sigmoide par-a-par, não com softmax/InfoNCE — a distribuição de similaridade é
   diferente e **esses limiares não transferem**. O mesmo vale para o limiar do gate de
   dedup por CLIP. Sem recalibração, a taxonomia vai passar a marcar tudo ou nada.
4. **Custo de inferência maior.** `so400m-patch14-384` a 384px com patch 14 → 729 tokens
   visuais, contra 256 tokens do CLIP L/14 a 224px. É mais lento por imagem, tanto na
   indexação quanto na consulta, e ocupa ~2 GB residentes em fp16 contra ~0,9 GB. Numa placa
   de 6 GB compartilhada com o resto do pipeline, isso não é grátis. Se a folga apertar, o
   `siglip2-large-patch16-256` (1024-d) é o meio-termo.
5. **Perda de integração com `sentence-transformers`.** A documentação do ST lista suporte a
   CLIP e a modelos VLM novos via o módulo `Transformer` unificado, mas **não lista o SigLIP 2**.
   Na prática o carregamento vira `AutoModel`/`AutoProcessor` do `transformers`, e o método
   `.encode()` usado hoje em `core/indexer.py` e `core/search_engine.py` teria de ser
   encapsulado atrás de uma interface própria. É refactor pequeno, mas real.

### 3.5 O problema que ninguém notou: truncamento em 77 tokens

Independente de trocar ou não o CLIP, há um bug silencioso de qualidade em
`core/indexer.py:874-882`:

```python
text_inputs = [
    f"Meme Category/Tags: {item['tags']}. Text: {item['ocr_en']}. Context: {item['visual']}"
    for item in batch_metadata
]
desc_embeddings = models.clip_model.encode(text_inputs, ...)
```

O encoder de texto do CLIP tem `max_position_embeddings = 77` — confirmei no
`config.json` do próprio `sentence-transformers/clip-ViT-L-14`. O paper do Long-CLIP mostra
empiricamente que o **comprimento efetivo é ainda menor, abaixo de ~20 tokens**
([Long-CLIP, ECCV 2024](https://www.ecva.net/papers/eccv_2024/papers_ECCV/papers/06793.pdf)).

Uma `<MORE_DETAILED_CAPTION>` do Florence-2 somada ao OCR de um meme passa de 77 tokens com
folga. **O prefixo literal `"Meme Category/Tags: "` já consome tokens do orçamento**, e o
`Context:` (a legenda) fica frequentemente fora por completo. O índice `_desc.faiss` está,
hoje, indexando pouco mais que as primeiras tags.

E isso **não se resolve migrando para SigLIP 2**: a torre de texto do SigLIP 2 tem
`max_position_embeddings = 64` por padrão (verifiquei em
[`configuration_siglip2.py`](https://raw.githubusercontent.com/huggingface/transformers/v4.57.0/src/transformers/models/siglip2/configuration_siglip2.py)) —
é **pior** que o CLIP nesse aspecto.

**Recomendação separada (item 3 do resumo):** dividir os papéis. O encoder contrastivo
(CLIP/SigLIP) cuida de imagem↔consulta-curta; a descrição longa vai para um encoder de texto
dedicado, multilíngue e de contexto longo — p. ex.
[`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) (**Apache-2.0**,
100+ idiomas, carrega direto no `sentence-transformers`) ou `intfloat/multilingual-e5-large`
(MIT). Vantagem decisiva de migração: **os textos já estão no SQLite** (`descricao_ia`,
`tags`, `texto_extraido`), então esse índice é reconstruível sem tocar em um único arquivo de
mídia. É a melhoria de retrieval com a melhor razão ganho/custo do documento, e é
independente da decisão sobre o CLIP.

### 3.6 Veredito da pergunta 2

O `clip-ViT-L-14` **não** é mais o estado da arte: o SigLIP 2 o supera em toda a linha e é
Apache-2.0. Mas "não é o melhor" ≠ "vale trocar agora". A ordem racional é:

1. Consertar o `desc_embedding` (§3.5) — barato, sem reler mídia, ganho grande.
2. Escrever o script de re-embedding isolado (necessário de qualquer forma).
3. **Medir** com o `scripts/evaluate_golden_set.py` que já existe: rodar o golden set
   com CLIP e com SigLIP 2 num índice de amostra (`build_sample_index.py`). O projeto já tem
   a infraestrutura de avaliação; usar ela é melhor que confiar em ImageNet zero-shot, que
   não é a tarefa do Iris.
4. Só migrar o acervo inteiro se o golden set melhorar de fato, e sabendo que a taxonomia
   precisa ser recalibrada junto.

---

## 4. Pergunta 3 — EasyOCR ainda se justifica?

### 4.1 Estado de manutenção

Consultei a API do PyPI diretamente: a última release do `easyocr` é a **1.7.2, de
2024-09-24** — exatamente a versão pinada no `requirements.txt`. Ou seja, **dois anos sem
release** na data deste documento. Há atividade de PRs no repositório, mas nada publicado.
Existe um fork/continuação `easyocr2` (1.0.1, 2025-12-28, Apache-2.0), mantido por terceiros
— maturidade insuficiente para adotar num projeto que o usuário quer distribuir.

Comparação, mesma fonte (PyPI): `paddleocr` está na **3.7.0 (2026-06-11)**, Apache-2.0, com
releases a cada poucas semanas. `rapidocr` está na **3.9.2 (2026-07-21)**.

### 4.2 Qualidade

A documentação oficial do PaddleOCR traz a tabela de reconhecimento multilíngue do PP-OCRv5
([PP-OCRv5_multi_languages.en.md](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.en.md)):

| Modelo | Acurácia | Ganho sobre PP-OCRv3 |
|---|---|---|
| `latin_PP-OCRv5_mobile_rec` (inclui **português**) | **84,7%** | +46,8% |
| `en_PP-OCRv5_mobile_rec` | 85,25% | +11,0% |

O PP-OCRv5 cobre 106 idiomas e o modelo latino agrupa português, espanhol, francês, alemão,
italiano etc. — um único modelo para o caso `["pt", "en"]` do Iris, em vez de dois conjuntos
de pesos.

⚠️ **Honestidade sobre a evidência:** *não encontrei nenhum benchmark público head-to-head
entre EasyOCR e PP-OCRv5 em texto de cena/meme em português.* Os números acima são do próprio
time do PaddleOCR, em conjunto de teste próprio — são autorrelato, não comparação
independente. O argumento forte a favor da troca é a **manutenção**, não uma diferença de
acurácia comprovada.

### 4.3 Como trocar sem herdar o PaddlePaddle

O ponto que decide: o `paddleocr` traz a dependência do framework `paddlepaddle`, pesada e com
histórico ruim de conflito de versões de CUDA. O
[**RapidOCR**](https://github.com/RapidAI/RapidOCR) (Apache-2.0) roda os mesmos modelos PP-OCR
sobre **ONNX Runtime**, OpenVINO, MNN, PaddlePaddle, TensorRT ou PyTorch.

O Iris **já depende de `onnxruntime==1.20.1`** por causa do InsightFace
(`requirements.txt:31`, `core/faces.py`). Adotar RapidOCR + modelos ONNX PP-OCRv5:

- não adiciona framework novo;
- reaproveita a mesma lógica de escolha de provider já existente em `FaceDetector._providers()`;
- é Apache-2.0, sem `trust_remote_code`;
- modelos "mobile" são pequenos (dezenas de MB) — economiza VRAM em relação ao EasyOCR, que
  carrega CRAFT + CRNN em PyTorch na mesma GPU disputada pelo Florence-2 e pelo CLIP.

⚠️ Confirmei a licença e os backends no README do RapidOCR, mas **não consegui confirmar na
fonte primária quais pesos multilíngues PP-OCRv5 já estão empacotados/convertidos** para o
RapidOCR na versão atual — pode ser necessário converter o `latin_PP-OCRv5_mobile_rec` para
ONNX manualmente. Verificar isso é pré-requisito da adoção.

### 4.4 Alternativas descartadas

- **Surya** — excelente qualidade (83,3% no olmOCR-bench, melhor sub-3B segundo fontes
  secundárias), mas o código é **GPL-3.0** e os pesos usam **AI Pubs RAIL-M**, com uso
  comercial restrito ([LICENSE](https://github.com/datalab-to/surya/blob/master/LICENSE)).
  Incompatível com um projeto que se quer distribuível.
- **OCR via VLM** (Qwen3-VL, PaddleOCR-VL) — ver §5. Qualidade alta (OCRBench 86,9 no
  Qwen3-VL-2B) mas custo por item e VRAM muito acima de um OCR especializado.
- **Florence-2 `<OCR>`** — está disponível "de graça" (modelo já carregado) e vale testar como
  *complemento*, mas o Florence-2 é fraco em texto denso e não foi treinado com foco em
  português. Não substitui um OCR dedicado.

**Veredito:** trocar, sim, mas pela razão certa — o EasyOCR está parado desde 2024 e é a única
dependência importante do pipeline sem manutenção ativa. Destino recomendado: **RapidOCR +
PP-OCRv5 latino sobre ONNX Runtime**. Migração: só o campo `texto_extraido` (e derivados
`ocr_en`, `ocr_normalized`, `desc_embedding`) precisa de reprocessamento; o embedding de
imagem não muda.

---

## 5. Pergunta 4 — Consolidar num único VLM ou manter especialistas?

### 5.1 O que seria consolidado

Em tese, um Qwen3-VL-2B faria numa passada: legenda + tags + categoria + OCR (86,9 em
OCRBench, 32 idiomas). Sobrariam como especialistas: CLIP/SigLIP (embedding — um VLM
generativo **não** produz vetor de retrieval), Whisper (áudio), CLAP (áudio), InsightFace
(rostos).

Ou seja, a consolidação máxima realista funde **três** estágios (Florence-2 + EasyOCR +
categoria) e mantém **quatro** modelos. Não é "um modelo só" — é 5 modelos em vez de 7.

### 5.2 Trade-off real

**A favor:**
- Menos um modelo residente durante a indexação → alívio de VRAM na sequência OCR→legenda.
- OCR e legenda no mesmo passe: o modelo lê o texto *em contexto*, o que para meme é
  qualitativamente melhor (entende que "o texto é a piada").
- Uma única dependência para manter, em vez de EasyOCR (parado) + Florence-2 (código remoto).

**Contra — e é o que decide:**

1. **Custo por item.** O Florence-2 é seq2seq; o `<OD>` gera dezenas de tokens. Um VLM
   instruído gera centenas de tokens autorregressivos por imagem, com KV cache. Em 17 mil
   itens, uma diferença de 2 s/item são ~9,5 horas a mais de indexação. ⚠️ *Não medi isso
   nesta máquina — é estimativa aritmética, não benchmark.* Medir numa amostra de 100 itens
   (`scripts/sample_media.py` já faz o sorteio) resolve a dúvida em minutos.
2. **OCR especializado ainda ganha em custo.** O PP-OCRv5 mobile é um detector + reconhecedor
   de dezenas de MB rodando em ONNX; o Qwen3-VL-2B são ~4,2 GB de pesos. Para extrair texto
   de 17 mil imagens, a diferença de throughput é de ordem de grandeza. A documentação do
   PaddleOCR chega a afirmar que o PP-OCRv5 supera VLMs generalistas (Gemini 2.5 Pro,
   Qwen2.5-VL, GPT-4o) em benchmarks de OCR — ⚠️ autorrelato do vendor, tratar com ceticismo,
   mas a direção é plausível e consistente com o consenso de que modelos especializados
   ganham em tarefa estreita.
3. **Determinismo e parsing.** Vocabulário de tarefas fechado + `post_process_generation`
   devolve estrutura. Um VLM instruído devolve prosa: precisa de parser tolerante, retry,
   detecção de recusa e de repetição. Para um pipeline batch não supervisionado de 17 mil
   itens, isso é onde o tempo de manutenção vai.
4. **Ponto único de falha.** Hoje, se o Florence-2 falha, o OCR e o embedding continuam
   (`run_florence_task` já devolve string de erro sem derrubar o item). Consolidado, uma
   falha derruba três campos de uma vez.
5. **6 GB é pouco para conviver.** O servidor de busca mantém o CLIP residente. Um VLM de
   2B (~4,2 GB) + CLIP (~0,9 GB) + ativações já estoura os 6 GB. Consolidar só funciona
   mantendo a separação estrita indexação/servidor que já existe — e aumenta o custo de
   qualquer futuro "reindexar enquanto o servidor roda".

### 5.3 Veredito

**Manter modelos especializados.** A arquitetura atual — um modelo por competência, cada um
atrás de um seam injetável (o padrão que `core/faces.py` já adota com `FaceDetector` /
`set_detector`) — é a decisão certa para 6 GB de VRAM e um pipeline batch.

A consolidação que **vale** é bem menor e não envolve VLM nenhum: **matar a chamada `<VQA>`**
e mover a categorização para a taxonomia CLIP zero-shot que já existe. Isso remove uma
inferência generativa inteira por imagem, sem adicionar modelo algum.

Se, no futuro, houver placa com mais VRAM, a experiência a fazer é um estágio opcional
"enriquecimento com VLM" — em vez de substituição — reprocessando só os itens onde a legenda
do Florence-2 ficou pobre. Isso encaixa no padrão de seam que o projeto já usa em
`core/web_enrichment.py`.

---

## 6. O que NÃO vale a pena mudar (e por quê)

**Whisper `tiny` como padrão.** Não foi objeto da pesquisa e não encontrei razão para mexer.
Vale registrar apenas que o `whisper` da OpenAI está em modo manutenção e que existem
alternativas mais rápidas (`faster-whisper`/CTranslate2) — mas com `tiny` (39M params) o
custo já é desprezível e o `whisper.load_audio` é usado também pelo caminho do CLAP
(`core/indexer.py:1243`), então trocar arrastaria mais do que parece. **Fora do escopo desta
avaliação; não recomendo mexer sem uma razão concreta.**

**InsightFace `buffalo_l` (SCRFD + ArcFace).** Continua sendo o padrão de facto para
reconhecimento facial local: roda em ONNX Runtime (mesma dependência já presente), embeddings
512-d, e o projeto já isolou o detector atrás de um seam testável. Não encontrei substituto
que justifique a troca em 6 GB de VRAM. **Manter.**

**CLAP `laion/clap-htsat-unfused` + Chromaprint.** O espaço de embedding de áudio tem bem
menos rotatividade que o de imagem, e a combinação embedding semântico + fingerprint exato
cobre busca e dedup com pouca sobreposição. Não pesquisei alternativas a fundo. **Manter.**

**FAISS `IndexFlatIP`.** Com 17 mil vetores, busca exata por produto interno é
instantânea e não tem parâmetro para errar. Trocar por HNSW/IVF só introduziria recall
aproximado e tuning sem ganho perceptível nessa escala. **Manter.**

**A média de 6 frames CLIP para vídeo.** É uma heurística simples com justificativa
documentada no próprio código (robusta a meio-do-vídeo escuro, a cópias aparadas e a
re-encodes). Não achei nada que a supere sem custo desproporcional. **Manter** — e notar que
se o encoder de imagem mudar, esse caminho reindexará junto, com o custo de 6 decodificações
por vídeo.

**O `_MAX_DIM = 1024`.** É a razão de o pipeline não dar OOM. Se o SigLIP 2 384px entrar, esse
teto continua correto (o processor redimensiona para 384 de qualquer forma). **Manter.**

---

## 7. Lacunas de evidência — o que não consegui verificar

Registrado explicitamente para não passar estimativa por medição:

1. **Nenhum benchmark head-to-head EasyOCR × PP-OCRv5 em texto de cena em português.** Os
   números do PP-OCRv5 são autorrelato do time do PaddleOCR.
2. **Nenhuma comparação pública Florence-2 × Qwen3-VL-2B em legendagem de memes/imagens com
   texto sobreposto.** Os benchmarks disponíveis (OCRBench, DocVQA, MMMU) medem outra coisa.
3. **Nenhuma medição de throughput nesta GPU.** Todos os números de tempo por item neste
   documento são aritmética a partir de contagem de parâmetros e tokens gerados, não medição.
4. **SigLIP 2 não aparece na tabela do MIEB** (o benchmark é anterior). A comparação
   SigLIP 2 × CLIP vem do paper do próprio SigLIP 2 — logo, do vendor.
5. **Não confirmei quais pesos PP-OCRv5 multilíngues já vêm convertidos para ONNX no
   RapidOCR** na versão atual.
6. **Não avaliei a fundo InternVL3.5, MiniCPM-V 4.5 e Gemma 3 4B** — apareceram em listas
   secundárias, não em fonte primária comparável, e Gemma tem licença própria não-OSI.
7. **Não medi o impacto real do truncamento em 77 tokens** no `_desc.faiss` deste acervo. A
   limitação é fato verificado no `config.json`; o *tamanho do prejuízo* nas 17 mil linhas,
   não. Dá para medir contando tokens dos `text_inputs` reconstruídos a partir do SQLite,
   sem GPU.
