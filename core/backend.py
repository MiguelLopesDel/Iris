from abc import ABC, abstractmethod
from typing import Any

from PIL import Image

import core.concepts as concepts
from core.duplicates import DuplicateGroup, find_duplicate_groups
from core.search_engine import IrisEngine
from core.search_types import IndexRecord, SearchOptions, SearchResult


def create_backend(
    db_path: str | None = None,
    model_name: str = "sentence-transformers/clip-ViT-L-14",
    media_root: str | None = None,
    device: str | None = None,
    load_model: bool = True,
) -> "SearchBackend":
    """Factory: returns the default local SearchBackend implementation.

    The frontend only depends on SearchBackend, never on concrete classes.
    To swap backends, change this one function — nothing else needs to change.

    Set load_model=False for tests that only need DB operations (CRUD, collections, concepts).
    """
    return LocalBackend(db_path=db_path, model_name=model_name, media_root=media_root, device=device, load_model=load_model)


class SearchBackend(ABC):
    """Abstract interface for the Iris search backend."""
    
    @property
    @abstractmethod
    def has_audio_support(self) -> bool: pass
    
    @property
    @abstractmethod
    def weights(self) -> dict[str, float]: pass
    
    @abstractmethod
    def get_record(self, idx: int) -> IndexRecord | None: pass

    @abstractmethod
    def get_all_records(self) -> list[IndexRecord]: pass

    @abstractmethod
    def get_total_records(self) -> int: pass

    # --- Search ---
    @abstractmethod
    def search_text(self, query: str, options: SearchOptions) -> list[SearchResult]: pass

    @abstractmethod
    def search_image(self, image: Image.Image, options: SearchOptions) -> list[SearchResult]: pass

    @abstractmethod
    def search_similar(self, idx: int, options: SearchOptions) -> list[SearchResult]: pass
    
    @abstractmethod
    def search_audio_text(self, query: str, top_k: int) -> list[SearchResult]: pass

    @abstractmethod
    def random_results(self, n: int) -> list[SearchResult]: pass

    # --- Duplicates ---
    @abstractmethod
    def find_duplicate_groups(self, threshold: float, max_neighbors: int) -> list[DuplicateGroup]: pass

    # --- Collections ---
    @abstractmethod
    def list_collections(self) -> list[dict]: pass

    @abstractmethod
    def create_collection(self, name: str) -> None: pass

    @abstractmethod
    def rename_collection(self, collection_id: int, new_name: str) -> None: pass

    @abstractmethod
    def delete_collection(self, collection_id: int) -> None: pass

    @abstractmethod
    def get_record_collections(self, db_id: int) -> list[dict]: pass

    @abstractmethod
    def add_records_to_collection(self, db_ids: list[int], collection_id: int) -> int: pass

    @abstractmethod
    def remove_records_from_collection(self, db_ids: list[int], collection_id: int) -> int: pass

    @abstractmethod
    def get_collection_members(self, collection_id: int) -> list[int]: pass

    @abstractmethod
    def get_collection_db_ids(self, collection_ids: frozenset[int]) -> frozenset[int]: pass

    # --- Concepts ---
    @abstractmethod
    def has_concept_tables(self) -> bool: pass

    @abstractmethod
    def list_concepts(self) -> list[dict]: pass

    @abstractmethod
    def create_concept(self, name: str, category: str, description: str, search_terms: str, auto_threshold: float = 0.65) -> int: pass

    @abstractmethod
    def update_concept(self, concept_id: int, **kwargs) -> None: pass

    @abstractmethod
    def delete_concept(self, concept_id: int) -> None: pass

    @abstractmethod
    def get_confirmed_meme_ids(self, concept_id: int) -> set[int]: pass

    @abstractmethod
    def get_references(self, concept_id: int) -> list[dict]: pass

    @abstractmethod
    def add_reference(self, concept_id: int, emb_bytes: bytes, thumb_bytes: bytes, file_path: str) -> None: pass

    @abstractmethod
    def delete_reference(self, reference_id: int) -> None: pass

    @abstractmethod
    def set_media_confirmed(self, concept_id: int, db_ids: list[int]) -> None: pass

    @abstractmethod
    def set_media_rejected(self, concept_id: int, db_ids: list[int]) -> None: pass

    @abstractmethod
    def get_media_concepts(self, db_id: int) -> list[dict]: pass

    @abstractmethod
    def get_concept_db_ids(self, concept_ids: frozenset[int]) -> frozenset[int]: pass

    @abstractmethod
    def find_concept_matches(self, concept_id: int, top_k: int = 80, min_score: float = 0.65) -> list[tuple[int, float]]: pass

    @abstractmethod
    def encode_image(self, img: Image.Image) -> Any: pass

