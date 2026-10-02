# Lições técnicas aprendidas em campo

Regras que saíram de erro repetido, não de teoria. Cada uma custou várias
tentativas erradas antes de a forma certa aparecer, e todas têm consequência
presa em teste: este documento preserva a lição, os testes impedem o código de
esquecê-la.

## 1. Ausência não se representa com um valor do domínio

Um item sem embedding de descrição precisava de um score. A busca foi por qual
valor usar, e nenhum servia:

| tentativa | o que deu errado |
|---|---|
| `None` para a matriz inteira | um item sem descrição desligava a busca por descrição do **catálogo todo** |
| linha de zeros | cosseno é assinado: `0.0` vence qualquer score negativo. Medido, o item sem descrição ficou em 2º contra uma consulta oposta ao acervo |
| sentinela `-1.0` | é o mínimo do cosseno, então **empata** com um item legitimamente oposto |
| sentinela `-2.0` | sobrevive à combinação `b·I + (1−b)·D`: vira penalidade de 1,0 com `b=0.5` e de 0 com `b=1.0` — arbitrária, escalada por um peso que significa outra coisa |

A forma certa não era um número melhor. Era tirar a ausência da aritmética:

```
S = Σ(wᵢ · vᵢ · sᵢ) / Σ(wᵢ · vᵢ)     vᵢ = 1 se o sinal existe
```

Sem sinal disponível com peso → **sem score**, não um score construído a partir
de um sinal que a consulta mandou ignorar.

> Regra: quando um valor pode faltar, decida no ponto de uso, não escolhendo um
> valor que o represente. Se um valor sentinela sobrevive a uma operação
> posterior, ele deixou de ser sentinela e virou dado.

Testes: `tests/test_search_engine.py::WeightedSignalMeanTests`.

## 2. Memória mede-se pelo que nunca foi alocado

O custo de manter um catálogo aberto foi medido errado três vezes seguidas:

| erro | efeito |
|---|---|
| RSS medido antes de importar `core.indexer` | 1.198 MB de torch/transformers/faiss atribuídos aos itens — reportou 75 KB/item onde eram 9 |
| `RSS / N` num único ponto | sem separar custo fixo de marginal; deu 29,5 KB/item contra 23,1 reais |
| campos anulados **depois** de carregar | economia medida: **zero**. Liberar não devolve memória ao SO, o alocador mantém a arena |
| vários engines no mesmo processo | subestima, pelo mesmo motivo invertido |

O método que funcionou:

```
processo novo por ponto
+ vários valores de N
+ inclinação (não razão)
+ medir estrutura que nunca é alocada
```

Foi ele que mostrou que a parcela fixa era 3 MB e que os "14 KB/item de overhead
de Python" eram, na verdade, os mesmos embeddings três vezes: `fetchall` com os
blobs, a cópia por registro e a matriz empilhada.

> Regra: `sys.getsizeof` não prova nada (conta só a memória direta do objeto).
> Benchmark de memória exige processo isolado, curva e a estrutura ausente —
> nunca a liberada.

## 3. Clique completo onde o contrato pede componente

A mesma explosão combinatória apareceu **três vezes em camadas diferentes**:
`phash_groups`, as arestas do union-find e a montagem de grupo em
`find_duplicate_groups`. Corrigir a função não basta se o chamador reintroduz.

> Regra: nunca materializar `C(k,2)` quando o contrato exige apenas
> componente/grupo. Com k=5.000 são 12,5 milhões de pares e ~1,25 GB.

## 4. Cache menor que o conjunto de trabalho é pior que cache nenhum

O texto saiu da memória e virou consulta ao SQLite com cache LRU de 4.096
entradas. Uma busca ranqueia o conjunto de candidatos inteiro — 5.225 itens com
o pool padrão. O cache despejava as linhas que estava prestes a receber de volta:
**cada acesso virava um miss**, e a normalização (que o cache existia para
guardar) rodava 14 vezes por candidato. Medido: 503 ms/consulta. Dimensionando o
cache pelo conjunto de candidatos antes de preenchê-lo: 407 ms.

> Regra: o tamanho do cache é uma propriedade do *conjunto de trabalho*, não um
> número redondo. Se uma unidade de trabalho toca N itens, o cache cabe N ou não
> serve para nada. O teto continua existindo — é ele que impede o cache de virar
> o acervo residente de novo.

## 5. Trabalho que não depende do item sai do laço do item

Com o texto do acervo já dobrado e em cache, 88% do tempo de busca ainda era
`normalize_text` — da **consulta**, não do acervo: `_lexical_score` e
`_text_multiplier` normalizavam as mesmas duas ou três palavras uma vez por
candidato, dez a doze chamadas cada. Içar isso para fora do laço levou de 407 ms
para 341 ms, com ranqueamento idêntico bit a bit em 60 consultas.

> Regra: antes de otimizar a estrutura de dados, olhar o que o laço recalcula
> que não depende do que ele itera. É o `O(candidatos)` mais barato de eliminar
> e o mais fácil de não enxergar, porque parece trabalho do item.
