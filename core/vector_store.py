from __future__ import annotations

from pathlib import Path
from typing import Any

import faiss
import numpy as np


class VectorStore:
    """Manages FAISS indices for image, description, and audio embeddings."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.image_index, self.desc_index = self._load_faiss_indices()
        self.audio_index = None

    def _load_faiss_indices(self) -> tuple[Any | None, Any | None]:
        prefix = self.db_path.with_suffix("")
        image_path = prefix.with_name(f"{prefix.name}_image.faiss")
        desc_path = prefix.with_name(f"{prefix.name}_desc.faiss")
        image_index = faiss.read_index(str(image_path)) if image_path.exists() else None
        desc_index = faiss.read_index(str(desc_path)) if desc_path.exists() else None
        return image_index, desc_index

    def build_audio_index(self, records: list[Any]) -> tuple[np.ndarray | None, list[int]]:
        """Build in-memory audio FAISS index from CLAP embeddings stored in records."""
        audio_vecs = []
        audio_indices = []
        for i, rec in enumerate(records):
            if rec.audio_embedding is not None and len(rec.audio_embedding) > 0:
                audio_vecs.append(rec.audio_embedding)
                audio_indices.append(i)
        
        if not audio_vecs:
            self.audio_index = None
            return None, []
        
        matrix = np.array(audio_vecs, dtype=np.float32)
        faiss.normalize_L2(matrix)
        idx = faiss.IndexFlatIP(matrix.shape[1])
        idx.add(matrix)
        self.audio_index = idx
        return matrix, audio_indices

    @staticmethod
    def normalize_vector(vector: np.ndarray) -> np.ndarray:
        vector = np.asarray(vector, dtype=np.float32)
        if vector.ndim == 1:
            vector = vector.reshape(1, -1)
        faiss.normalize_L2(vector)
        return vector

    def search_image_index(self, query_embedding: np.ndarray, limit: int) -> list[int]:
        if self.image_index is None:
            return []
        _, indices = self.image_index.search(query_embedding, limit)
        return [idx for idx in indices[0].tolist() if idx >= 0]

    def search_desc_index(self, query_embedding: np.ndarray, limit: int) -> list[int]:
        if self.desc_index is None:
            return []
        _, indices = self.desc_index.search(query_embedding, limit)
        return [idx for idx in indices[0].tolist() if idx >= 0]

    def search_audio_index(self, query_embedding: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
        if self.audio_index is None:
            return np.array([]), np.array([])
        return self.audio_index.search(query_embedding, limit)
