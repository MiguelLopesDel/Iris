# Meme Compass

Meme Compass e uma ferramenta local para indexar e buscar memes por texto ou por imagem. Ela combina OCR, transcricao de video, legendas visuais, embeddings CLIP, SQLite e FAISS.

## Requisitos

- Python 3.10+
- Linux com GPU NVIDIA/CUDA recomendado
- 16 GB de RAM ou mais
- Espaco para modelos baixados pelo Hugging Face/Whisper

O fallback CPU existe, mas a indexacao completa fica lenta.

## Instalacao

```bash
./scripts/install.sh
```

Ou manualmente:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Indexar Midias

```bash
source venv/bin/activate
python -m core.indexer --dir media --db meme_compass_v10.db
```

Para indexacao portatil (copiando para biblioteca gerenciada):

```bash
python -m core.indexer --dir media --db meme_compass_v10.db --copy-to-library --library default --library-root data/library
```

Opcoes uteis:

```bash
python -m core.indexer --dir media --recursive --limit 200
python -m core.indexer --db data/meme_compass_v10.db --rebuild-faiss-only
python -m core.indexer --dir media --caption-model none --whisper-model none
```

O banco e os indices sao criados em `data/` quando `--db` recebe um nome relativo.
O schema atual tambem guarda hash, texto normalizado, perfil visual em JSON, estilo,
obra/personagem provavel, contexto e sinais usados para explicar a busca.

## Bibliotecas Portateis

O projeto agora suporta bibliotecas de midia cadastradas no banco (`media_libraries`).
Arquivos podem ser armazenados em `data/library/<nome>`, permitindo mover o projeto com
menos dependencia de caminhos absolutos antigos.

Para migrar uma colecao legada para biblioteca:

```bash
python -m scripts.migrate_library --db data/meme_compass_full_v1.db --media-root media --library default --library-root data/library
```

Para importar novos arquivos/pastas de forma incremental (pula duplicadas por hash):

```bash
python -m scripts.import_media --db data/meme_compass_full_v1.db --source /caminho/pasta --source /caminho/imagem.jpg --library default --library-root data/library --device cuda
```

## Fluxo Por Amostras

Use este fluxo antes de indexar tudo. Ele nao altera `media/` nem os bancos arquivados.

Primeiro confirme que CUDA esta acessivel ao PyTorch. No ambiente atual, isso precisa rodar
fora de qualquer sandbox que esconda o driver NVIDIA:

```bash
python scripts/gpu_probe.py --require-cuda
```

```bash
python scripts/sample_media.py --dir media --sample-size 100 --seed 42 --output data/eval/samples/sample_100.json
python scripts/create_eval_pack.py --manifest data/eval/samples/sample_100.json --output-dir data/eval/packs/sample_100
python scripts/build_sample_index.py --manifest data/eval/samples/sample_100.json --db data/eval/indexes/sample_100.db --caption-model microsoft/Florence-2-large
```

Depois edite `data/eval/packs/sample_100/queries.json` com buscas que um usuario real faria
para encontrar cada imagem. Avalie:

```bash
python scripts/evaluate_search.py --db data/eval/indexes/sample_100.db --queries data/eval/packs/sample_100/queries.json
python scripts/evaluate_captions.py --db data/eval/indexes/sample_100.db
```

Para um teste mais serio de recall, crie um golden set. Cada imagem ganha consultas por
OCR literal, memoria vaga, visual, obra/estilo e contexto da piada:

```bash
python scripts/create_golden_set.py --dir media --sample-size 30 --output-dir data/eval/golden/golden_30
python scripts/create_eval_pack.py --manifest data/eval/golden/golden_30/manifest.json --output-dir data/eval/golden/golden_30/pack
```

Depois edite `data/eval/golden/golden_30/queries.json` olhando a contact sheet e rode:

```bash
python scripts/evaluate_golden_set.py --db data/eval/indexes/sample_100.db --queries data/eval/golden/golden_30/queries.json --top-k 20 --min-recall10 0.90 --min-recall20 0.95
```

