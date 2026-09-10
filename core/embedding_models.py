"""Embedding encoder selection for CLIP and optional SigLIP 2 checkpoints.

The seam exists because the model families require different loading paths.
Sentence Transformers can load SigLIP 2, but pads text only to the longest item
in each batch. SigLIP 2 requires fixed-length text padding, so that path makes an
embedding depend on the other inputs in its batch. ``SiglipEncoder`` enforces
the checkpoint's fixed text length and keeps indexing deterministic.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Protocol

import numpy as np

DEFAULT_MODEL = "sentence-transformers/clip-ViT-L-14"

# Fallback used by the text composer when an encoder exposes no token limit.
_FALLBACK_TEXT_TOKENS = 77


class EmbeddingEncoder(Protocol):
    """Minimal encoder interface used by indexing and search."""

    tokenizer: Any

    def encode(
        self,
        inputs: Any,
        batch_size: int = 32,
        show_progress_bar: bool = False,
        **kwargs: Any,
    ) -> Any: ...

    def half(self) -> Any: ...


class EncoderFamily(Enum):
    SENTENCE_TRANSFORMER = "sentence_transformer"
    SIGLIP = "siglip"


_SIGLIP_MODEL_PREFIXES = ("google/siglip-", "google/siglip2-")


def resolve_embedding_model(default: str = DEFAULT_MODEL) -> str:
    """Return the deployment override or the supplied model fallback."""
    return (os.environ.get("IRIS_MODEL") or "").strip() or default


def encoder_family(model_name: str) -> EncoderFamily:
    """Resolve supported model families from explicit checkpoint namespaces."""
    normalized = model_name.strip().lower()
    if normalized.startswith(_SIGLIP_MODEL_PREFIXES):
        return EncoderFamily.SIGLIP
    return EncoderFamily.SENTENCE_TRANSFORMER


def is_siglip(model_name: str) -> bool:
    return encoder_family(model_name) is EncoderFamily.SIGLIP


def max_text_tokens(model: object) -> int:
    """Return the encoder's declared text limit or a safe CLIP fallback."""
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
    """SigLIP encoder with the fixed text padding required by the model."""

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
        show_progress_bar: bool = False,  # noqa: ARG002 - interface compatibility
        **_: Any,
    ) -> np.ndarray:
        single = isinstance(inputs, str) or not isinstance(inputs, (list, tuple))
        items = [inputs] if single else list(inputs)
        if not items:
            return np.empty((0, self.get_sentence_embedding_dimension()), dtype=np.float32)

        chunk_size = max(batch_size, 1)
        chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]
        outputs = [self._encode_batch(chunk) for chunk in chunks]
        matrix = np.concatenate(outputs, axis=0)
        return matrix[0] if single else matrix

    def _encode_batch(self, items: list[Any]) -> np.ndarray:
        import torch

        if all(isinstance(item, str) for item in items):
            encoded = self.processor(
                text=items,
                # Fixed length is required even for a one-item batch.
                padding="max_length",
                max_length=self.max_text_tokens,
                truncation=True,
                return_tensors="pt",
            )
            feature_method = self.model.get_text_features
        else:
            encoded = self.processor(images=items, return_tensors="pt")
            feature_method = self.model.get_image_features

        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        if next(self.model.parameters()).dtype == torch.float16:
            encoded = {
                key: (value.half() if value.is_floating_point() else value)
                for key, value in encoded.items()
            }
        with torch.no_grad():
            output = feature_method(**encoded)
        # Transformers 5.x returns a model output object instead of a tensor.
        tensor = getattr(output, "pooler_output", output)
        return tensor.float().cpu().numpy().astype(np.float32)


def load_encoder(
    model_name: str, device: str = "cpu", half: bool = False
) -> EmbeddingEncoder:
    """Load the adapter for the requested checkpoint family."""
    if encoder_family(model_name) is EncoderFamily.SIGLIP:
        encoder = SiglipEncoder(model_name, device=device)
    else:
        from sentence_transformers import SentenceTransformer

        encoder = SentenceTransformer(model_name, device=device)
    if half and device == "cuda":
        encoder.half()
    return encoder
