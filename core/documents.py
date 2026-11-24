"""Which photos are documents, and of what kind.

People photograph their ID card, a bill to pay, a payment receipt, a doctor's
prescription, and then cannot find them among thousands of pictures. This
sorts them out without any new processing: it compares the CLIP embedding each
item already has with short descriptions of each kind of document, and reads
the OCR text the indexer already extracted for the cues that settle a kind
(a CPF, "CARTEIRA NACIONAL DE HABILITAÇÃO", a boleto's digit line).

An item counts as a document only when it looks more like a document than like
any of the usual non-documents a phone fills with -- memes, screenshots of
posts and chats, people, food -- because text on a picture is not enough: a
meme has text too.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DocumentKind:
    key: str
    title: str
    # English: CLIP was trained on English captions.
    prompts: tuple[str, ...]
    # On accent-free, upper-case OCR text. One hit settles the kind.
    patterns: tuple[str, ...] = ()


KINDS: tuple[DocumentKind, ...] = (
    DocumentKind(
        "identidade",
        "Identidade",
        (
            "a photo of a national identity card",
            "a photo of a driver's license card",
            "a photo of a passport page with a portrait",
            "an id card with a portrait photo and personal data",
        ),
        (
            r"CARTEIRA DE IDENTIDADE",
            r"CARTEIRA NACIONAL DE HABILITACAO",
            r"REGISTRO GERAL",
            r"REPUBLICA FEDERATIVA DO BRASIL.*(IDENTIDADE|HABILITACAO|PASSAPORTE)",
        ),
    ),
    DocumentKind(
        "boletos",
        "Boletos e contas",
        (
            "a photo of a bank payment slip with a barcode",
            "a photo of a utility bill",
            "a printed bill with a barcode and a due date",
        ),
        (
            r"\d{5}\.?\d{5} ?\d{5}\.?\d{6} ?\d{5}\.?\d{6} ?\d ?\d{14}",
            r"LINHA DIGITAVEL",
            r"\bBENEFICIARIO\b.*\bVENCIMENTO\b|\bVENCIMENTO\b.*\bBENEFICIARIO\b",
        ),
    ),
    DocumentKind(
        "comprovantes",
        "Comprovantes",
        (
            "a screenshot of a bank transfer receipt",
            "a payment confirmation receipt",
            "a screenshot of a banking app showing a completed payment",
        ),
        (
            r"COMPROVANTE DE (PAGAMENTO|TRANSFERENCIA|PIX)",
            r"\bCOMPROVANTE\b.*\bPIX\b|\bPIX\b.*\bCOMPROVANTE\b",
            r"TRANSFERENCIA (REALIZADA|EFETUADA|ENVIADA)",
        ),
    ),
    DocumentKind(
        "notas",
        "Notas fiscais",
        (
            "a printed store receipt",
            "a photo of an invoice",
            "a long thermal paper receipt from a shop",
        ),
        (
            r"NOTA FISCAL",
            r"\bNFC?-?E\b",
            r"CUPOM FISCAL",
            r"\bDANFE\b",
        ),
    ),
    DocumentKind(
        "saude",
        "Saúde",
        (
            "a medical prescription on paper",
            "a printed medical exam result",
            "a doctor's note on a clinic letterhead",
        ),
        (
            r"\bRECEITUARIO\b",
            r"\bCRM\b.{0,12}\d{4,}",
            r"RESULTADO DE EXAME|LAUDO (MEDICO|DE EXAME)|HEMOGRAMA",
        ),
    ),
    DocumentKind(
        "outros",
        "Outros documentos",
        (
            "a photo of a paper document",
            "a scanned printed page with text",
            "a filled paper form",
            "a contract with a signature",
        ),
    ),
)

# What a phone is otherwise full of. A document must look more like a document
# than like any of these, which keeps memes and screenshots of posts out.
CONTRAST_PROMPTS: tuple[str, ...] = (
    "a meme with a caption",
    "a screenshot of a social media post",
    "a screenshot of a chat conversation",
    "a screenshot of a video",
    "a photo of people",
    "a selfie",
    "a photo of food",
    "a landscape photo",
    "a cartoon or anime drawing",
    "a photo of a pet",
    "a portrait photo of a person",
    "an emoji or an icon",
    "a logo on a plain background",
)

# The lowest similarity a document prompt must reach at all, so that a picture
# unlike everything (a black frame) is not called a document by elimination.
MIN_SCORE = 0.17
# How much more a document prompt must score than the best contrast. Measured
# on a real library: at 0.01 portraits and icons slipped in; at 0.02, with
# readable text required as well, the picks were camera shots of paper and
# screenshots of apps.
MARGIN = 0.02
# A document has text on it. Requiring some OCR text keeps out what merely
# looks formal -- a portrait, an emoji, a logo -- which the image alone let in.
MIN_TEXT = 20

_BY_KEY = {kind.key: kind for kind in KINDS}
_COMPILED = [(kind.key, [re.compile(p) for p in kind.patterns]) for kind in KINDS]


def kind_titles() -> list[tuple[str, str]]:
    return [(kind.key, kind.title) for kind in KINDS]


def title_of(key: str) -> str:
    return _BY_KEY[key].title


def _plain(text: str) -> str:
    """Upper case without accents, which is how OCR text is compared."""
    decomposed = unicodedata.normalize("NFD", text.upper())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def kind_from_text(text: str | None) -> str | None:
    """The kind the OCR text settles, or None when it settles none."""
    if not text:
        return None
    plain = " ".join(_plain(text).split())
    for key, patterns in _COMPILED:
        if any(p.search(plain) for p in patterns):
            return key
    return None


@dataclass(frozen=True)
class PromptMatrix:
    """Every prompt's embedding, row by row, and which kind each row stands for
    (None for a contrast prompt)."""

    vectors: np.ndarray
    owners: tuple[str | None, ...]


def build_prompts(encode: Callable[[str], np.ndarray]) -> PromptMatrix:
    rows: list[np.ndarray] = []
    owners: list[str | None] = []
    for kind in KINDS:
        for prompt in kind.prompts:
            rows.append(np.asarray(encode(prompt), dtype=np.float32).reshape(-1))
            owners.append(kind.key)
    for prompt in CONTRAST_PROMPTS:
        rows.append(np.asarray(encode(prompt), dtype=np.float32).reshape(-1))
        owners.append(None)
    vectors = np.vstack(rows)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-12)
    return PromptMatrix(vectors, tuple(owners))


def classify(
    image_matrix: np.ndarray | None,
    present: np.ndarray | None,
    prompts: PromptMatrix | None,
    texts: dict[int, str],
) -> dict[int, str]:
    """Position → document kind, for every position that is a document.

    ``texts`` holds each position's OCR text. Its cues are specific enough to
    settle a kind on their own, whatever the picture looks like -- a Pix
    receipt is a screenshot to the eye. For the rest that carry some text, the
    image decides: a
    embedding row (where ``present``; any scale) that looks more like a
    document than like any contrast is one, of the kind of its closest prompt.
    """
    result: dict[int, str] = {}
    for position, text in texts.items():
        kind = kind_from_text(text)
        if kind is not None:
            result[position] = kind
    if image_matrix is None or prompts is None or not len(image_matrix):
        return result
    owners = np.array([o if o is not None else "" for o in prompts.owners])
    is_doc = owners != ""
    # Cosine without a normalised copy of the catalogue: divide by row norms.
    norms = np.linalg.norm(image_matrix, axis=1)
    norms[norms == 0] = 1.0
    scores = (image_matrix @ prompts.vectors.T) / norms[:, None]
    doc_scores = np.where(is_doc, scores, -np.inf)
    best_doc = doc_scores.max(axis=1)
    best_contrast = np.where(is_doc, -np.inf, scores).max(axis=1)
    mask = (best_doc >= MIN_SCORE) & (best_doc - best_contrast >= MARGIN)
    if present is not None:
        mask &= present
    readable = np.zeros(len(mask), dtype=bool)
    for position, text in texts.items():
        if 0 <= position < len(readable) and len(text.strip()) >= MIN_TEXT:
            readable[position] = True
    mask &= readable
    closest = doc_scores.argmax(axis=1)
    for position in np.flatnonzero(mask).tolist():
        result.setdefault(position, str(owners[closest[position]]))
    return result