Antes de indexar tudo na RTX 4050, descubra o batch seguro:

```bash
python scripts/model_capacity.py --manifest data/eval/samples/sample_100.json --model sentence-transformers/clip-ViT-L-14 --batch-sizes 1,2,4,8,16 --device cuda --require-cuda
```

Para comparar embeddings locais na mesma amostra:

```bash
python scripts/compare_models.py --manifest data/eval/samples/sample_100.json --models sentence-transformers/clip-ViT-L-14 jinaai/jina-clip-v2 --caption-model none
python scripts/model_quality_sweep.py --manifest data/eval/samples/sample_100.json --queries data/eval/golden/golden_30/queries.json --models sentence-transformers/clip-ViT-L-14 jinaai/jina-clip-v2 --caption-model none --device cuda
```

Critério recomendado antes do indice completo: `Recall@10 >= 90%` e `Recall@20 >= 95%`
no golden set de 30; depois repetir com 100 imagens e buscar `Recall@10 >= 92%` e
`Recall@20 >= 97%`.

## Enriquecer Metadados

Se o banco ja foi indexado com `--caption-model none`, rode o enriquecedor para adicionar
taxonomia visual via CLIP sem reindexar imagens:

```bash
python scripts/enrich_metadata.py --db data/meme_compass_full_v1.db --device cuda
```

Ele adiciona classificacoes como `wojak/chudjak`, `youtube/social screenshot`,
`anime/manga`, `discord moderation`, `programming/linux`, `music/playlist` e outras
em `style`, `source_work`, `context`, `humor`, `tags` e `visual_json`.

## Duplicatas

A interface tem uma aba `Duplicatas` que agrupa arquivos duplicados ou quase duplicados
usando hash exato e similaridade visual dos embeddings. Tambem da para gerar relatorio:

```bash
python scripts/find_duplicates.py --db data/meme_compass_full_v1.db --media-root media --threshold 0.985
```

Na interface voce pode marcar varias imagens e usar `Mover selecionadas para lixeira`.
Isso envia os arquivos para a lixeira do sistema (nao usa `rm`).

Depois de remover arquivos fisicos, rode um comando unico para limpar registros quebrados
no banco e recriar os indices:

```bash
python scripts/sync_index_after_trash.py --db meme_compass_v10.db --media-root media
```

## Rodar a Interface

```bash
./scripts/run_app.sh
```

Ou:

```bash
source venv/bin/activate
streamlit run app/main.py
```

Na barra lateral, selecione o banco em `data/`, o modelo CLIP usado para gerar embeddings e a pasta de midias local.

## Benchmark e Pesos

```bash
python scripts/benchmark.py --db data/teste_playground.db --num 20 --seed 42
python scripts/optimize.py --db data/teste_playground.db --output data/best_weights.json
```

O benchmark mede Top 1, Top 3, Top 5 e latencia media. `data/best_weights.json` ajusta o peso visual/conceitual e o bonus de texto usado pela busca.
Os novos scripts em `data/eval/` sao melhores para validar qualidade real, porque usam
consultas esperadas por imagem e relatam Recall@1/5/10, MRR e latencia.

## Artefatos Gerados

Bancos `.db`, indices `.faiss`, logs, JSONs grandes e midias locais sao tratados como artefatos locais e ficam ignorados pelo Git.
Arquivos `.tar.gz` nao sao apagados pelo limpador padrao.

Para ver o que seria removido:

```bash
python scripts/clean_generated.py
```

Para remover:

```bash
python scripts/clean_generated.py --apply
```

## Desenvolvimento

```bash
python -m compileall app core scripts utils
pytest
ruff check .
```

O codigo principal fica em:

- `core/indexer.py`: pipeline de indexacao e criacao FAISS
- `core/search_engine.py`: carregamento de banco, ranking e busca
- `app/main.py`: interface Streamlit
