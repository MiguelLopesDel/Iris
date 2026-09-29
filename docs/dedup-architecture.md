# Detecção de duplicatas — arquitetura, garantias e limites

Todo número neste documento foi medido neste repositório, no acervo real, pelo
caminho de produção (`core.indexer._compute_phash`, limiar 8). Onde há projeção
em vez de medição, está escrito.

Reproduzir: `python scripts/evaluate_dedup.py --dir media --sample-size 200`.

## O problema, enunciado com precisão

> Encontrar todos os grupos de imagens quase idênticas num acervo que pode
> chegar a dezenas de milhões de arquivos, num computador doméstico, sem em
> nenhum momento comparar todos os pares.

Duas restrições mudam a resposta e por isso ficam explícitas:

**Um usuário não é uma pessoa.** Uma empresa de edição acumula centenas de
milhares a milhões de arquivos numa única biblioteca, e um usuário comum de
celular chega a dezenas de milhares em poucos anos. Shardar por conta limita
armazenamento, não limita este problema.

**Os dois erros não têm o mesmo preço.** Um falso negativo deixa uma segunda
cópia no disco. Um falso positivo coloca uma foto única na frente de um botão de
apagar. Essa assimetria decide o resto da arquitetura: o índice aproximado só
responde *"vale a pena comparar?"*, e a decisão final é sempre exata.

## O pipeline adotado

```text
                         arquivo
                            │
            ┌───────────────▼───────────────┐
            │ 1. SHA-256 do arquivo         │  idênticos byte a byte
            └───────────────┬───────────────┘  O(bytes), sem falso positivo
                            │
            ┌───────────────▼───────────────┐
            │ 2. ledger (caminho+tam+mtime) │  reimportação já vista
            └───────────────┬───────────────┘  1 stat(), sem ler o arquivo
                            │
            ┌───────────────▼───────────────┐
            │ 3. guarda de baixa estrutura  │  sólidas e quase sólidas saem
            └───────────────┬───────────────┘  aqui, antes do índice
                            │
            ┌───────────────▼───────────────┐
            │ 4. pHash 64 bits              │  fingerprint perceptual
            └───────────────┬───────────────┘
                            │
            ┌───────────────▼───────────────┐
            │ 5. colapso de hashes iguais   │  N iguais → 1 representante
            └───────────────┬───────────────┘  O(N), nunca O(N²)
                            │
            ┌───────────────▼───────────────┐
            │ 6. índice MIH (3 fatias)      │  gera candidatos
            └───────────────┬───────────────┘  pode perder, nunca inventa
                            │
            ┌───────────────▼───────────────┐
            │ 7. XOR + popcount ≤ 8         │  decisão exata
            └───────────────┬───────────────┘
                            │
            ┌───────────────▼───────────────┐
            │ 8. union-find                 │  componentes conexas
            └───────────────┬───────────────┘  arestas de ligação, não todos os pares
                            │
                     grupos sugeridos
                            │
            ┌───────────────▼───────────────┐
            │ 9. revisão humana             │  nada é apagado antes disto
            └───────────────────────────────┘
```

### Etapa 1 — SHA-256 do arquivo

Pega cópia literal. Custo proporcional aos bytes lidos, sem nenhum falso
positivo possível. É a etapa mais barata por duplicata encontrada e por isso vem
primeiro.

### Etapa 2 — ledger de arquivos já vistos

Guarda o desfecho de todo arquivo já examinado, indexado por
`(caminho, tamanho, mtime)`. Uma reimportação da mesma pasta decide com um único
`stat()`, sem ler nem hashear.

**Limite conhecido:** o atalho não olha o desfecho. Um arquivo em quarentena não
é reavaliado em importações futuras — continua pendente na fila de revisão, mas
não ganha segunda chance automática.

### Etapa 3 — guarda de baixa estrutura

Um hash perceptual descreve como o brilho **varia**. Imagem uniforme não varia,
então todas produzem praticamente o mesmo hash. Medido:

| par | distância |
|---|---|
| vermelho sólido × azul sólido | **0** |
| preto × branco | **1** |
| preto × quase preto | 1 |
| cinza médio × vermelho sólido | 0 |

Ou seja, **toda cor sólida colidia com toda outra**. Prints em branco,
placeholders exportados e fundos sólidos são comuns o bastante para tornar isso
rotineiro — e é o erro caro.

Imagens com desvio padrão de luminância abaixo de **3,0** não recebem hash
perceptual. O corte foi medido, não escolhido:

| conjunto | desvio padrão |
|---|---|
| 400 fotos reais, a **menos** detalhada | 6,48 |
| 400 fotos reais, percentil 1 | 22,68 |
| preto com ruído de compressão JPEG | 1,53 |
| qualquer cor sólida | 0,00 |

Em 400 fotos reais, **zero** foram recusadas. Elas continuam deduplicadas pela
etapa 1; o que se perde é oferecer uma sólida como duplicata de outra de cor
diferente — falso negativo, a direção barata.

### Etapa 4 — pHash de 64 bits

