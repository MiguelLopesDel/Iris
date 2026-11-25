"""Translate a search query to English on this machine, without a web service.

A multilingual model such as SigLIP 2 understands Portuguese, but measured on
a real library it still ranked better with an English rendering of the query
beside the Portuguese one: searching with both, averaged, came within a few
points of a hand translation, while a machine translation alone was worse than
the Portuguese (it turns "abraham lincoln" into "open lincoln"). So the
translation here is only ever a second opinion, never a replacement.

The model is small (MarianMT, about 300 MB) and loads on first use; if it
cannot load (offline, missing weights) the query is searched as typed.
"""
from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache

MODEL = "Helsinki-NLP/opus-mt-ROMANCE-en"
_logger = logging.getLogger("iris")
_lock = threading.Lock()
_loaded: tuple | None = None
_failed = False


def enabled() -> bool:
    return os.environ.get("IRIS_LOCAL_QUERY_TRANSLATION", "1").strip().lower() not in {"0", "false", "no"}


def _model():
    global _loaded, _failed
    with _lock:
        if _loaded is None and not _failed:
            try:
                from transformers import MarianMTModel, MarianTokenizer

                tokenizer = MarianTokenizer.from_pretrained(MODEL)
                model = MarianMTModel.from_pretrained(MODEL).eval()
                _loaded = (tokenizer, model)
            except Exception as exc:  # offline, missing sentencepiece, no weights
                _failed = True
                _logger.warning("local query translation unavailable: %s", type(exc).__name__)
        return _loaded


@lru_cache(maxsize=512)
def to_english(text: str) -> str:
    """English rendering of ``text``, or "" if none is available."""
    text = text.strip()
    if not text or not enabled():
        return ""
    loaded = _model()
    if loaded is None:
        return ""
    tokenizer, model = loaded
    import torch

    with _lock, torch.inference_mode():
        batch = tokenizer([text], return_tensors="pt", truncation=True, max_length=64)
        output = model.generate(**batch, max_new_tokens=48, num_beams=4)
    return tokenizer.batch_decode(output, skip_special_tokens=True)[0].strip()


def _unit(vector):
    import numpy as np

    vector = np.asarray(vector, dtype=np.float32).reshape(1, -1)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def encode_multilingual(model, query: str, translate: bool = True):
    """Unit vector for ``query`` with a multilingual model, and the text shown.

    The query as typed, plus its local English rendering when there is one,
    averaged (see the module docstring for why both).
    """
    vectors = [_unit(model.encode(query))]
    english = to_english(query) if translate and query else ""
    if english and english.casefold() != query.casefold():
        vectors.append(_unit(model.encode(english)))
    return _unit(sum(vectors)), english or query
