"""Judge a model change on a trial batch, before the whole library moves.

During a migration the new vectors exist only for the items a worker already
processed (``embedding_next``), and search keeps using the current model until
every item has them. This ranks that processed subset with both models for one
query, so a few hundred photos are enough to see whether the new one finds
things better -- and what the new text reading looks like.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

import numpy as np

_encoders: dict[str, object] = {}
_lock = threading.Lock()


def encoder_for(model_name: str):
    """The target model's encoder, loaded once (on CPU: a preview, not search)."""
    from core.embedding_models import load_encoder

    with _lock:
        if model_name not in _encoders:
            _encoders[model_name] = load_encoder(model_name, device="cpu")
        return _encoders[model_name]


@dataclass(frozen=True)
class Hit:
    media_id: int
    score: float
    text: str


@dataclass(frozen=True)
class Preview:
    processed: int
    with_current: int
    new: list[Hit]
    current: list[Hit]


def _rank(ids: list[int], matrix: np.ndarray, query: np.ndarray, texts: dict[int, str], top: int) -> list[Hit]:
    if not ids:
        return []
    matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    scores = matrix @ query.reshape(-1)
    order = np.argsort(-scores)[:top]
    return [Hit(ids[i], float(scores[i]), texts.get(ids[i], "")) for i in order]


def compare(conn: sqlite3.Connection, target: str, new_query: np.ndarray,
            current_query: np.ndarray | None, top: int = 12) -> Preview:
    """Top items of the processed subset under the new and the current model."""
    rows = conn.execute(
        """SELECT n.meme_id, n.embedding, m.embedding, COALESCE(m.texto_extraido, '')
           FROM embedding_next n JOIN memes m ON m.id = n.meme_id
           WHERE n.model_name = ?""",
        (target,),
    ).fetchall()
    texts = {r[0]: r[3] for r in rows}
    new_ids = [r[0] for r in rows]
    new_matrix = (np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                  if rows else np.zeros((0, 1), np.float32))
    old_rows = [r for r in rows if r[2]]
    widths = {len(r[2]) for r in old_rows}
    current: list[Hit] = []
    if current_query is not None and len(widths) == 1 and widths == {current_query.size * 4}:
        old_matrix = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in old_rows])
        current = _rank([r[0] for r in old_rows], old_matrix, current_query, texts, top)
    return Preview(
        processed=len(rows),
        with_current=len(old_rows),
        new=_rank(new_ids, new_matrix, new_query, texts, top),
        current=current,
    )