`imagehash.phash` sobre a imagem convertida para RGB. Não aplica orientação
EXIF, o que é o comportamento desejado: a tag é metadado, os pixels é que são
comparados.

### Etapa 5 — colapso de hashes idênticos

Antes do índice, posições com o mesmo hash viram um representante. Union-find
precisa de arestas que **conectem** a componente, não de todas as arestas dentro
dela.

| imagens com o mesmo hash | antes | agora |
|---:|---:|---:|
| 5.000 | 12,5 M arestas, 13,3 s | 4.999 arestas, 0,11 s |
| 20.000 | ~200 M arestas (est.) | 19.999 arestas, **0,11 s** |

O tempo é constante: não depende de quantas repetições existem.

### Etapa 6 — índice MIH

Cada hash é fatiado em 3 partes de ~21 bits. Se dois hashes diferem em no máximo
8 bits no total, alguma fatia carrega no máximo `8 // 3 = 2` dessas diferenças —
as diferenças não podem todas exceder sua cota. Sondar raio 2 em cada fatia
portanto **não perde nenhum par**: o recall desta etapa é matematicamente 100%.

### Etapa 7 — decisão exata

`popcount(a XOR b) <= 8`. O índice pode perder candidatos, nunca inventa: um par
só é agrupado se a distância real estiver dentro do limiar.

### Etapas 8 e 9 — agrupamento e revisão

Union-find produz as componentes. **Nenhum caminho do código apaga com base no
índice.** Na importação, uma quase duplicata vai para quarentena com três saídas
explícitas do usuário: `ignorar` (fica no disco), `lixeira` (via `Send2Trash`,
recuperável) e **`importar assim mesmo`**, que reindexa com dedup desligado.

## Garantias medidas, por transformação

120 fotos reais, limiar 8. Recall agregado seria ~72% e esconderia tudo que
importa — por isso está separado.

### Garantido (100% na amostra)

| transformação | distância mediana | p95 | máx |
|---|---:|---:|---:|
| resize 50%, 25%, 200% | 0 | 2 | 2 |
| JPEG q90 / q70 / q40 | 0 | 2 | 2 |
| PNG round-trip | 0 | 0 | 0 |
| WebP q80 | 0 | 2 | 2 |
| brilho +15% / −15% | 2 / 0 | 4 / 2 | 6 / 2 |
| contraste +20% | 0 | 4 | 6 |
| grayscale | 0 | 0 | 0 |
| tag de orientação EXIF | 0 | 2 | 2 |

A folga é confortável: o pior caso garantido fica em 6 contra o limiar 8.

### Parcial

| transformação | recall | observação |
|---|---:|---|
| watermark | **99,6%** (250 fotos) | falhas concentradas em imagens pequenas: <600px cai para 94%, ≥600px é 100% |

### Fora da garantia

| transformação | recall | distância mediana |
|---|---:|---:|
| crop 5% | 34% | 10 |
| borda 5% | 21% | 12 |
| crop 10% | 3% | 20 |
| crop 20% | **0%** | 30 |
| rotação nos pixels | **0%** | 32 |

Isto é limite do método, não defeito de calibração. Um hash de frequência
descreve o quadro inteiro; remover ou girar parte dele muda o hash muito.
**Alargar o limiar para pegar crops começaria a casar imagens não relacionadas**
— trocar o erro barato pelo caro.

Pegar crops e rotações exige outra família de sinal (features locais ou
embeddings), depois dos métodos baratos, nunca antes.

## Falsos positivos medidos

400 fotos reais, 79.800 pares comparados, limiar 8:

| | |
|---|---:|
| pares dentro do limiar | 7 (**0,0088%**) |
| desses, com imagens de fato distintas | **0** |

Os 7 são todos screenshots da mesma interface, com diferença média de pixel
entre 10 e 20 (de 255) — vários tirados com segundos de diferença. Nenhum é um
par de imagens diferentes. Antes da etapa 3, as colisões entre cores sólidas
eram falsos positivos reais.

## Escala — onde isto para de funcionar

O fatiamento reduz os pares examinados; **não muda a ordem de crescimento**. Um
par aleatório vira candidato com probabilidade 2,8×10⁻⁴, então o número de
candidatos continua proporcional ao quadrado do acervo.

Medido, com hashes aleatórios:

| N | candidatos | **candidatos/N** | pares verdadeiros |
|---:|---:|---:|---:|
| 25.000 | 175.601 | 7,0 | 0 |
| 50.000 | 702.466 | **14,0** | 0 |
| 100.000 | 2.812.141 | **28,1** | 2 |
| 200.000 | 11.249.152 | **56,2** | 9 |
| 400.000 | 45.003.834 | **112,5** | 27 |

`candidatos/N` **dobra quando N dobra**. Essa é a curva, independente do que o
relógio diga em N pequeno — e é o diagnóstico a usar antes de esperar um
benchmark grande terminar: plano escala, proporcional é quadrático.

Custo real hoje: 0,10 s no acervo atual (3.636 hashes), 0,86 s a 25 mil,
5,0 s a 100 mil, minutos a um milhão, horas acima disso.