class LocalBackend(SearchBackend):
    def __init__(self, db_path=None, model_name="sentence-transformers/clip-ViT-L-14", media_root=None, device=None, load_model=True):
        self.engine = IrisEngine(db_path=db_path, model_name=model_name, media_root=media_root, device=device, load_model=load_model)

    @property
    def has_audio_support(self) -> bool:
        return self.engine.audio_index is not None

    @property
    def weights(self) -> dict[str, float]:
        return self.engine.weights

    def get_record(self, idx: int) -> IndexRecord | None:
        if 0 <= idx < len(self.engine.records):
            return self.engine.records[idx]
        return None

    def get_all_records(self) -> list[IndexRecord]:
        return self.engine.records

    def get_total_records(self) -> int:
        return len(self.engine.records)

    def search_text(self, query: str, options: SearchOptions) -> list[SearchResult]:
        return self.engine.search_text(query, options)

    def search_image(self, image: Image.Image, options: SearchOptions) -> list[SearchResult]:
        return self.engine.search_image(image, options)

    def search_similar(self, idx: int, options: SearchOptions) -> list[SearchResult]:
        return self.engine.search_similar(idx, options)

    def search_audio_text(self, query: str, top_k: int) -> list[SearchResult]:
        return self.engine.search_audio_text(query, top_k)

    def random_results(self, n: int) -> list[SearchResult]:
        return self.engine.random_results(n)

    def find_duplicate_groups(self, threshold: float, max_neighbors: int) -> list[DuplicateGroup]:
        return find_duplicate_groups(self.engine, threshold=threshold, max_neighbors=max_neighbors)

    def list_collections(self) -> list[dict]:
        return self.engine.list_collections()

    def create_collection(self, name: str) -> None:
        self.engine.create_collection(name)

    def rename_collection(self, collection_id: int, new_name: str) -> None:
        self.engine.rename_collection(collection_id, new_name)

    def delete_collection(self, collection_id: int) -> None:
        self.engine.delete_collection(collection_id)

    def get_record_collections(self, db_id: int) -> list[dict]:
        return self.engine.get_record_collections(db_id)

    def add_records_to_collection(self, db_ids: list[int], collection_id: int) -> int:
        return self.engine.add_records_to_collection(db_ids, collection_id)

    def remove_records_from_collection(self, db_ids: list[int], collection_id: int) -> int:
        return self.engine.remove_records_from_collection(db_ids, collection_id)

    def get_collection_members(self, collection_id: int) -> list[int]:
        conn = self.engine.db.get_connection()
        try:
            import sqlite3
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT meme_id FROM media_collections WHERE collection_id = ?", (collection_id,)
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            pass

    def get_collection_db_ids(self, collection_ids: frozenset[int]) -> frozenset[int]:
        return self.engine.db.get_collection_db_ids(collection_ids)

    def has_concept_tables(self) -> bool:
        return self.engine._has_concept_tables()

    def list_concepts(self) -> list[dict]:
        conn = self.engine.db.get_connection()
        try:
            return concepts.list_concepts(conn)
        finally:
            pass

    def create_concept(self, name: str, category: str, description: str, search_terms: str, auto_threshold: float = 0.65) -> int:
        conn = self.engine.db.get_connection()
        return concepts.create_concept(
            conn, name,
            description=description,
            category=category,
            search_terms=search_terms,
            auto_threshold=auto_threshold,
        )

    def update_concept(self, concept_id: int, **kwargs) -> None:
        conn = self.engine.db.get_connection()
        concepts.update_concept(conn, concept_id, **kwargs)

    def delete_concept(self, concept_id: int) -> None:
        conn = self.engine.db.get_connection()
        concepts.delete_concept(conn, concept_id)

    def get_confirmed_meme_ids(self, concept_id: int) -> set[int]:
        conn = self.engine.db.get_connection()
        return concepts.get_confirmed_meme_ids(conn, concept_id)

    def get_references(self, concept_id: int) -> list[dict]:
        conn = self.engine.db.get_connection()
        return concepts.get_references(conn, concept_id)

    def add_reference(self, concept_id: int, emb_bytes: bytes, thumb_bytes: bytes, file_path: str) -> None:
        conn = self.engine.db.get_connection()
        
        
        concepts.add_reference(conn, concept_id, emb_bytes, thumb_bytes, file_path)

    def delete_reference(self, reference_id: int) -> None:
        conn = self.engine.db.get_connection()
        concepts.delete_reference(conn, reference_id)

    def set_media_confirmed(self, concept_id: int, db_ids: list[int]) -> None:
        conn = self.engine.db.get_connection()
        concepts.set_media_confirmed(conn, concept_id, db_ids)

    def set_media_rejected(self, concept_id: int, db_ids: list[int]) -> None:
        conn = self.engine.db.get_connection()
        concepts.set_media_rejected(conn, concept_id, db_ids)

    def get_media_concepts(self, db_id: int) -> list[dict]:
        conn = self.engine.db.get_connection()
        return concepts.get_media_concepts(conn, db_id)

    def get_concept_db_ids(self, concept_ids: frozenset[int]) -> frozenset[int]:
        conn = self.engine.db.get_connection()
        return concepts.get_concept_meme_ids_for_filter(conn, concept_ids)

    def find_concept_matches(self, concept_id: int, top_k: int = 80, min_score: float = 0.65) -> list[tuple[int, float]]:
        return self.engine.find_concept_matches(concept_id, top_k, min_score)

    def encode_image(self, img: Image.Image) -> Any:
        return self.engine.encode_image(img)
