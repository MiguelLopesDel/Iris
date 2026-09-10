# Product consistency proposal

## One user-facing hierarchy

The product should expose four primary objects:

1. **Itens** are the media in the library.
2. **Álbuns** are intentional groups of items.
3. **Pessoas** are face-based groups.
4. **Reconhecimentos ensinados** are visual subjects learned from examples.

Automatic tags, captions, taxonomy fields, embeddings, and scores remain search
signals. They are metadata, not peer navigation categories.

## Naming changes

- Coleções → **Álbuns**
- Conceitos → **Reconhecimentos ensinados**
- Novo conceito → **Ensinar algo ao Iris**
- Categoria do conceito → **Tipo**
- Associações → **Itens reconhecidos**
- Referências → **Imagens de exemplo**
- Auto-match → **Encontrar nas minhas fotos**

Internal API and database names can remain unchanged during the UI migration.

## Search controls

The normal search UI should not expose raw scores.

- Default: **Equilibrada** — current production defaults.
- **Mais precisa** — fewer, stronger results.
- **Mais abrangente** — includes approximate results.

Result count belongs to pagination and should not be a relevance slider. “Visual
vs. conceptual”, lexical weight, translation, numeric thresholds, and candidate
limits belong under a clearly marked diagnostics/developer section.

## Similarity controls

Each workflow needs its own language; a universal “similarity” slider is misleading.

- Image search: **Agrupar variações parecidas** (on/off by default).
- Duplicate review: **Quase idênticos**, **Muito parecidos**, or **Explorar parecidos**.
- Learned recognition: **Cuidadoso**, **Equilibrado**, or **Abrangente**.

Numeric values may be shown in an advanced disclosure for expert tuning, but must
not be the primary control.
