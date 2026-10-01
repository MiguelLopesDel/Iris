from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from deep_translator import GoogleTranslator
from PIL import Image

from core import compute_device, vector_sidecar
from core.db_manager import DatabaseManager
from core.embedding_models import DEFAULT_MODEL as DEFAULT_MODEL  # historical re-export
from core.embedding_models import EmbeddingEncoder, load_encoder, resolve_embedding_model
from core.record_catalog import RecordCatalog, RecordColumns
from core.record_text import TEXT_COLUMNS, TextStore
from core.search_types import (
    STOP_WORDS,
    IndexRecord,
    SearchOptions,
    SearchResult,
    normalize_text,
    parse_query_terms,
)
from core.vector_store import VectorStore

logger = logging.getLogger("iris")

# A smaller checkpoint for machines with few CPU cores and shared video memory.
# The index and query encoder must always use the same checkpoint.
LOW_RESOURCE_MODEL = "sentence-transformers/clip-ViT-B-32"
DEFAULT_WEIGHTS = {"balance": 0.5, "text_bonus": 2.0, "lexical_weight": 0.25}

VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mkv", ".mov", ".ogg"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
AUDIO_EXTENSIONS = frozenset({".mp3"})


def queries_may_leave_the_machine() -> bool:
    """Se a consulta pode ser enviada ao tradutor externo.

    O modelo de busca (``clip-ViT-L-14``) foi treinado em inglês, então buscar
    em português sem traduzir degrada bastante o resultado. O preço é que o
    texto digitado sai da máquina rumo ao Google Translate — a mídia não sai, a
    consulta sim. Quem prefere privacidade a qualidade de busca desliga com
    ``IRIS_TRANSLATE_QUERIES=0``; antes disso não havia como, porque o
    interruptor na interface era ``checked hidden``.
    """
    return os.environ.get("IRIS_TRANSLATE_QUERIES", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


# Where each ranking column sits in the folded tuple, and what it is worth.
# Positions rather than names: this runs once per candidate per query.
_OCR = TEXT_COLUMNS.index("texto_extraido")
_TAGS = TEXT_COLUMNS.index("tags")
_LEXICAL_WEIGHTS: tuple[tuple[int, float], ...] = (
    (_TAGS, 1.4),
    (_OCR, 1.3),
    (TEXT_COLUMNS.index("descricao_ia"), 1.0),
    (TEXT_COLUMNS.index("objects"), 1.1),
    (TEXT_COLUMNS.index("style"), 1.1),
    (TEXT_COLUMNS.index("source_work"), 1.4),
    (TEXT_COLUMNS.index("humor"), 1.0),
    (TEXT_COLUMNS.index("context"), 1.1),
)
# Everything the caption pipeline produced, as opposed to what the image itself
# showed in text: the multiplier weighs the two differently.
_DESCRIPTIVE: tuple[int, ...] = tuple(
    position for position, _ in _LEXICAL_WEIGHTS if position not in (_TAGS, _OCR)
)

@dataclass(frozen=True)
class _QueryText:
    """The folded query, prepared once and read by every candidate."""

    words: list[str]
    normalized: str
    bigrams: list[str]
    total_weight: int


# How many records are warmed at once when the whole catalogue is walked.
# Smaller than the store's cache so a chunk survives until it has been read.
_TEXT_CHUNK = 512


def _stack_vectors(vectors: list) -> np.ndarray | None:
    """Stack vectors, substituting zeros for the ones that are missing.

    A single missing value used to return None for the whole matrix, which
    silently disabled every search path that depends on it -- one item without a
    description embedding switched off description search for the entire
    catalogue. A zero row has zero cosine with everything, so the item simply
    never matches and the rest keeps working: degradation proportional to the
    gap rather than all or nothing.
    """
    present = [vector for vector in vectors if vector is not None]
    if not present:
        return None
    width = present[0].shape[0]
    matrix = np.zeros((len(vectors), width), dtype=np.float32)
    for position, vector in enumerate(vectors):
        if vector is not None and vector.shape[0] == width:
            matrix[position] = vector
    return matrix


def _torch_cosine_similarity(query: torch.Tensor, candidates: torch.Tensor) -> torch.Tensor:
    """Calculate row-wise cosine scores without importing an encoder package.

    ``sentence-transformers`` is required to load and run an embedding model,
    but importing it during server startup made a model-free development server
    depend on its full optional dependency stack. Keep this small tensor
    operation local so gallery, accounts, and API development work with
    ``IRIS_LOAD_MODEL=0``.
    """
    query_normalized = torch.nn.functional.normalize(query, p=2, dim=1)
    candidates_normalized = torch.nn.functional.normalize(candidates, p=2, dim=1)
    return query_normalized @ candidates_normalized.T


def _weighted_mean(signals: list[tuple[float, bool, float]]) -> float | None:
    """Weighted mean over the signals an item actually has.

    Ranking blends an image score and a description score, and not every item
    carries both. Substituting a value for the missing one puts absence into the
    arithmetic, where it becomes whatever that value happens to do: a zero
    outranks anything genuinely dissimilar, and a sentinel below the domain
    turns into a penalty scaled by a weight that means something else. Averaging
    only over what exists keeps absence out of the numbers.

    Returns None when no available signal carries weight -- balance 0 on an item
    with no description, say. That item has no score for this query rather than
    a score built from a signal the query asked to ignore.
    """
    total = 0.0
    weight_sum = 0.0
    for weight, available, value in signals:
        if not available or weight <= 0.0:
            continue
        total += weight * value
        weight_sum += weight
    if weight_sum <= 0.0:
        return None
    return total / weight_sum


class IrisEngine:
    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        model_name: str | None = None,
        media_root: str | os.PathLike[str] | None = None,
        weights_path: str | os.PathLike[str] = "data/best_weights.json",
        load_model: bool = True,
        device: str | None = None,
    ):
        self.db_path = Path(db_path or self._default_db_path())
        self.db = DatabaseManager(self.db_path)
        self.vector_store = VectorStore(self.db_path)
        
        self.model_name = model_name or resolve_embedding_model()
        self.media_root = Path(media_root or ".").resolve()
        self.device = device or self._detect_device()
        self.weights = self._load_weights(Path(weights_path))
        
        self.library_roots = self.db.get_library_roots()
        self._image_matrix: np.ndarray | None = None
        self._desc_matrix: np.ndarray | None = None
        self._desc_present: np.ndarray | None = None
        self._text_store: TextStore | None = None
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
        self._catalog_model_checked = False

    @staticmethod
    def _detect_device() -> str:
        """Honours the instance's GPU choice; engines are rebuilt when it changes."""
        return compute_device.resolve("auto")

    @staticmethod
    def _default_db_path() -> str:
        if Path("data/teste_playground.db").exists():
            return "data/teste_playground.db"
        return "data/iris.db"

    def _load_model(self) -> EmbeddingEncoder:
        return load_encoder(self.model_name, device=self.device, half=True)

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

    def _load_records(self) -> RecordCatalog | list[IndexRecord]:
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
                "embedding",
                "desc_embedding",
            ]
            # Text is read by id when the catalogue has one. A schema without
            # `id` -- only very old ones -- has nothing to read it by, so there
            # the columns still come along with the row.
            text_columns = [name for name in TEXT_COLUMNS if name in columns]
            eager_text = "id" not in columns
            self._text_store = None if eager_text else TextStore(self.db, columns)
            if eager_text:
                select_columns.extend(text_columns)
            if "relative_path" in columns:
                select_columns.append("relative_path")
            for optional in [
                "content_hash",
                "file_size",
                "file_mtime",
                "library_id",
                "storage_path",
                "source_path",
                "audio_fingerprint",
                "audio_embedding",
                "perceptual_hash",
                "thumb_hash",
            ]:
                if optional in columns:
                    select_columns.append(optional)

            if "id" in columns:
                select_columns.append("id")
            order_column = "id" if "id" in columns else "arquivo"
            sql = f"SELECT {', '.join(select_columns)} FROM memes ORDER BY {order_column}"
            # Streamed, not fetched. fetchall held every row -- blobs included --
            # while the records were being built from copies of the same bytes, so
            # the embeddings existed three times over at the peak: the rows, the
            # per-record copies, and the stacked matrices.
            rows = conn.execute(sql)
        finally:
            pass

        # Scalar metadata is kept columnar.  Retaining an IndexRecord for every
        # row costs an object plus a long run of pointers per item; the adapter
        # created below materialises one only when a caller asks for it.
        arquivos: list[str] = []
        caminhos: list[str] = []
        resolved_paths: list[str | None] = []
        relative_paths: list[str | None] = []
        content_hashes: list[str] = []
        file_sizes: list[int] = []
        file_mtimes: list[float] = []
        library_ids: list[int] = []
        storage_paths: list[str | None] = []
        source_paths: list[str | None] = []
        db_ids: list[int] = []
        audio_fingerprints: list[str] = []
        audio_embeddings: list[np.ndarray | None] = []
        perceptual_hashes: list[str] = []
        thumb_hashes: list[str] = []
        eager_text_values: list[tuple[str, ...] | None] = []
        image_present: list[bool] = []
        # Written straight into the matrices as rows stream past. Collecting the
        # vectors in a list first would have kept a second copy of every
        # embedding alive until the stacking finished, and a peak is not undone
        # by freeing it: the allocator keeps the arena.
        record_count = int(conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0])
        embedded_count = self._embedding_count(conn)
        described_count = int(
            conn.execute("SELECT COUNT(*) FROM memes WHERE desc_embedding IS NOT NULL").fetchone()[0]
        )
        # A sidecar only maps the dense case. When some media has not been
        # embedded yet, build aligned zero-filled matrices for the vector-bearing
        # rows while keeping every media row in the gallery catalog.
        image_matrix = (
            self._mapped_column(conn, "embedding", record_count)
            if record_count and embedded_count == record_count
            else None
        )
        desc_matrix = (
            self._mapped_column(conn, "desc_embedding", record_count)
            if record_count and described_count == record_count
            else None
        )
        mapped_image = image_matrix is not None
        mapped_desc = desc_matrix is not None
        has_desc: list[bool] = []
        for row in rows:
            position = len(arquivos)
            embedding_blob = row["embedding"]
            relative_path = row["relative_path"] if "relative_path" in row.keys() else None
            caminho = row["caminho"] or ""
            resolved_path = self.resolve_media_path(
                caminho,
                relative_path,
                storage_path=row["storage_path"] if "storage_path" in row.keys() else None,
                library_id=row["library_id"] if "library_id" in row.keys() else None,
            )
            desc_blob = row["desc_embedding"]
            has_image = False
            if embedding_blob:
                vector = np.frombuffer(embedding_blob, dtype=np.float32)
                if image_matrix is None:
                    image_matrix = np.zeros((record_count, vector.shape[0]), dtype=np.float32)
                if vector.shape[0] == image_matrix.shape[1]:
                    if not mapped_image:
                        image_matrix[position] = vector
                    has_image = True
            image_present.append(has_image)

            has_description = False
            if desc_blob:
                desc_vector = np.frombuffer(desc_blob, dtype=np.float32)
                if desc_matrix is None:
                    desc_matrix = np.zeros((record_count, desc_vector.shape[0]), dtype=np.float32)
                if desc_vector.shape[0] == desc_matrix.shape[1]:
                    if not mapped_desc:
                        desc_matrix[position] = desc_vector
                    has_description = True
            has_desc.append(has_description)
            keys = row.keys()
            arquivos.append(row["arquivo"] or "")
            caminhos.append(caminho)
            resolved_paths.append(resolved_path)
            relative_paths.append(relative_path)
            content_hashes.append(row["content_hash"] if "content_hash" in keys else "")
            file_sizes.append(int(row["file_size"]) if "file_size" in keys and row["file_size"] is not None else -1)
            file_mtimes.append(float(row["file_mtime"]) if "file_mtime" in keys and row["file_mtime"] is not None else float("nan"))
            library_ids.append(int(row["library_id"]) if "library_id" in keys and row["library_id"] is not None else -1)
            storage_paths.append(row["storage_path"] if "storage_path" in keys else None)
            source_paths.append(row["source_path"] if "source_path" in keys else None)
            db_ids.append(int(row["id"]) if "id" in keys and row["id"] is not None else 0)
            audio_fingerprints.append(row["audio_fingerprint"] if "audio_fingerprint" in keys and row["audio_fingerprint"] else "")
            audio_embeddings.append(
                np.frombuffer(row["audio_embedding"], dtype=np.float32).copy()
                if "audio_embedding" in keys and row["audio_embedding"]
                else None
            )
            perceptual_hashes.append(row["perceptual_hash"] if "perceptual_hash" in keys and row["perceptual_hash"] else "")
            thumb_hashes.append(row["thumb_hash"] if "thumb_hash" in keys and row["thumb_hash"] else "")
            eager_text_values.append(
                tuple(row[name] or "" if name in text_columns else "" for name in TEXT_COLUMNS)
                if eager_text
                else None
            )
        if not arquivos:
            return []
        self._image_matrix = image_matrix
        # Kept even when some rows have no description embedding. Returning None
        # for the whole matrix, as this used to, silently switched off
        # description search for the entire catalogue because of one gap; a zero
        # row simply never matches.
        self._desc_matrix = desc_matrix if any(has_desc) else None
        self._desc_present = np.array(has_desc, dtype=bool) if any(has_desc) else None
        columns = RecordColumns(
            arquivo=tuple(arquivos),
            caminho=tuple(caminhos),
            resolved_path=tuple(resolved_paths),
            relative_path=tuple(relative_paths),
            content_hash=tuple(content_hashes),
            file_size=np.asarray(file_sizes, dtype=np.int64),
            file_mtime=np.asarray(file_mtimes, dtype=np.float64),
            library_id=np.asarray(library_ids, dtype=np.int64),
            storage_path=tuple(storage_paths),
            source_path=tuple(source_paths),
            db_id=np.asarray(db_ids, dtype=np.int64),
            audio_fingerprint=tuple(audio_fingerprints),
            audio_embedding=tuple(audio_embeddings),
            perceptual_hash=tuple(perceptual_hashes),
            thumb_hash=tuple(thumb_hashes),
            eager_text=tuple(eager_text_values),
            image_present=np.asarray(image_present, dtype=bool),
        )
        return RecordCatalog(
            columns,
            image_matrix=image_matrix,
            desc_matrix=self._desc_matrix,
            desc_present=self._desc_present,
            text_store=self._text_store,
        )

    def _mapped_column(self, conn, column: str, expected: int | None) -> np.ndarray | None:
        """The column as a memory map, or None to read it the old way."""
        try:
            mapped = vector_sidecar.open_map(conn, self.db_path, column)
        except Exception:
            return None
        if mapped is None:
            return None
        if expected is not None and mapped.shape[0] != expected:
            return None
        return mapped

    @staticmethod
    def _embedding_count(conn) -> int:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM memes WHERE embedding IS NOT NULL"
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

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
        if isinstance(self.records, RecordCatalog):
            return {
                int(db_id): index
                for index, db_id in enumerate(self.records.columns.db_id)
                if db_id > 0
            }
        return {
            record.db_id: position
            for position, record in enumerate(self.records)
            if record.db_id
        }

    def _record_db_id(self, index: int) -> int:
        if isinstance(self.records, RecordCatalog):
            return self.records.db_id_at(index)
        return self.records[index].db_id

    def _record_filename(self, index: int) -> str:
        if isinstance(self.records, RecordCatalog):
            return self.records.filename_at(index)
        return self.records[index].arquivo

    def _record_resolved_path(self, index: int) -> str | None:
        if isinstance(self.records, RecordCatalog):
            return self.records.resolved_path_at(index)
        return self.records[index].resolved_path

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
            if self._record_db_id(idx) in already_decided:
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

    def _has_description(self, record_index: int) -> bool:
        """Whether this record actually has a description embedding.

        The matrix keeps a row for every record so it stays aligned with them,
        but a row of zeros is not a neutral score: cosine is signed, so zero
        outranks anything genuinely dissimilar. Measured, an item with no
        description ranked second against a query opposed to the catalogue,
        beating two real items whose scores were negative. Asking here, at the
        point where the score is used, keeps that out of the numbers entirely.
        """
        if self._desc_present is None:
            return True
        return bool(self._desc_present[record_index])

    def _has_image_embedding(self, record_index: int) -> bool:
        if isinstance(self.records, RecordCatalog):
            return self.records.has_image_embedding(record_index)
        embedding = self.records[record_index].embedding
        return embedding is not None and np.size(embedding) > 0

    def _stack_embeddings(self, field_name: str) -> np.ndarray | None:
        """Matrix built while loading; this only hands it over."""
        if field_name == "embedding":
            return self._image_matrix
        if field_name == "desc_embedding":
            return self._desc_matrix
        return None

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
            # Falha ao codificar/buscar com CLAP (modelo ausente, índice vazio,
            # erro de runtime): degrada para "sem resultados", mas registra para
            # não esconder a causa real.
            logger.warning("busca de áudio (CLAP) falhou para a query %r", query, exc_info=True)
            return []

    def _has_face_tables(self) -> bool:
        conn = self.db.get_connection()
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        return "faces" in tables and "persons" in tables

    def _ensure_face_index(self) -> None:
        """Build the in-memory face FAISS index lazily on first face query."""
        if getattr(self, "_face_built", False):
            return
        self._face_built = True
        self._face_index = None
        self._face_meme_ids: list[int] = []
        try:
            if not self._has_face_tables():
                return
            import faiss

            conn = self.db.get_connection()
            rows = conn.execute("SELECT meme_id, embedding FROM faces").fetchall()
            vecs: list[np.ndarray] = []
            meme_ids: list[int] = []
            for row in rows:
                emb = np.frombuffer(row["embedding"], dtype=np.float32)
                if emb.size == 0:
                    continue
                vecs.append(emb)
                meme_ids.append(int(row["meme_id"]))
            if not vecs:
                return
            matrix = np.array(vecs, dtype=np.float32)
            faiss.normalize_L2(matrix)
            index = faiss.IndexFlatIP(matrix.shape[1])
            index.add(matrix)
            self._face_index = index
            self._face_meme_ids = meme_ids
        except Exception:
            self._face_index = None

    def _face_result(self, meme_id: int, score: float, db_id_to_idx: dict[int, int]) -> SearchResult | None:
        idx = db_id_to_idx.get(meme_id)
        if idx is None:
            return None
        rec = self.records[idx]
        return SearchResult(
            score=score,
            index=idx,
            arquivo=rec.arquivo,
            caminho=rec.caminho,
            resolved_path=rec.resolved_path,
            texto_extraido=rec.texto_extraido,
            descricao_ia=rec.descricao_ia,
            tags=rec.tags,
            embedding=rec.embedding,
            score_details={"face_score": score},
        )

    # ArcFace cosine floor below which two faces are almost certainly different
    # people. Kept low to favour recall ("find every photo of X"); clearly
    # unrelated faces (cosine ~0) are still dropped so results aren't padded.
    FACE_MATCH_MIN_SCORE = 0.20

    def search_face(
        self, query_embedding: np.ndarray, top_k: int = 50, min_score: float | None = None
    ) -> list[SearchResult]:
        """Find media containing the person in ``query_embedding`` (best face per media)."""
        self._ensure_face_index()
        if self._face_index is None:
            return []
        floor = self.FACE_MATCH_MIN_SCORE if min_score is None else min_score
        query = self._normalize_vector(query_embedding)
        # Over-fetch: many faces map to the same media, and we aggregate per media.
        k = min(max(top_k * 5, top_k), self._face_index.ntotal)
        scores, indices = self._face_index.search(query, k)
        best: dict[int, float] = {}
        for score, fi in zip(scores[0], indices[0], strict=False):
            if fi < 0 or score < floor:
                continue
            meme_id = self._face_meme_ids[int(fi)]
            if score > best.get(meme_id, -1.0):
                best[meme_id] = float(score)
        db_id_to_idx = self._db_id_to_idx()
        results: list[SearchResult] = []
        for meme_id, score in sorted(best.items(), key=lambda kv: kv[1], reverse=True):
            res = self._face_result(meme_id, score, db_id_to_idx)
            if res is not None:
                results.append(res)
            if len(results) >= top_k:
                break
        return results

    def _face_embedding_by_id(self, face_id: int) -> np.ndarray | None:
        conn = self.db.get_connection()
        row = conn.execute("SELECT embedding FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not row or not row["embedding"]:
            return None
        return np.frombuffer(row["embedding"], dtype=np.float32)

    def _best_face_embedding_for_record(self, record_index: int) -> np.ndarray | None:
        if record_index < 0 or record_index >= len(self.records):
            return None
        db_id = self._record_db_id(record_index)
        if not db_id:
            return None
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT embedding FROM faces WHERE meme_id = ? ORDER BY det_score DESC LIMIT 1",
            (db_id,),
        ).fetchone()
        if not row or not row["embedding"]:
            return None
        return np.frombuffer(row["embedding"], dtype=np.float32)

    def search_face_by_face(self, face_id: int, top_k: int = 50) -> list[SearchResult]:
        """Find media containing the person of a specific detected face (gallery reference)."""
        emb = self._face_embedding_by_id(face_id)
        if emb is None:
            return []
        return self.search_face(emb, top_k=top_k)

    def search_face_by_record(self, record_index: int, top_k: int = 50) -> list[SearchResult] | None:
        """Find media with the person in an already-indexed item (its strongest face).

        Returns ``None`` when the item has no detected face, so callers can tell
        "no people here" apart from "no matches".
        """
        emb = self._best_face_embedding_for_record(record_index)
        if emb is None:
            return None
        return self.search_face(emb, top_k=top_k)

    def get_person_media(self, person_id: int, top_k: int = 500) -> list[SearchResult]:
        """All media of a person, ranked by similarity to the person's face centroid."""
        if not self._has_face_tables():
            return []
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT meme_id, embedding FROM faces WHERE person_id = ?", (person_id,)
        ).fetchall()
        if not rows:
            return []
        embs = [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
        centroid = self._normalize_vector(np.stack(embs).mean(axis=0)).reshape(-1)
        best: dict[int, float] = {}
        for r, emb in zip(rows, embs, strict=False):
            score = float(np.dot(emb, centroid))
            meme_id = int(r["meme_id"])
            if score > best.get(meme_id, -1.0):
                best[meme_id] = score
        db_id_to_idx = self._db_id_to_idx()
        results: list[SearchResult] = []
        for meme_id, score in sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:top_k]:
            res = self._face_result(meme_id, score, db_id_to_idx)
            if res is not None:
                results.append(res)
        return results

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
            # Chunked against the store's cache: warming all of it at once would
            # evict the start of the catalogue before the first dict is built,
            # turning one bulk read back into a query per item.
            rows: list[dict[str, Any]] = []
            for start in range(0, len(self.records), _TEXT_CHUNK):
                block = self.records[start : start + _TEXT_CHUNK]
                self._prefetch_text(range(start, start + len(block)))
                rows.extend(self._record_to_dict(record) for record in block)
            self._dados_cache = rows
        return self._dados_cache

    def encode_text(self, query: str, translate: bool = True) -> tuple[np.ndarray, str]:
        if self.model is None:
            raise RuntimeError("Search model is not loaded.")
        search_text = query
        if translate and query and queries_may_leave_the_machine():
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
        if not self._has_image_embedding(record_index):
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
        if not self.records:
            return []
        if self.image_matrix is None and not text_query:
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
                if self._record_db_id(idx) in allowed_db_ids
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
                if self._record_db_id(idx) in allowed_db_ids
            ]
            if not candidate_indices:
                return []

        if options.media_type != "all":
            allowed_exts = VIDEO_EXTENSIONS if options.media_type == "video" else IMAGE_EXTENSIONS
            candidate_indices = [
                idx for idx in candidate_indices
                if os.path.splitext(self._record_filename(idx))[1].lower() in allowed_exts
            ]
            if not candidate_indices:
                return []

        if options.excluded_db_ids:
            candidate_indices = [
                idx for idx in candidate_indices
                if self._record_db_id(idx) not in options.excluded_db_ids
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
        self._validate_catalog_model()
        expected = self.image_matrix.shape[1] if self.image_matrix is not None else None
        if expected and query_embedding.shape[1] != expected:
            raise ValueError(
                f"Model dimension mismatch: query has {query_embedding.shape[1]}, "
                f"index expects {expected}."
            )

    def _validate_catalog_model(self) -> None:
        """Reject catalogs containing embeddings from any other model."""
        if self._catalog_model_checked:
            return
        try:
            rows = self.db.get_connection().execute(
                "SELECT DISTINCT model_name FROM memes "
                "WHERE embedding IS NOT NULL AND model_name IS NOT NULL AND model_name != ''"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc).lower():
                raise
            self._catalog_model_checked = True
            return
        catalog_models = {row[0] for row in rows}
        if catalog_models and catalog_models != {self.model_name}:
            raise ValueError(
                f"The catalog contains embeddings from {', '.join(sorted(catalog_models))}, "
                f"but search is configured for {self.model_name}. Embeddings from different "
                "models are not comparable. Reindex the library or restore IRIS_MODEL to "
                "the original model."
            )
        self._catalog_model_checked = True

    def _candidate_indices(self, query_embedding: np.ndarray, candidate_pool: int) -> list[int]:
        if self.image_matrix is None:
            return list(range(len(self.records)))
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
        candidate_image_matrix = (
            self.image_matrix[candidate_indices] if self.image_matrix is not None else None
        )
        if candidate_image_matrix is None:
            image_scores = np.zeros(len(candidate_indices), dtype=np.float32)
            desc_scores: np.ndarray | None = None
        elif self.device == "cpu":
            # FAISS already runs on CPU. Avoiding Torch allocations here keeps
            # ranking responsive on low-end APUs.
            query_norm = np.linalg.norm(query_embedding[0])
            image_scores = (candidate_image_matrix @ query_embedding[0]) / np.maximum(
                np.linalg.norm(candidate_image_matrix, axis=1) * query_norm, 1e-12
            )
            desc_scores: np.ndarray | None = None
            if self.desc_matrix is not None:
                candidate_desc_matrix = self.desc_matrix[candidate_indices]
                desc_scores = (candidate_desc_matrix @ query_embedding[0]) / np.maximum(
                    np.linalg.norm(candidate_desc_matrix, axis=1) * query_norm, 1e-12
                )
        else:
            query_tensor = torch.from_numpy(query_embedding).to(self.device)
            image_tensor = torch.from_numpy(candidate_image_matrix).to(self.device).to(
                query_tensor.dtype
            )
            image_scores = _torch_cosine_similarity(
                query_tensor, image_tensor
            )[0].detach().cpu().numpy()

            desc_scores = None
            if self.desc_matrix is not None:
                desc_tensor = torch.from_numpy(self.desc_matrix[candidate_indices]).to(
                    self.device
                ).to(query_tensor.dtype)
                desc_scores = _torch_cosine_similarity(
                    query_tensor, desc_tensor
                )[0].detach().cpu().numpy()

        # One statement per batch instead of one per candidate: the loop below
        # reads text from every candidate it does not reject, and negative terms
        # read it from all of them.
        self._prefetch_text(candidate_indices)
        query_text = self._prepare_query_text(text_query, translated_query)

        scores: dict[int, float] = {}
        details: dict[int, dict[str, float | str]] = {}
        for local_idx, record_idx in enumerate(candidate_indices):
            record = self.records[record_idx]
            if self._matches_negative(record, negative_terms):
                continue

            image_score = float(image_scores[local_idx])
            has_description = desc_scores is not None and self._has_description(record_idx)
            desc_score = float(desc_scores[local_idx]) if has_description else 0.0
            has_image = self._has_image_embedding(record_idx)
            semantic_score = _weighted_mean(
                [
                    (options.balance, has_image, image_score),
                    (1.0 - options.balance, has_description, desc_score),
                ]
            )
            lexical_score = self._lexical_score(record, query_text) if text_query else 0.0
            if semantic_score is None:
                # Media accepted while AI indexing is paused still participates
                # in filename/metadata search, but never receives a fabricated
                # zero-vector semantic score.
                if not text_query or lexical_score <= 0.0:
                    continue
                score = lexical_score * options.lexical_weight
            else:
                score = semantic_score
            if text_query:
                if semantic_score is not None:
                    score += lexical_score * options.lexical_weight
                score *= self._text_multiplier(record, query_text, options.text_bonus)
            scores[record_idx] = score
            details[record_idx] = {
                "image": image_score,
                "description": desc_score,
                "semantic": semantic_score,
                "lexical": lexical_score,
                "image_available": has_image,
                "balance": options.balance,
                "lexical_weight": options.lexical_weight,
                "style": record.style,
                "source_work": record.source_work,
                "context": record.context,
                "humor": record.humor,
            }
        return scores, details

    def _prefetch_text(self, record_indices: Iterable[int]) -> None:
        """Warm the text store for a set of records, if there is one."""
        if self._text_store is None:
            return
        self._text_store.prefetch(
            self._record_db_id(index)
            for index in record_indices
            if 0 <= index < len(self.records)
        )

    def _matches_negative(self, record: IndexRecord, negative_terms: Iterable[str]) -> bool:
        if not negative_terms:
            return False
        _, content = record.normalized_text()
        return any(term and term in content for term in negative_terms)

    def _prepare_query_text(self, text_query: str, translated_query: str) -> _QueryText:
        """Everything about the query that does not depend on the item.

        Folding the query inside the per-candidate loop meant normalising the
        same handful of words once per candidate -- ten to twelve calls each,
        the largest remaining cost in a search once the item text was cached.
        """
        words = self._query_words(text_query) + self._query_words(translated_query)
        q_words = list(dict.fromkeys(words))
        return _QueryText(
            words=q_words,
            normalized=normalize_text(text_query),
            bigrams=[" ".join(q_words[i : i + 2]) for i in range(len(q_words) - 1)],
            total_weight=max(sum(len(word) for word in q_words), 1),
        )

    def _lexical_score(self, record: IndexRecord, query: _QueryText) -> float:
        q_words = query.words
        if not q_words:
            return 0.0

        fields, full_content = record.normalized_text()
        score = 0.0
        max_score = 0.0
        for position, weight in _LEXICAL_WEIGHTS:
            normalized = fields[position]
            for word in q_words:
                max_score += weight
                if word in normalized:
                    score += weight

        if query.normalized and query.normalized in full_content:
            score += 2.0
            max_score += 2.0
        return score / max(max_score, 1.0)

    def _text_multiplier(
        self, record: IndexRecord, query: _QueryText, text_bonus: float
    ) -> float:
        q_words = query.words
        if not q_words:
            return 1.0

        fields, joined = record.normalized_text()
        tags_content = fields[_TAGS]
        ocr_content = fields[_OCR]
        desc_content = " ".join(fields[position] for position in _DESCRIPTIVE)

        matched_tags = [word for word in q_words if word in tags_content]
        matched_text = [word for word in q_words if word in ocr_content or word in desc_content]
        match_score = (
            sum(len(word) for word in matched_tags) * 1.5
            + sum(len(word) for word in matched_text)
        ) / (query.total_weight * 1.5)

        multiplier = 1.0 + min(1.0, match_score) * text_bonus
        if query.bigrams:
            matched = sum(1 for bigram in query.bigrams if bigram in joined)
            multiplier += (matched / len(query.bigrams)) * text_bonus * 0.3

        if query.normalized and query.normalized in joined:
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