### O teto é o fingerprint, não o índice

Busca exaustiva da melhor configuração de MIH (projeção a partir das
probabilidades, validada contra as medições acima):

| fingerprint | melhor configuração | sondagens/item | candidatos a 50M |
|---|---|---:|---:|
| **64 bits (hoje)** | 3 fatias × 21b, raio 2 | 696 | **414.848.319.340** |
| 256 bits | 7 fatias × 36b, raio 2 | 4.669 | **84.928.614** |
| 256 bits | 6 fatias × 42b, raio 3 | 74.304 | 21.118.467 |

A configuração ótima para 64 bits é exatamente a implementada. **Nenhuma
estrutura de dados resolve 64 bits a 50M** — nem árvore, nem LSH, nem
particionamento adaptativo. O limite é a informação contida na chave.

Um fingerprint de 256 bits dá 4.900× menos candidatos por 6,7× mais sondagem, o
que a 50M são 1,7 candidatos por imagem: efetivamente linear. O motivo não é o
bucket ser mais seletivo por si, é o **limiar relativo cair** — 7,0% dos bits a
256 contra 12,5% a 64 — e o custo do MIH depender de `bits/(k+1)`.

Armazenamento não é obstáculo: 50M × 32 bytes = **1,6 GB** de fingerprints.

### Hipótese testada e descartada

Normalizar a imagem antes de hashear, para encolher o limiar e assim afiar o
índice. `ImageOps.equalize` **piorou**: limiar necessário subiu de 8 para 14 em
64 bits e de 18 para 52 em 256. Equalização de histograma é um remapeamento
não-linear guiado pelo próprio histograma; mudar o brilho muda o remapeamento,
que muda mais do que a mudança original.

## Eixo vertical — a mesma arquitetura em máquinas diferentes

Duas máquinas de referência:

| | **A** | **B** |
|---|---|---|
| RAM | 32–64 GB | 4–8 GB |
| CPU | moderna, instruções vetoriais | modesta, 2015–2020 |
| GPU | RTX 4000/5000 | nenhuma |

**A GPU é irrelevante nestas etapas.** É trabalho inteiro limitado por memória,
não ponto flutuante: XOR e popcount não se beneficiam de forma útil, e o gargalo
é acesso aleatório a memória. A GPU importa no CLIP e nos embeddings, não aqui.
A máquina B não perde por CPU.

**O que separa as duas é residência de memória, e isso é decidido pela estrutura
de dados, não pelo hardware:**

| estrutura | RAM exigida a 50M |
|---|---:|
| dicionário Python, 50M entradas | ~5 GB — inviável em B |
| **array ordenado + busca binária + mmap** | **0** — o SO pagina sob demanda |

Números projetados para 50M com fingerprint de 256 bits: 1,6 GB de fingerprints
e ~3,2 GB de índice (7 fatias), **em disco**. Cabe num HD comum e nunca precisa
estar inteiro em RAM.

A consequência de projeto é dura e não dá para adiar: **nada de dicionário, nada
de índice residente, nada de "carrega tudo e compara"**. Estrutura array-based e
compatível com `mmap` desde o início, porque isso não se retrofita depois.

O mesmo código roda nas duas máquinas. O que muda é tamanho de chunk e
paralelismo — parâmetros, não arquitetura.

> A implementação atual usa dicionários e é adequada ao tamanho de acervo atual.
> A migração para arrays em disco é parte da mesma mudança que leva o
> fingerprint a 256 bits; uma sem a outra não resolve.

## Eixo horizontal — onde o peso é pago

Não é questão de preferência entre "organizar antes" e "processar direto". O
formato do trabalho decide:

- sem índice, cada consulta custa `O(N)`;
- as imagens **chegam ao longo do tempo**, não de uma vez.

Disso segue:

| decisão | por quê |
|---|---|
| pagar na **importação, por item** | ~5 ms por imagem nova (projetado, 256 bits) |
| **nunca** reconstruir o índice inteiro por importação | o custo seria proporcional ao acervo, não à importação |
| backfill de acervo existente é evento único | em chunks, retomável, fora do caminho interativo |
| decisão exata sempre no final | barata por par, e é o que impede o erro caro |

A assimetria que justifica: trabalho de importação é por item, paralelizável e
sem ninguém esperando; trabalho de consulta tem um humano na frente.

## Estado

**Decidido e implementado:** pipeline das etapas 1 a 9, guarda de baixa
estrutura, colapso de hashes iguais, MIH 3×21 raio 2, decisão exata, revisão
humana obrigatória antes de qualquer remoção.

**Decidido e não implementado:** fingerprint de 256 bits com índice de 7 fatias
em arrays `mmap`. É a única mudança que torna dezenas de milhões viáveis, e
exige backfill do acervo existente.

**Em aberto:** cobertura de crops e rotações, que precisa de outra família de
sinal; e se o índice pode ser probabilístico. Para este produto a resposta
provável é sim — duplicata é sugestão revisada por humano, e a decisão final
continua exata, então um índice probabilístico introduz falso negativo mas nunca
falso positivo.
