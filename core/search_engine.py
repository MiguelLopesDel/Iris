from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import torch
from deep_translator import GoogleTranslator
from PIL import Image
from sentence_transformers import SentenceTransformer, util

from core.db_manager import DatabaseManager
from core.search_types import (
    STOP_WORDS,
    IndexRecord,
    SearchOptions,
    SearchResult,
    normalize_text,
    parse_query_terms,
)
from core.vector_store import VectorStore

DEFAULT_MODEL = "sentence-transformers/clip-ViT-L-14"
DEFAULT_WEIGHTS = {"balance": 0.5, "text_bonus": 2.0, "lexical_weight": 0.25}

VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mkv", ".mov", ".ogg"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
AUDIO_EXTENSIONS = frozenset({".mp3"})


class IrisEngine:
    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        model_name: str = DEFAULT_MODEL,
        media_root: str | os.PathLike[str] | None = None,
        weights_path: str | os.PathLike[str] = "data/best_weights.json",
        load_model: bool = True,
        device: str | None = None,
    ):
        self.db_path = Path(db_path or self._default_db_path())
        self.db = DatabaseManager(self.db_path)
        self.vector_store = VectorStore(self.db_path)
        
        self.model_name = model_name
        self.media_root = Path(media_root or ".").resolve()
        self.device = device or self._detect_device()
        self.weights = self._load_weights(Path(weights_path))
        
        self.library_roots = self.db.get_library_roots()
        self.records = self._load_records()
        self.image_matrix = self._stack_embeddings("embedding")
        self.desc_matrix = self._stack_embeddings("desc_embedding")
        
        self.image_index = self.vector_store.image_index
        self.desc_index = self.vector_store.desc_index
        self.audio_matrix, self.audio_record_indices = self.vector_store.build_audio_index(self.records)
        self.audio_index = self.vector_store.audio_index
        
        self._clap_model = None
        self._clap_processor = None
        self.model = self._load_model() if load_model else None

        # Lazy cache for backward-compatible .dados property (only computed on demand)
        self._dados_cache: list[dict[str, Any]] | None = None

    @staticmethod
    def _detect_device() -> str:
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    @staticmethod
    def _default_db_path() -> str:
        if Path("data/teste_playground.db").exists():
            return "data/teste_playground.db"
        return "data/iris.db"

    def _load_model(self) -> SentenceTransformer:
        model = SentenceTransformer(self.model_name, device=self.device)
        if self.device == "cuda":
            model.half()
        return model

    def _load_weights(self, weights_path: Path) -> dict[str, float]:
        if not weights_path.exists():
            return dict(DEFAULT_WEIGHTS)
        try:
            with weights_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            return {
                "balance": float(loaded.get("balance", DEFAULT_WEIGHTS["balance"])),
                "text_bonus": float(
                    loaded.get("text_bonus", DEFAULT_WEIGHTS["text_bonus"])
                ),
                "lexical_weight": float(
                    loaded.get("lexical_weight", DEFAULT_WEIGHTS["lexical_weight"])
                ),
            }
        except (OSError, ValueError, TypeError):
            return dict(DEFAULT_WEIGHTS)

    def _load_records(self) -> list[IndexRecord]:
        if not self.db_path.exists():
            return []

        conn = self.db.get_connection()
        try:
            columns = self.db.table_columns("memes")
            if not columns:
                # DB file exists but was never indexed (no `memes` table yet) —
                # treat as empty instead of crashing on the SELECT below.
                return []
            select_columns = [
                "arquivo",
                "caminho",
                "texto_extraido",
                "descricao_ia",
                "embedding",
                "desc_embedding",
            ]
            if "tags" in columns:
                select_columns.append("tags")
            if "relative_path" in columns:
                select_columns.append("relative_path")
            for optional in [
                "visual_json",
                "objects",
                "style",
                "source_work",
                "humor",
                "context",
                "content_hash",
                "file_size",
                "file_mtime",
                "library_id",
                "storage_path",
                "source_path",
                "audio_fingerprint",
                "audio_embedding",
                "perceptual_hash",
            ]:
                if optional in columns:
                    select_columns.append(optional)

            if "id" in columns:
                select_columns.append("id")
            order_column = "id" if "id" in columns else "arquivo"
            sql = f"SELECT {', '.join(select_columns)} FROM memes ORDER BY {order_column}"
            rows = conn.execute(sql).fetchall()
        finally:
            pass

        records: list[IndexRecord] = []
        for idx, row in enumerate(rows):
            embedding_blob = row["embedding"]
            if not embedding_blob:
                continue
            relative_path = row["relative_path"] if "relative_path" in row.keys() else None
            caminho = row["caminho"] or ""
            resolved_path = self.resolve_media_path(
                caminho,
                relative_path,
                storage_path=row["storage_path"] if "storage_path" in row.keys() else None,
                library_id=row["library_id"] if "library_id" in row.keys() else None,
            )
            desc_blob = row["desc_embedding"]
            records.append(
                IndexRecord(
                    index=idx,
                    arquivo=row["arquivo"] or "",
                    caminho=caminho,
                    resolved_path=resolved_path,
                    texto_extraido=row["texto_extraido"] or "",
                    descricao_ia=row["descricao_ia"] or "",
                    tags=row["tags"] if "tags" in row.keys() and row["tags"] else "",
                    embedding=np.frombuffer(embedding_blob, dtype=np.float32).copy(),
                    desc_embedding=np.frombuffer(desc_blob, dtype=np.float32).copy() if desc_blob else None,
                    relative_path=relative_path,
                    visual_json=row["visual_json"] if "visual_json" in row.keys() else "",
                    objects=row["objects"] if "objects" in row.keys() else "",
                    style=row["style"] if "style" in row.keys() else "",
                    source_work=row["source_work"] if "source_work" in row.keys() else "",
                    humor=row["humor"] if "humor" in row.keys() else "",
                    context=row["context"] if "context" in row.keys() else "",
                    content_hash=row["content_hash"] if "content_hash" in row.keys() else "",
                    file_size=row["file_size"] if "file_size" in row.keys() else None,
                    file_mtime=row["file_mtime"] if "file_mtime" in row.keys() else None,
                    library_id=row["library_id"] if "library_id" in row.keys() else None,
                    storage_path=row["storage_path"] if "storage_path" in row.keys() else None,
                    source_path=row["source_path"] if "source_path" in row.keys() else None,
                    db_id=int(row["id"]) if "id" in row.keys() and row["id"] is not None else 0,
                    audio_fingerprint=row["audio_fingerprint"] if "audio_fingerprint" in row.keys() and row["audio_fingerprint"] else "",
                    audio_embedding=np.frombuffer(row["audio_embedding"], dtype=np.float32).copy() if "audio_embedding" in row.keys() and row["audio_embedding"] else None,
                    perceptual_hash=row["perceptual_hash"] if "perceptual_hash" in row.keys() and row["perceptual_hash"] else "",
                )
            )
        return records

    def invalidate_table_cache(self) -> None:
        self.db.invalidate_table_cache()

    def _has_collections_tables(self) -> bool:
        return self.db.has_collections_tables()

    def _has_concept_tables(self) -> bool:
        return self.db.has_concept_tables()

    def list_collections(self) -> list[dict[str, Any]]:
        return self.db.list_collections()

    def create_collection(self, name: str, description: str = "") -> int:
        return self.db.create_collection(name, description)

    def rename_collection(self, collection_id: int, new_name: str) -> None:
        self.db.rename_collection(collection_id, new_name)

    def delete_collection(self, collection_id: int) -> None:
        self.db.delete_collection(collection_id)

    def add_records_to_collection(self, db_ids: list[int], collection_id: int) -> int:
        return self.db.add_records_to_collection(db_ids, collection_id)

    def remove_records_from_collection(self, db_ids: list[int], collection_id: int) -> None:
        self.db.remove_records_from_collection(db_ids, collection_id)

    def get_record_collections(self, db_id: int) -> list[dict[str, Any]]:
        return self.db.get_record_collections(db_id)

    def _get_collection_db_ids(self, collection_ids: frozenset[int]) -> frozenset[int]:
        return self.db.get_collection_db_ids(collection_ids)

    def _db_id_to_idx(self) -> dict[int, int]:
        return {r.db_id: r.index for r in self.records if r.db_id}

    def _concept_refined_centroid(self, concept_id: int) -> np.ndarray | None:
        from core.concepts import (
            compute_refined_centroid,
            get_confirmed_meme_ids,
            get_references,
            get_rejected_meme_ids,
        )

        conn = self.db.get_connection()
        try:
            refs = get_references(conn, concept_id)
            if not refs:
                return None
            confirmed_ids = get_confirmed_meme_ids(conn, concept_id)
            rejected_ids = get_rejected_meme_ids(conn, concept_id)
        finally:
            pass

        if self.image_matrix is None:
            from core.concepts import compute_centroid
            return compute_centroid([r["embedding"] for r in refs])

        db_to_idx = self._db_id_to_idx()
        pos_extra = [
            self.image_matrix[db_to_idx[did]]
            for did in confirmed_ids
            if did in db_to_idx
        ]
        negatives = [
            self.image_matrix[db_to_idx[did]]
            for did in rejected_ids
            if did in db_to_idx
        ]
        return compute_refined_centroid(
            [r["embedding"] for r in refs], pos_extra, negatives
        )

    def _try_concept_embedding(self, query: str) -> np.ndarray | None:
        if not self._has_concept_tables():
            return None
        from core.concepts import (
            compute_refined_centroid,
            get_confirmed_meme_ids,
            get_references,
            get_rejected_meme_ids,
            list_concepts,
        )

        q = query.lower().strip()
        conn = self.db.get_connection()
        try:
            concepts = list_concepts(conn)
            matched = next(
                (
                    c for c in concepts
                    if q in [c["name"].lower()]
                    + [t.strip().lower() for t in c["search_terms"].split(",") if t.strip()]
                ),
                None,
            )
            if not matched:
                return None
            refs = get_references(conn, matched["id"])
            if not refs:
                return None
            if self.image_matrix is None:
                from core.concepts import compute_centroid
                return compute_centroid([r["embedding"] for r in refs])
            confirmed_ids = get_confirmed_meme_ids(conn, matched["id"])
            rejected_ids = get_rejected_meme_ids(conn, matched["id"])
        finally:
            pass

        db_to_idx = self._db_id_to_idx()
        pos_extra = [self.image_matrix[db_to_idx[did]] for did in confirmed_ids if did in db_to_idx]
        negatives = [self.image_matrix[db_to_idx[did]] for did in rejected_ids if did in db_to_idx]
        return compute_refined_centroid([r["embedding"] for r in refs], pos_extra, negatives)

    def find_concept_matches(
        self, concept_id: int, top_k: int = 80, min_score: float = 0.65
    ) -> list[tuple[int, float]]:
        if self.image_matrix is None:
            return []
        from core.concepts import get_confirmed_meme_ids, get_rejected_meme_ids

        centroid = self._concept_refined_centroid(concept_id)
        if centroid is None:
            return []

        conn = self.db.get_connection()
        try:
            confirmed_db_ids = get_confirmed_meme_ids(conn, concept_id)
            rejected_db_ids = get_rejected_meme_ids(conn, concept_id)
        finally:
            pass

        already_decided = confirmed_db_ids | rejected_db_ids

        if (
            self.image_index is not None
            and self.image_index.ntotal == len(self.records)
        ):
            limit = min(top_k * 4, len(self.records))
            _, idxs = self.image_index.search(centroid, limit)
            candidates = [i for i in idxs[0].tolist() if i >= 0]
        else:
            candidates = list(range(len(self.records)))

        results: list[tuple[int, float]] = []
        for idx in candidates:
            record = self.records[idx]
            if record.db_id in already_decided:
                continue
            score = float(np.dot(centroid.reshape(-1), self.image_matrix[idx].reshape(-1)))
            if score >= min_score:
                results.append((idx, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def resolve_media_path(
        self,
        caminho: str,
        relative_path: str | None = None,
        *,
        storage_path: str | None = None,
        library_id: int | None = None,
    ) -> str | None:
        candidates: list[Path] = []
        if storage_path and library_id is not None:
            library_root = self.library_roots.get(int(library_id))
            if library_root:
                candidates.append(library_root / storage_path)
        if relative_path:
            candidates.append(self.media_root / relative_path)
        if caminho:
            path = Path(caminho)
            candidates.append(path if path.is_absolute() else Path.cwd() / path)
            candidates.append(self.media_root / path.name)

        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return str(candidates[0]) if candidates else None

    def _stack_embeddings(self, field_name: str) -> np.ndarray | None:
        values: list[np.ndarray] = []
        for record in self.records:
            value = getattr(record, field_name)
            if value is None:
                return None
            values.append(value)
        if not values:
            return None
        return np.stack(values).astype("float32")

    def search_audio_text(self, query: str, top_k: int = 20) -> list[SearchResult]:
        if self.audio_index is None or not self.audio_record_indices:
            return []
        try:
            if self._clap_model is None:
                from transformers import ClapModel, ClapProcessor
                self._clap_model = ClapModel.from_pretrained(
                    "laion/clap-htsat-unfused", torch_dtype=torch.float32
                ).to(self.device).eval()
                self._clap_processor = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")
            inputs = self._clap_processor(text=[query], return_tensors="pt", padding=True).to(self.device)
            with torch.no_grad():
                text_emb = self._clap_model.get_text_features(**inputs)
                text_emb = text_emb / text_emb.norm(dim=-1, keepdim=True)
            text_vec = text_emb.cpu().float().numpy().reshape(1, -1)
            k = min(top_k, len(self.audio_record_indices))
            scores, indices = self.audio_index.search(text_vec, k)
            results = []
            for score, idx in zip(scores[0], indices[0], strict=False):
                if idx < 0:
                    continue
                rec_idx = self.audio_record_indices[int(idx)]
                rec = self.records[rec_idx]
                results.append(SearchResult(
                    score=float(score),
                    index=rec_idx,
                    arquivo=rec.arquivo,
                    caminho=rec.caminho,
                    resolved_path=rec.resolved_path,
                    texto_extraido=rec.texto_extraido,
                    descricao_ia=rec.descricao_ia,
                    tags=rec.tags,
                    embedding=rec.embedding,
                    score_details={"clap_score": float(score)},
                ))
            return results
        except Exception:
            return []

    @staticmethod
    def _record_to_dict(record: IndexRecord) -> dict[str, Any]:
        return {
            "arquivo": record.arquivo,
            "caminho": record.caminho,
            "resolved_path": record.resolved_path,
            "texto_extraido": record.texto_extraido,
            "descricao_ia": record.descricao_ia,
            "tags": record.tags,
            "embedding": record.embedding,
            "desc_embedding": record.desc_embedding,
            "relative_path": record.relative_path,
            "visual_json": record.visual_json,
            "objects": record.objects,
            "style": record.style,
            "source_work": record.source_work,
            "humor": record.humor,
            "context": record.context,
            "content_hash": record.content_hash,
            "file_size": record.file_size,
            "file_mtime": record.file_mtime,
            "library_id": record.library_id,
            "storage_path": record.storage_path,
            "source_path": record.source_path,
        }

    @property
    def dados(self) -> list[dict[str, Any]]:
        """Lazy list-of-dicts for eval/benchmark scripts. Computed once on first read."""
        if self._dados_cache is None:
            self._dados_cache = [self._record_to_dict(r) for r in self.records]
        return self._dados_cache

    def encode_text(self, query: str, translate: bool = True) -> tuple[np.ndarray, str]:
        if self.model is None:
            raise RuntimeError("Search model is not loaded.")
        search_text = query
        if translate and query:
            try:
                translated = GoogleTranslator(source="pt", target="en").translate(query)
                if translated:
                    search_text = translated
            except Exception:
                search_text = query
        embedding = self.model.encode(search_text).astype("float32")
        return self._normalize_vector(embedding), search_text

    def encode_image(self, image: Image.Image) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Search model is not loaded.")
        embedding = self.model.encode(image.convert("RGB")).astype("float32")
        return self._normalize_vector(embedding)

    def _normalize_vector(self, vector: np.ndarray) -> np.ndarray:
        return self.vector_store.normalize_vector(vector)

    def search_text(self, query: str, options: SearchOptions | None = None) -> list[SearchResult]:
        options = options or SearchOptions()
        positive_query, negative_terms = parse_query_terms(query)
        if not positive_query:
            return []

        concept_embedding = self._try_concept_embedding(positive_query)
        if concept_embedding is not None:
            return self.search_by_embedding(
                query_embedding=concept_embedding,
                options=options,
                text_query=positive_query,
                translated_query=positive_query,
                negative_terms=negative_terms,
            )

        query_embedding, translated_query = self.encode_text(
            positive_query, translate=options.translate
        )
        return self.search_by_embedding(
            query_embedding=query_embedding,
            options=options,
            text_query=positive_query,
            translated_query=translated_query,
            negative_terms=negative_terms,
        )

    def search_image(
        self, image: Image.Image, options: SearchOptions | None = None
    ) -> list[SearchResult]:
        return self.search_by_embedding(self.encode_image(image), options or SearchOptions())

    def search_similar(
        self, record_index: int, options: SearchOptions | None = None
    ) -> list[SearchResult]:
        if record_index < 0 or record_index >= len(self.records):
            return []
        embedding = self._normalize_vector(self.records[record_index].embedding)
        return self.search_by_embedding(embedding, options or SearchOptions())

    def search_by_embedding(
        self,
        query_embedding: np.ndarray,
        options: SearchOptions,
        text_query: str = "",
        translated_query: str = "",
        negative_terms: Iterable[str] = (),
    ) -> list[SearchResult]:
        if not self.records or self.image_matrix is None:
            return []

        query_embedding = self._normalize_vector(query_embedding)
        self._validate_dimension(query_embedding)
        candidate_indices = self._candidate_indices(query_embedding, options.candidate_pool)
        if not candidate_indices:
            return []

        if options.collection_ids:
            allowed_db_ids = self.db.get_collection_db_ids(options.collection_ids)
            candidate_indices = [
                idx for idx in candidate_indices
                if self.records[idx].db_id in allowed_db_ids
            ]
            if not candidate_indices:
                return []

        if options.concept_ids:
            from core.concepts import get_concept_meme_ids_for_filter
            conn = self.db.get_connection()
            try:
                allowed_db_ids = get_concept_meme_ids_for_filter(conn, options.concept_ids)
            finally:
                pass
            candidate_indices = [
                idx for idx in candidate_indices
                if self.records[idx].db_id in allowed_db_ids
            ]
            if not candidate_indices:
                return []

        if options.media_type != "all":
            allowed_exts = VIDEO_EXTENSIONS if options.media_type == "video" else IMAGE_EXTENSIONS
            candidate_indices = [
                idx for idx in candidate_indices
                if os.path.splitext(self.records[idx].arquivo)[1].lower() in allowed_exts
            ]
            if not candidate_indices:
                return []

        if options.excluded_db_ids:
            candidate_indices = [
                idx for idx in candidate_indices
                if self.records[idx].db_id not in options.excluded_db_ids
            ]
            if not candidate_indices:
                return []

        scores, details = self._score_candidates(
            query_embedding=query_embedding,
            candidate_indices=candidate_indices,
            options=options,
            text_query=text_query,
            translated_query=translated_query,
            negative_terms=list(negative_terms),
        )
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)

        results: list[SearchResult] = []
        for idx, score in ranked:
            if score < options.threshold:
                continue
            record = self.records[idx]
            results.append(
                SearchResult(
                    score=float(score),
                    index=idx,
                    arquivo=record.arquivo,
                    caminho=record.caminho,
                    resolved_path=record.resolved_path,
                    texto_extraido=record.texto_extraido,
                    descricao_ia=record.descricao_ia,
                    tags=record.tags,
                    embedding=record.embedding,
                    score_details=details.get(idx, {}),
                )
            )
            if len(results) >= options.top_k:
                break
        return results

    def _validate_dimension(self, query_embedding: np.ndarray) -> None:
        expected = self.image_matrix.shape[1] if self.image_matrix is not None else None
        if expected and query_embedding.shape[1] != expected:
            raise ValueError(
                f"Model dimension mismatch: query has {query_embedding.shape[1]}, "
                f"index expects {expected}."
            )

    def _candidate_indices(self, query_embedding: np.ndarray, candidate_pool: int) -> list[int]:
        use_faiss = (
            self.image_index is not None
            and self.image_index.d == query_embedding.shape[1]
            and self.image_index.ntotal == len(self.records)
        )
        if not use_faiss:
            return list(range(len(self.records)))

        limit = min(max(candidate_pool, 1), len(self.records))
        _, image_indices = self.image_index.search(query_embedding, limit)
        candidates = {idx for idx in image_indices[0].tolist() if idx >= 0}

        if (
            self.desc_index is not None
            and self.desc_index.d == query_embedding.shape[1]
            and self.desc_index.ntotal == len(self.records)
        ):
            _, desc_indices = self.desc_index.search(query_embedding, limit)
            candidates.update(idx for idx in desc_indices[0].tolist() if idx >= 0)

        return sorted(candidates)

    def _score_candidates(
        self,
        query_embedding: np.ndarray,
        candidate_indices: list[int],
        options: SearchOptions,
        text_query: str,
        translated_query: str,
        negative_terms: list[str],
    ) -> tuple[dict[int, float], dict[int, dict[str, float | str]]]:
        candidate_image_matrix = self.image_matrix[candidate_indices]
        query_tensor = torch.from_numpy(query_embedding).to(self.device)
        image_tensor = torch.from_numpy(candidate_image_matrix).to(self.device).to(
            query_tensor.dtype
        )
        image_scores = util.cos_sim(query_tensor, image_tensor)[0].detach().cpu().numpy()

        desc_scores: np.ndarray | None = None
        if self.desc_matrix is not None:
            desc_tensor = torch.from_numpy(self.desc_matrix[candidate_indices]).to(
                self.device
            ).to(query_tensor.dtype)
            desc_scores = util.cos_sim(query_tensor, desc_tensor)[0].detach().cpu().numpy()

        scores: dict[int, float] = {}
        details: dict[int, dict[str, float | str]] = {}
        for local_idx, record_idx in enumerate(candidate_indices):
            record = self.records[record_idx]
            if self._matches_negative(record, negative_terms):
                continue

            image_score = float(image_scores[local_idx])
            if desc_scores is not None:
                desc_score = float(desc_scores[local_idx])
                semantic_score = image_score * options.balance + desc_score * (1.0 - options.balance)
            else:
                desc_score = 0.0
                semantic_score = image_score

            lexical_score = 0.0
            score = semantic_score
            if text_query:
                lexical_score = self._lexical_score(
                    record=record,
                    text_query=text_query,
                    translated_query=translated_query,
                )
                score += lexical_score * options.lexical_weight
                score *= self._text_multiplier(
                    record=record,
                    text_query=text_query,
                    translated_query=translated_query,
                    text_bonus=options.text_bonus,
                )
            scores[record_idx] = score
            details[record_idx] = {
                "image": image_score,
                "description": desc_score,
                "semantic": semantic_score,
                "lexical": lexical_score,
                "balance": options.balance,
                "lexical_weight": options.lexical_weight,
                "style": record.style,
                "source_work": record.source_work,
                "context": record.context,
                "humor": record.humor,
            }
        return scores, details

    def _matches_negative(self, record: IndexRecord, negative_terms: Iterable[str]) -> bool:
        if not negative_terms:
            return False
        content = normalize_text(
            f"{record.texto_extraido} {record.descricao_ia} {record.tags} "
            f"{record.objects} {record.style} {record.source_work} {record.humor} {record.context}"
        )
        return any(term and term in content for term in negative_terms)

    def _lexical_score(
        self,
        record: IndexRecord,
        text_query: str,
        translated_query: str,
    ) -> float:
        words = self._query_words(text_query) + self._query_words(translated_query)
        q_words = list(dict.fromkeys(words))
        if not q_words:
            return 0.0

        weighted_fields = [
            (record.tags, 1.4),
            (record.texto_extraido, 1.3),
            (record.descricao_ia, 1.0),
            (record.objects, 1.1),
            (record.style, 1.1),
            (record.source_work, 1.4),
            (record.humor, 1.0),
            (record.context, 1.1),
        ]
        score = 0.0
        max_score = 0.0
        for field, weight in weighted_fields:
            normalized = normalize_text(field)
            for word in q_words:
                max_score += weight
                if word in normalized:
                    score += weight

        normalized_query = normalize_text(text_query)
        full_content = normalize_text(" ".join(field for field, _ in weighted_fields if field))
        if normalized_query and normalized_query in full_content:
            score += 2.0
            max_score += 2.0
        return score / max(max_score, 1.0)

    def _text_multiplier(
        self,
        record: IndexRecord,
        text_query: str,
        translated_query: str,
        text_bonus: float,
    ) -> float:
        words = self._query_words(text_query) + self._query_words(translated_query)
        q_words = list(dict.fromkeys(words))
        if not q_words:
            return 1.0

        tags_content = normalize_text(record.tags)
        ocr_content = normalize_text(record.texto_extraido)
        desc_content = normalize_text(
            f"{record.descricao_ia} {record.objects} {record.style} "
            f"{record.source_work} {record.humor} {record.context}"
        )
        content = f"{tags_content} {ocr_content} {desc_content}"

        matched_tags = [word for word in q_words if word in tags_content]
        matched_text = [word for word in q_words if word in ocr_content or word in desc_content]
        total_weight = max(sum(len(word) for word in q_words), 1)
        match_score = (
            sum(len(word) for word in matched_tags) * 1.5
            + sum(len(word) for word in matched_text)
        ) / (total_weight * 1.5)

        multiplier = 1.0 + min(1.0, match_score) * text_bonus
        if len(q_words) >= 2:
            bigrams = [" ".join(q_words[i : i + 2]) for i in range(len(q_words) - 1)]
            matched_bigrams = sum(1 for bigram in bigrams if bigram in content)
            multiplier += (matched_bigrams / len(bigrams)) * text_bonus * 0.3

        normalized_query = normalize_text(text_query)
        if normalized_query and normalized_query in content:
            multiplier += text_bonus * 0.2
        return multiplier

    @staticmethod
    def _query_words(query: str) -> list[str]:
        words: list[str] = []
        for raw_word in query.replace(",", " ").replace(".", " ").split():
            word = normalize_text(raw_word)
            if len(word) > 2 and word not in STOP_WORDS:
                words.append(word)
        return words

    def random_results(self, top_k: int) -> list[SearchResult]:
        indices = np.random.permutation(len(self.records))[:top_k]
        results: list[SearchResult] = []
        for idx in indices:
            record = self.records[int(idx)]
            results.append(
                SearchResult(
                    score=1.0,
                    index=record.index,
                    arquivo=record.arquivo,
                    caminho=record.caminho,
                    resolved_path=record.resolved_path,
                    texto_extraido=record.texto_extraido,
                    descricao_ia=record.descricao_ia,
                    tags=record.tags,
                    embedding=record.embedding,
                    score_details={"mode": "random"},
                )
            )
        return results

    def buscar(
        self,
        termo: str,
        top_k: int = 5,
        translate: bool = True,
        custom_weights: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        weights = custom_weights or self.weights
        options = SearchOptions(
            top_k=top_k,
            threshold=-1.0,
            balance=float(weights.get("balance", DEFAULT_WEIGHTS["balance"])),
            text_bonus=float(weights.get("text_bonus", DEFAULT_WEIGHTS["text_bonus"])),
            lexical_weight=float(weights.get("lexical_weight", 0.25)),
            translate=translate,
        )
        return [
            {"score": result.score, "arquivo": result.arquivo, "index": result.index}
            for result in self.search_text(termo, options)
        ]
