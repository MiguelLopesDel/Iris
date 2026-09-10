"""Escolha do encoder de embedding (CLIP hoje, SigLIP 2 opcional).

Existe um seam aqui porque os dois modelos **não** se carregam do mesmo jeito.
O CLIP funciona pelo ``sentence-transformers``; o SigLIP, não — e falha calado,
que é o pior modo de falhar.

O ``sentence-transformers`` 5.5 até carrega um checkpoint SigLIP 2 e devolve
vetores com a forma certa, mas preenche o lote até a maior sequência dele
(``padding=True``). A torre de texto do SigLIP foi treinada com preenchimento
fixo de 64 tokens, e ela não é invariante a isso. Medido neste repositório, com
``google/siglip2-base-patch16-224``:

* o vetor da mesma consulta muda conforme o que mais está no lote — cosseno
  **0,76** entre "a photo of a person" sozinha e a mesma frase acompanhada de
  uma frase longa;
* contra o caminho correto (``padding="max_length", max_length=64``), o cosseno
  fica em **0,74/0,65**.

Um índice construído assim guarda vetores que dependem da ordem em que os
arquivos foram processados, e a consulta nunca cai no mesmo lugar duas vezes.
Por isso o SigLIP passa por ``SiglipEncoder``, que fixa o preenchimento, em vez
de ir pelo caminho aparentemente mais simples.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

DEFAULT_MODEL = "sentence-transformers/clip-ViT-L-14"

# O limite da torre de texto, usado para orçar o texto indexado. O CLIP declara
# 77 e o SigLIP 2, 64: quem escreve o texto precisa perguntar, não supor.
_FALLBACK_TEXT_TOKENS = 77


def resolve_embedding_model(default: str = DEFAULT_MODEL) -> str:
    """Modelo de embedding configurado, ou o padrão.

    Reaproveita ``IRIS_MODEL``, que o servidor e o docker-compose já usam, em
    vez de criar uma segunda variável para a mesma decisão.

    Trocar isso reescreve o espaço vetorial inteiro: um catálogo indexado com
    CLIP não pode ser consultado com SigLIP. Quem muda a variável precisa
    reindexar, e o guard em ``core/search_engine`` recusa a mistura em vez de
    devolver resultado sem sentido.
    """
    return (os.environ.get("IRIS_MODEL") or "").strip() or default


def is_siglip(model_name: str) -> bool:
    return "siglip" in model_name.lower()


def max_text_tokens(model: object) -> int:
    """Quantos tokens a torre de texto do encoder realmente aceita."""
    declared = getattr(model, "max_text_tokens", None)
    if isinstance(declared, int) and declared > 0:
        return declared
    getter = getattr(model, "get_max_seq_length", None)
    if callable(getter):
        try:
            value = getter()
            if isinstance(value, int) and value > 0:
                return value
        except Exception:
            pass
    return _FALLBACK_TEXT_TOKENS


class SiglipEncoder:
    """Encoder SigLIP com o preenchimento de texto que o modelo exige.

    Expõe a mesma superfície que o resto do código já usa do
    ``SentenceTransformer``: ``encode``, ``half`` e ``tokenizer``.
    """

    def __init__(self, model_name: str, device: str = "cpu"):
        from transformers import AutoConfig, AutoModel, AutoProcessor

        self.model_name = model_name
        self.device = device
        self.processor = AutoProcessor.from_pretrained(model_name)
        self.tokenizer = self.processor.tokenizer
        self.model = AutoModel.from_pretrained(model_name).to(device).eval()
        config = AutoConfig.from_pretrained(model_name)
        self.max_text_tokens = int(
            getattr(config.text_config, "max_position_embeddings", 64) or 64
        )

    def half(self) -> SiglipEncoder:
        self.model = self.model.half()
        return self

    def get_sentence_embedding_dimension(self) -> int:
        return int(self.model.config.text_config.hidden_size)

    def encode(
        self,
        inputs: Any,
        batch_size: int = 32,
        show_progress_bar: bool = False,  # noqa: ARG002 - assinatura do SentenceTransformer
        **_: Any,
    ) -> np.ndarray:
        single = isinstance(inputs, str) or not isinstance(inputs, (list, tuple))
        items = [inputs] if single else list(inputs)
        if not items:
            return np.empty((0, self.get_sentence_embedding_dimension()), dtype=np.float32)

        chunks = [items[i : i + max(batch_size, 1)] for i in range(0, len(items), max(batch_size, 1))]
        saidas = [self._encode_batch(chunk) for chunk in chunks]
        matrix = np.concatenate(saidas, axis=0)
        return matrix[0] if single else matrix

    def _encode_batch(self, items: list[Any]) -> np.ndarray:
        import torch

        if all(isinstance(item, str) for item in items):
            encoded = self.processor(
                text=items,
                # O ponto de todo este módulo: comprimento fixo, sempre.
                padding="max_length",
                max_length=self.max_text_tokens,
                truncation=True,
                return_tensors="pt",
            )
            metodo = self.model.get_text_features
        else:
            encoded = self.processor(images=items, return_tensors="pt")
            metodo = self.model.get_image_features

        encoded = {chave: valor.to(self.device) for chave, valor in encoded.items()}
        if next(self.model.parameters()).dtype == torch.float16:
            encoded = {
                chave: (valor.half() if valor.is_floating_point() else valor)
                for chave, valor in encoded.items()
            }
        with torch.no_grad():
            saida = metodo(**encoded)
        # transformers 5.x devolve um objeto de saída aqui, não um tensor.
        tensor = getattr(saida, "pooler_output", saida)
        return tensor.float().cpu().numpy().astype(np.float32)


def load_encoder(model_name: str, device: str = "cpu", half: bool = False) -> Any:
    """Carrega o encoder certo para o checkpoint pedido."""
    if is_siglip(model_name):
        encoder = SiglipEncoder(model_name, device=device)
    else:
        from sentence_transformers import SentenceTransformer

        encoder = SentenceTransformer(model_name, device=device)
    if half and device == "cuda":
        encoder.half()
    return encoder
