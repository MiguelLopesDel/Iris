from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import easyocr
import faiss
import numpy as np
import torch
import whisper
from deep_translator import GoogleTranslator
from PIL import Image
from sentence_transformers import SentenceTransformer
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoProcessor
from transformers import logging as transformers_logging

from core import import_review
from core.deleted_registry import load_deleted_content_hashes
from core.indexer_db import (
    ensure_unique_destination,
    existing_hashes,
    find_or_create_collection,
    get_or_create_library,
    init_db,
    now_iso,
)
from core.media_inventory import (
    file_sha256,
    iter_media_files,
    read_manifest,
)
from core.media_metadata import extract_metadata
from core.search_engine import DEFAULT_MODEL, normalize_text
from core.taxonomy import (
    build_taxonomy_prompt_rows,
    classify_embedding,
    merge_taxonomy_into_profile,
    values_for_field,
)

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")   # suppress mmco/unref ffmpeg noise
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
torch.backends.cudnn.benchmark = True
warnings.filterwarnings("ignore", category=UserWarning)
transformers_logging.set_verbosity_error()

SCHEMA_VERSION = 4
DEFAULT_LIBRARY_NAME = "default"
DEFAULT_LIBRARY_ROOT = Path("data/library")


@dataclass(frozen=True)
class IndexerConfig:
    media_dir: Path
    db_path: Path
    model_name: str
    batch_size: int
    device: str
    recursive: bool
    limit: int | None
    rebuild_faiss_only: bool
    caption_model: str
    whisper_model: str
    sample_manifest: Path | None
    library_name: str
    library_root: Path
    copy_to_library: bool
    collection_name: str | None = None
    clap_model: str = "none"
    force_reimport_video_audio: bool = False


@dataclass
class LoadedModels:
    reader: easyocr.Reader
    florence_model: AutoModelForCausalLM | None
    florence_processor: AutoProcessor | None
    clip_model: SentenceTransformer
    whisper_model: whisper.Whisper | None
    dtype: torch.dtype
    taxonomy_rows: list[dict[str, str]]
    taxonomy_embeddings: np.ndarray | None
    clap_model: object | None = None
    clap_processor: object | None = None


def parse_arguments() -> IndexerConfig:
    parser = argparse.ArgumentParser(
        description="Indexador de midia do Iris",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dir", "-d", default="./media", help="Pasta com imagens e videos.")
    parser.add_argument("--db", "-b", default="iris.db", help="Banco SQLite de saida.")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, help="Modelo CLIP.")
    parser.add_argument("--batch-size", "-bs", type=int, default=8, help="Tamanho do lote.")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    parser.add_argument("--recursive", action="store_true", help="Indexa subpastas.")
    parser.add_argument("--limit", type=int, default=None, help="Limita a quantidade de arquivos.")
    parser.add_argument(
        "--sample-manifest",
        type=Path,
        default=None,
        help="Indexa somente os arquivos listados em um manifest gerado por sample_media.py.",
    )
    parser.add_argument(
        "--rebuild-faiss-only",
        action="store_true",
        help="Nao processa midias; apenas recria os indices FAISS do banco.",
    )
    parser.add_argument(
        "--caption-model",
        default="microsoft/Florence-2-large",
        help="Modelo de legenda/VQA. Use 'none' para desativar.",
    )
    parser.add_argument(
        "--whisper-model",
        default="tiny",
        help="Modelo Whisper. Use 'none' para desativar transcricao.",
    )
    parser.add_argument(
        "--library",
        default=DEFAULT_LIBRARY_NAME,
        help="Nome da biblioteca alvo para armazenamento portatil.",
    )
    parser.add_argument(
        "--library-root",
        default=str(DEFAULT_LIBRARY_ROOT),
        help="Raiz onde as bibliotecas sao armazenadas.",
    )
    parser.add_argument(
        "--copy-to-library",
        action="store_true",
        help="Copia os arquivos processados para a biblioteca antes de indexar.",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Adiciona os arquivos indexados a esta colecao (cria se nao existir).",
    )
    parser.add_argument(
        "--clap-model",
        default="none",
        help="Modelo CLAP para embeddings de audio. Use 'none' para desativar.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path("data") / db_path

    return IndexerConfig(
        media_dir=Path(args.dir),
        db_path=db_path,
        model_name=args.model,
        batch_size=args.batch_size,
        device=resolve_device(args.device),
        recursive=args.recursive,
        limit=args.limit,
        rebuild_faiss_only=args.rebuild_faiss_only,
        caption_model=args.caption_model,
        whisper_model=args.whisper_model,
        sample_manifest=args.sample_manifest,
        library_name=args.library,
        library_root=Path(args.library_root),
        copy_to_library=args.copy_to_library,
        collection_name=args.collection,
        clap_model=args.clap_model,
    )


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"



def load_models(config: IndexerConfig) -> LoadedModels:
    print(f"Carregando modelos no dispositivo: {config.device}")
    dtype = torch.float16 if config.device == "cuda" else torch.float32

    print("  -> EasyOCR")
    reader = easyocr.Reader(["pt", "en"], gpu=(config.device == "cuda"))

    florence_model = None
    florence_processor = None
    if config.caption_model.lower() != "none":
        print(f"  -> Caption/VQA: {config.caption_model}")
        try:
            florence_model = AutoModelForCausalLM.from_pretrained(
                config.caption_model,
                torch_dtype=dtype,
                trust_remote_code=True,
            ).to(config.device)
            florence_processor = AutoProcessor.from_pretrained(
                config.caption_model, trust_remote_code=True
            )
            florence_model.eval()
        except Exception as exc:
            print(f"  ! Falha ao carregar caption model: {exc}")
            print("  -> Continuando sem legendas detalhadas.")

    print(f"  -> CLIP: {config.model_name}")
    clip_model = SentenceTransformer(config.model_name, device=config.device)
    if config.device == "cuda":
        clip_model.half()
    taxonomy_rows = build_taxonomy_prompt_rows()
    taxonomy_embeddings = clip_model.encode(
        [row["prompt"] for row in taxonomy_rows],
        batch_size=32,
        show_progress_bar=False,
    ).astype(np.float32)

    whisper_model = None
    if config.whisper_model.lower() != "none":
        print(f"  -> Whisper: {config.whisper_model}")
        try:
            whisper_model = whisper.load_model(config.whisper_model, device=config.device)
        except Exception as exc:
            print(f"  ! Falha ao carregar Whisper: {exc}")
            print("  -> Videos serao indexados sem transcricao.")

    clap_model_inst = None
    clap_processor_inst = None
    if config.clap_model.lower() != "none":
        print(f"  -> CLAP: {config.clap_model}")
        try:
            from transformers import ClapModel, ClapProcessor
            clap_model_inst = ClapModel.from_pretrained(
                config.clap_model, torch_dtype=torch.float32
            ).to(config.device).eval()
            clap_processor_inst = ClapProcessor.from_pretrained(config.clap_model)
        except Exception as exc:
            print(f"  ! CLAP nao disponivel: {exc}")

    return LoadedModels(
        reader=reader,
        florence_model=florence_model,
        florence_processor=florence_processor,
        clip_model=clip_model,
        whisper_model=whisper_model,
        dtype=dtype,
        taxonomy_rows=taxonomy_rows,
        taxonomy_embeddings=taxonomy_embeddings,
        clap_model=clap_model_inst,
        clap_processor=clap_processor_inst,
    )


def already_processed(conn: sqlite3.Connection) -> set[str]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memes)")}
    column = "relative_path" if "relative_path" in columns else "arquivo"
    # Safety: column is derived from PRAGMA, but guard against future changes
    if column not in {"relative_path", "arquivo", "caminho", "storage_path"}:
        raise RuntimeError(f"Unexpected column: {column}")
    return {row[0] for row in conn.execute(f"SELECT {column} FROM memes WHERE {column} IS NOT NULL")}


_PHASH_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"})
_DEDUP_CLIP_THRESHOLD = 0.985
_DEDUP_PHASH_THRESHOLD = 8
_CANDIDATE_THUMB_SIZE = (320, 320)


def _phash_to_u64(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value, 16) & 0xFFFFFFFFFFFFFFFF
    except (ValueError, TypeError):
        return None


def _hamming_to_array(arr: np.ndarray, value: int) -> np.ndarray:
    """Vectorized Hamming distance between each uint64 in arr and a scalar value."""
    xor = (arr ^ np.uint64(value)).astype(np.uint64)
    bits = np.unpackbits(xor.view(np.uint8))
    return bits.reshape(-1, 64).sum(axis=1)


def _compute_phash(image: Image.Image) -> str | None:
    try:
        import imagehash
        return str(imagehash.phash(image))
    except Exception:
        return None


def _candidate_thumb_bytes(image: Image.Image) -> bytes | None:
    """Small JPEG of the candidate, stored so the review panel survives source moves."""
    try:
        import io
        thumb = image.convert("RGB")
        thumb.thumbnail(_CANDIDATE_THUMB_SIZE, Image.LANCZOS)
        buffer = io.BytesIO()
        thumb.save(buffer, format="JPEG", quality=80, optimize=True)
        return buffer.getvalue()
    except Exception:
        return None


def _phash_score(distance: int) -> float:
    """Map Hamming distance (0..8) to a 0.99..1.0 confidence for display."""
    return round(1.0 - (distance / 640.0), 4)


@dataclass
class _DedupContext:
    """In-memory snapshot of existing memes used to detect duplicates during import.

    Holds content_hash → meme_id, a uint64 perceptual-hash array, and a CLIP FAISS
    index. Updated as new files are inserted so candidates also dedup against each
    other within the same run.
    """

    hash_to_meme: dict[str, int]
    phash_u64: np.ndarray
    phash_ids: np.ndarray
    deleted_phash_u64: np.ndarray
    deleted_phash_ids: np.ndarray
    clip_index: object | None
    clip_ids: list[int]
    clip_threshold: float = _DEDUP_CLIP_THRESHOLD
    phash_threshold: int = _DEDUP_PHASH_THRESHOLD

    def nearest_phash(self, phash: str | None) -> tuple[int, int] | None:
        u = _phash_to_u64(phash)
        if u is None or self.phash_u64.size == 0:
            return None
        dist = _hamming_to_array(self.phash_u64, u)
        j = int(dist.argmin())
        if int(dist[j]) <= self.phash_threshold:
            return int(self.phash_ids[j]), int(dist[j])
        return None

    def nearest_deleted_phash(self, phash: str | None) -> int | None:
        u = _phash_to_u64(phash)
        if u is None or self.deleted_phash_u64.size == 0:
            return None
        dist = _hamming_to_array(self.deleted_phash_u64, u)
        j = int(dist.argmin())
        if int(dist[j]) <= self.phash_threshold:
            return int(self.deleted_phash_ids[j])
        return None

    def nearest_clip(self, embedding: np.ndarray) -> tuple[int, float] | None:
        if self.clip_index is None:
            return None
        query = np.asarray(embedding, dtype=np.float32).reshape(1, -1).copy()
        faiss.normalize_L2(query)
        scores, indices = self.clip_index.search(query, 1)
        j = int(indices[0][0])
        score = float(scores[0][0])
        if j >= 0 and score >= self.clip_threshold:
            return int(self.clip_ids[j]), score
        return None

    def add(
        self,
        meme_id: int,
        content_hash: str | None,
        phash: str | None,
        embedding: np.ndarray | None,
    ) -> None:
        if content_hash:
            self.hash_to_meme.setdefault(content_hash, meme_id)
        u = _phash_to_u64(phash)
        if u is not None:
            self.phash_u64 = np.append(self.phash_u64, np.uint64(u))
            self.phash_ids = np.append(self.phash_ids, np.int64(meme_id))
        if self.clip_index is not None and embedding is not None:
            query = np.asarray(embedding, dtype=np.float32).reshape(1, -1).copy()
            faiss.normalize_L2(query)
            self.clip_index.add(query)
            self.clip_ids.append(meme_id)


def _build_dedup_context(conn: sqlite3.Connection) -> _DedupContext:
    """Snapshot existing memes (hash → id, phash array, CLIP FAISS) for the dedup gates.

    Note: loads all image embeddings into RAM (≈150MB for ~50k @ 768-d), mirroring
    what the search backend already holds. Built once at the start of an import.
    """
    hash_to_meme: dict[str, int] = {}
    ph_u64: list[int] = []
    ph_ids: list[int] = []
    embeddings: list[np.ndarray] = []
    clip_ids: list[int] = []
    # Tolerate older schemas missing perceptual_hash/embedding columns.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memes)")}
    chash_col = "content_hash" if "content_hash" in cols else "NULL"
    phash_col = "perceptual_hash" if "perceptual_hash" in cols else "NULL"
    emb_col = "embedding" if "embedding" in cols else "NULL"
    for mid, chash, phash, embedding in conn.execute(
        f"SELECT id, {chash_col}, {phash_col}, {emb_col} FROM memes"
    ):
        if chash:
            hash_to_meme.setdefault(chash, int(mid))
        u = _phash_to_u64(phash)
        if u is not None:
            ph_u64.append(u)
            ph_ids.append(int(mid))
        if embedding:
            embeddings.append(np.frombuffer(embedding, dtype=np.float32))
            clip_ids.append(int(mid))

    del_u64: list[int] = []
    del_ids: list[int] = []
    try:
        for did, dphash in conn.execute(
            "SELECT id, perceptual_hash FROM deleted_media WHERE perceptual_hash IS NOT NULL"
        ):
            u = _phash_to_u64(dphash)
            if u is not None:
                del_u64.append(u)
                del_ids.append(int(did))
    except sqlite3.OperationalError:
        pass

    clip_index = None
    if embeddings:
        matrix = np.vstack(embeddings).astype(np.float32)
        faiss.normalize_L2(matrix)
        clip_index = faiss.IndexFlatIP(matrix.shape[1])
        clip_index.add(matrix)

    return _DedupContext(
        hash_to_meme=hash_to_meme,
        phash_u64=np.array(ph_u64, dtype=np.uint64),
        phash_ids=np.array(ph_ids, dtype=np.int64),
        deleted_phash_u64=np.array(del_u64, dtype=np.uint64),
        deleted_phash_ids=np.array(del_ids, dtype=np.int64),
        clip_index=clip_index,
        clip_ids=clip_ids,
    )


class ImportSourceUnavailable(OSError):
    """Raised when an import source folder is missing/unreadable (e.g. unmounted).

    Signals a *pausable* condition: the job should be marked interrupted (not failed)
    and resumed later, picking up where it stopped via the dedup/ledger skip-sets.
    """


def process_images(
    config: IndexerConfig,
    progress_callback: Callable[[int, int, str], None] | None = None,
    *,
    job_id: str | None = None,
    dedup_enabled: bool = True,
    explicit_files: list[Path] | None = None,
) -> dict[str, int]:
    if not config.media_dir.exists():
        # Don't sys.exit: this runs in a server background thread. Signal an
        # unavailable source so the caller can pause (not fail) and resume later
        # — e.g. the folder was unmounted mid-import.
        raise ImportSourceUnavailable(str(config.media_dir))

    conn = init_db(config.db_path)
    processed = already_processed(conn)
    known_hashes = existing_hashes(conn)
    _deleted_hashes = load_deleted_content_hashes(conn)

    # Dedup gates: disabled when importing into a named album (everything is wanted
    # there) or when forcing an explicit re-index of specific files.
    dedup = (
        _build_dedup_context(conn)
        if (dedup_enabled and config.collection_name is None and explicit_files is None)
        else None
    )
    quarantined_paths = import_review.pending_paths(conn) if dedup is not None else set()

    if explicit_files is not None:
        media_files = [p for p in explicit_files if p.exists()]
    else:
        media_files = media_files_from_config(config)
        if config.limit:
            media_files = media_files[: config.limit]

    if explicit_files is not None:
        # Forced re-index of specific files (e.g. "import anyway" from the review panel).
        pending = media_files
    elif config.copy_to_library:
        if config.force_reimport_video_audio:
            # Video/audio: always included (force re-process even if already indexed).
            # Images: only new ones (not already in processed) — don't re-process existing.
            pending = [
                path for path in media_files
                if path.suffix.lower() in _FORCE_REIMPORT_EXTS
                or (path.relative_to(config.media_dir).as_posix() not in processed
                    and path.name not in processed)
            ]
        else:
            pending = media_files
    else:
        pending = [
            path for path in media_files
            if (path.relative_to(config.media_dir).as_posix() not in processed
                and path.name not in processed)
            or (config.force_reimport_video_audio
                and path.suffix.lower() in _FORCE_REIMPORT_EXTS)
        ]

    # Resume: skip files already sitting in the review queue.
    if quarantined_paths:
        pending = [path for path in pending if str(path.resolve()) not in quarantined_paths]

    print(f"Diretorio: {config.media_dir}")
    print(f"Arquivos encontrados: {len(media_files)}")
    print(f"Arquivos novos: {len(pending)}")

    _imported = 0
    _quarantined = 0
    _skipped_ledger = 0

    def _record_ledger(src: Path, st, chash: str | None, outcome: str, detection: str = "") -> None:
        # Remember this file's fate so a future re-import skips it after one stat().
        if dedup is None or st is None:
            return
        import_review.ledger_record(
            conn, path=str(src), name=src.name, size=st.st_size, mtime=st.st_mtime,
            content_hash=chash, outcome=outcome, detection=detection,
        )

    if not pending:
        conn.close()
        if job_id is not None:
            with sqlite3.connect(config.db_path) as _jc:
                import_review.update_job(_jc, job_id, total=0, done=0)
        return {"imported": 0, "quarantined": 0, "processed": 0, "skipped": 0}

    # Log de falhas — ao lado do banco de dados
    _failed_log: Path = config.db_path.with_name(config.db_path.stem + "_failed.txt")
    _failed_entries: list[str] = []

    # Configura o álbum antes do loop — assim cada batch é atribuído imediatamente ao commit
    _collection_id: int | None = None
    _last_assigned_id: int = 0
    if config.collection_name:
        row = conn.execute("SELECT MAX(id) FROM memes").fetchone()
        _last_assigned_id = int(row[0]) if row and row[0] is not None else 0
        _collection_id = find_or_create_collection(conn, config.collection_name)

    library_root = (config.library_root / config.library_name).resolve()
    library_id = get_or_create_library(conn, config.library_name, library_root)
    models = load_models(config)
    cursor = conn.cursor()

    _proc_done = 0
    _proc_total = len(pending)

    try:
        with torch.inference_mode():
            for start in tqdm(range(0, _proc_total, config.batch_size), desc="Indexando"):
                batch_files = pending[start : start + config.batch_size]
                batch_images: list[Image.Image] = []
                batch_metadata: list[dict[str, object]] = []
                _existing_col_ids: list[int] = []  # IDs já indexados a adicionar ao álbum

                for path in batch_files:
                    if progress_callback is not None:
                        progress_callback(_proc_done, _proc_total, path.name)
                    _proc_done += 1
                    try:
                        source_path = path.resolve()
                        _is_force = (
                            config.force_reimport_video_audio
                            and source_path.suffix.lower() in _FORCE_REIMPORT_EXTS
                        )
                        # Fast ledger short-circuit: a single stat() decides whether
                        # we've already handled this exact file (same path/size/mtime).
                        # Skips the full SHA-256 read + decode for unchanged re-imports.
                        _st = source_path.stat() if dedup is not None else None
                        if dedup is not None and not _is_force and _st is not None:
                            _led = import_review.ledger_lookup(conn, str(source_path))
                            if (
                                _led is not None
                                and int(_led["size"]) == int(_st.st_size)
                                and int(_led["mtime"]) == int(_st.st_mtime)
                            ):
                                _skipped_ledger += 1
                                continue
                        content_hash = file_sha256(source_path)
                        if content_hash in known_hashes:
                            if _is_force:
                                # Remove the old record so the new INSERT creates a fresh one
                                conn.execute(
                                    "DELETE FROM memes WHERE content_hash = ?", (content_hash,)
                                )
                                known_hashes.discard(content_hash)
                            elif _collection_id is not None:
                                # Importing into an album: register the existing meme, don't quarantine.
                                row = conn.execute(
                                    "SELECT id FROM memes WHERE content_hash = ?", (content_hash,)
                                ).fetchone()
                                if row:
                                    _existing_col_ids.append(int(row[0]))
                                continue
                            elif dedup is not None:
                                # Exact byte-for-byte copy → review queue (not silent skip).
                                import_review.quarantine_candidate(
                                    conn, job_id=job_id, candidate_path=str(source_path),
                                    candidate_hash=content_hash, candidate_phash=None,
                                    candidate_thumb=None, detection="exact_hash",
                                    match_meme_id=dedup.hash_to_meme.get(content_hash), score=1.0,
                                )
                                _record_ledger(source_path, _st, content_hash, "quarantined", "exact_hash")
                                _quarantined += 1
                                continue
                            else:
                                continue
                        if content_hash in _deleted_hashes and not _is_force:
                            if dedup is not None:
                                import_review.quarantine_candidate(
                                    conn, job_id=job_id, candidate_path=str(source_path),
                                    candidate_hash=content_hash, candidate_phash=None,
                                    candidate_thumb=None, detection="deleted_registry", score=1.0,
                                )
                                _record_ledger(source_path, _st, content_hash, "quarantined", "deleted_registry")
                                _quarantined += 1
                            continue

                        # Perceptual-hash gates (images) BEFORE the expensive pipeline + copy.
                        candidate_phash: str | None = None
                        candidate_thumb: bytes | None = None
                        if (
                            dedup is not None
                            and not _is_force
                            and source_path.suffix.lower() in _PHASH_IMAGE_EXTS
                        ):
                            try:
                                _probe_img = Image.open(source_path).convert("RGB")
                            except Exception:
                                _probe_img = None
                            if _probe_img is not None:
                                candidate_phash = _compute_phash(_probe_img)
                                _ph_hit = dedup.nearest_phash(candidate_phash)
                                if _ph_hit is not None:
                                    _mid, _dist = _ph_hit
                                    import_review.quarantine_candidate(
                                        conn, job_id=job_id, candidate_path=str(source_path),
                                        candidate_hash=content_hash, candidate_phash=candidate_phash,
                                        candidate_thumb=_candidate_thumb_bytes(_probe_img),
                                        detection="perceptual", match_meme_id=_mid,
                                        score=_phash_score(_dist),
                                    )
                                    _record_ledger(source_path, _st, content_hash, "quarantined", "perceptual")
                                    _quarantined += 1
                                    continue
                                _del_hit = dedup.nearest_deleted_phash(candidate_phash)
                                if _del_hit is not None:
                                    import_review.quarantine_candidate(
                                        conn, job_id=job_id, candidate_path=str(source_path),
                                        candidate_hash=content_hash, candidate_phash=candidate_phash,
                                        candidate_thumb=_candidate_thumb_bytes(_probe_img),
                                        detection="deleted_registry", match_deleted_id=_del_hit,
                                        score=1.0,
                                    )
                                    _record_ledger(source_path, _st, content_hash, "quarantined", "deleted_registry")
                                    _quarantined += 1
                                    continue
                                candidate_thumb = _candidate_thumb_bytes(_probe_img)

                        try:
                            relative_from_source = source_path.relative_to(config.media_dir.resolve()).as_posix()
                        except ValueError:
                            relative_from_source = source_path.name

                        indexed_path = source_path
                        storage_path: str | None = None
                        if config.copy_to_library:
                            destination = ensure_unique_destination(
                                library_root=library_root,
                                relative_path=relative_from_source,
                                content_hash=content_hash,
                            )
                            if not destination.exists():
                                shutil.copy2(source_path, destination)
                            indexed_path = destination.resolve()
                            storage_path = indexed_path.relative_to(library_root).as_posix()
                        image, audio_text = load_media_preview(indexed_path, models, config)
                        extracted_text = extract_text(indexed_path, image, audio_text, models)
                        ocr_en = translate_text(extracted_text)
                        visual, tags = describe_image(image, models, config)
                        visual_profile = build_visual_profile(
                            image=image,
                            ocr=extracted_text,
                            ocr_en=ocr_en,
                            visual=visual,
                            tags=tags,
                        )
                        stat = path.stat()
                        # Videos: pre-compute multi-frame embedding instead of batching
                        # the single midpoint frame. Audio files use the placeholder image
                        # (all same embedding) and are added to the batch as-is.
                        _file_suffix = indexed_path.suffix.lower()
                        _audio_fp: str | None = None
                        _audio_emb: np.ndarray | None = None
                        if _file_suffix in (_AUDIO_ONLY_EXTS | frozenset({".ogg", ".og"})):
                            _audio_fp = _chromaprint_file(indexed_path)
                            if models.clap_model is not None and models.clap_processor is not None:
                                _audio_emb = _compute_clap_embedding(
                                    indexed_path, models.clap_model,
                                    models.clap_processor, config.device,
                                )
                        _is_video_file = _file_suffix in (_VIDEO_EXTS | frozenset({".ogg"}))
                        if _is_video_file:
                            _precomp = _compute_video_multi_frame_embedding(
                                indexed_path, models.clip_model
                            )
                        else:
                            _precomp = None

                        # Perceptual hash — images only (not video/audio).
                        # Reuse the one already computed by the dedup gate when available.
                        _perceptual_hash: str | None = candidate_phash
                        _is_audio = _file_suffix in (_AUDIO_ONLY_EXTS | frozenset({".ogg", ".og"}))
                        if _perceptual_hash is None and not _is_video_file and not _is_audio:
                            _perceptual_hash = _compute_phash(image)

                        if _precomp is None:
                            # Image, audio, or video with no useful frames → normal batch
                            batch_images.append(image)
                        else:
                            # Video with multi-frame embedding: add a sentinel so batch
                            # index stays aligned; real embedding comes from _precomp
                            batch_images.append(None)

                        try:
                            _media_meta = extract_metadata(source_path)
                            _metadata_json = json.dumps(_media_meta, ensure_ascii=False)
                        except Exception:
                            _metadata_json = ""

                        batch_metadata.append(
                            {
                                "path": indexed_path,
                                "source_path": str(source_path),
                                "relative_path": relative_from_source,
                                "storage_path": storage_path,
                                "library_id": library_id if config.copy_to_library else None,
                                "ocr": extracted_text,
                                "ocr_en": ocr_en,
                                "visual": visual,
                                "tags": tags,
                                "visual_profile": visual_profile,
                                "content_hash": content_hash,
                                "file_size": stat.st_size,
                                "file_mtime": stat.st_mtime,
                                "precomp_embedding": _precomp,
                                "audio_fingerprint": _audio_fp,
                                "audio_embedding": _audio_emb,
                                "perceptual_hash": _perceptual_hash,
                                "candidate_thumb": candidate_thumb,
                                "metadata_json": _metadata_json,
                            }
                        )
                    except ImportSourceUnavailable:
                        raise
                    except Exception as exc:
                        # If the source folder vanished (e.g. unmounted mid-import),
                        # pause instead of logging thousands of per-file errors.
                        if not config.media_dir.exists():
                            raise ImportSourceUnavailable(str(config.media_dir)) from exc
                        msg = f"{path.name}: {exc}"
                        print(f"\nErro ao processar {msg}")
                        _failed_entries.append(str(path))
                    finally:
                        if config.device == "cuda":
                            torch.cuda.empty_cache()

                if not batch_images:
                    # Whole batch was duplicates and/or already-indexed: nothing to encode,
                    # but the quarantine rows (and album links) written above MUST be flushed.
                    # Critical when most files are dupes — otherwise their review rows are lost.
                    if _collection_id is not None and _existing_col_ids:
                        conn.executemany(
                            "INSERT OR IGNORE INTO media_collections"
                            " (meme_id, collection_id, added_at) VALUES (?,?,?)",
                            [(_id, _collection_id, now_iso()) for _id in _existing_col_ids],
                        )
                    conn.commit()
                    if job_id is not None:
                        import_review.update_job(
                            conn, job_id, total=_proc_total, done=_proc_done,
                            imported=_imported, quarantined=_quarantined,
                            message=f"Importadas {_imported} · em revisão {_quarantined}",
                        )
                    continue

                # Encode only non-sentinel images (videos use pre-computed embeddings)
                _batch_real = [(i, img) for i, img in enumerate(batch_images) if img is not None]
                if _batch_real:
                    _real_indices, _real_imgs = zip(*_batch_real, strict=True)
                    _clip_out = models.clip_model.encode(
                        list(_real_imgs),
                        batch_size=len(_real_imgs),
                        show_progress_bar=False,
                    )
                    _clip_map: dict[int, np.ndarray] = dict(zip(_real_indices, _clip_out, strict=True))
                else:
                    _clip_map = {}

                # Merge: use pre-computed for videos, CLIP batch output for the rest
                image_embeddings_list: list[np.ndarray] = []
                for i, item in enumerate(batch_metadata):
                    if item.get("precomp_embedding") is not None:
                        image_embeddings_list.append(item["precomp_embedding"])
                    else:
                        image_embeddings_list.append(_clip_map[i].astype(np.float32))
                image_embeddings = np.array(image_embeddings_list, dtype=np.float32)

                del batch_images, _clip_map

                text_inputs = [
                    f"Meme Category/Tags: {item['tags']}. Text: {item['ocr_en']}. Context: {item['visual']}"
                    for item in batch_metadata
                ]
                desc_embeddings = models.clip_model.encode(
                    text_inputs,
                    batch_size=len(text_inputs),
                    show_progress_bar=False,
                )

                for idx, item in enumerate(batch_metadata):
                    path = Path(item["path"])
                    image_embedding = image_embeddings[idx].astype(np.float32)
                    desc_embedding = desc_embeddings[idx].astype(np.float32)

                    # CLIP similarity gate: visually-similar to an existing meme
                    # (not caught by hash/phash) → review queue instead of insert.
                    if dedup is not None:
                        _clip_hit = dedup.nearest_clip(image_embedding)
                        if _clip_hit is not None:
                            _mid, _score = _clip_hit
                            import_review.quarantine_candidate(
                                conn, job_id=job_id, candidate_path=item["source_path"],
                                candidate_hash=item["content_hash"],
                                candidate_phash=item.get("perceptual_hash"),
                                candidate_thumb=item.get("candidate_thumb"),
                                detection="clip_similarity", match_meme_id=_mid, score=_score,
                            )
                            if dedup is not None:
                                import_review.ledger_record(
                                    conn, path=str(item["source_path"]),
                                    name=os.path.basename(str(item["source_path"])),
                                    size=int(item["file_size"]), mtime=item["file_mtime"],
                                    content_hash=str(item["content_hash"]),
                                    outcome="quarantined", detection="clip_similarity",
                                )
                            _quarantined += 1
                            continue

                    taxonomy_matches = (
                        classify_embedding(
                            image_embedding,
                            models.taxonomy_embeddings,
                            models.taxonomy_rows,
                            text_content=(
                                f"{path.name} {item['ocr']} {item['ocr_en']} "
                                f"{item['visual']} {item['tags']}"
                            ),
                        )
                        if models.taxonomy_embeddings is not None
                        else []
                    )
                    visual_profile = merge_taxonomy_into_profile(
                        json.dumps(item["visual_profile"], ensure_ascii=False),
                        taxonomy_matches,
                    )
                    style = values_for_field(
                        taxonomy_matches,
                        "style",
                        str(visual_profile.get("style", "")),
                    )
                    source_work = values_for_field(
                        taxonomy_matches,
                        "source_work",
                        str(visual_profile.get("source_work", "")),
                    )
                    humor = values_for_field(
                        taxonomy_matches,
                        "humor",
                        str(visual_profile.get("humor", "")),
                    )
                    context = values_for_field(
                        taxonomy_matches,
                        "context",
                        str(visual_profile.get("context", "")),
                    )
                    tags = values_for_field(taxonomy_matches, "style", str(item["tags"]))
                    tags = values_for_field(taxonomy_matches, "source_work", tags)
                    tags = values_for_field(taxonomy_matches, "context", tags)
                    full_description = f"Tags: {item['tags']}. Visual: {item['visual']}"
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO memes (
                            arquivo, caminho, relative_path, storage_path, source_path, library_id, imported_at,
                            file_size, file_mtime,
                            texto_extraido, descricao_ia, tags, content_hash,
                            ocr_normalized, visual_json, objects, style, source_work,
                            humor, context, error_message, model_name,
                            embedding_dim, schema_version, embedding, desc_embedding,
                            audio_fingerprint, audio_embedding, perceptual_hash, metadata_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["relative_path"],
                            str(path.resolve()),
                            item["relative_path"],
                            item["storage_path"],
                            item["source_path"],
                            item["library_id"],
                            now_iso(),
                            item["file_size"],
                            item["file_mtime"],
                            item["ocr"],
                            full_description,
                            tags,
                            item["content_hash"],
                            visual_profile["ocr_normalized"],
                            json.dumps(visual_profile, ensure_ascii=False),
                            visual_profile["objects"],
                            style,
                            source_work,
                            humor,
                            context,
                            "",
                            config.model_name,
                            int(image_embedding.shape[0]),
                            SCHEMA_VERSION,
                            image_embedding.tobytes(),
                            desc_embedding.tobytes(),
                            item.get("audio_fingerprint"),
                            item["audio_embedding"].tobytes() if item.get("audio_embedding") is not None else None,
                            item.get("perceptual_hash"),
                            item.get("metadata_json", ""),
                        ),
                    )
                    known_hashes.add(str(item["content_hash"]))
                    _imported += 1
                    if dedup is not None:
                        import_review.ledger_record(
                            conn, path=str(item["source_path"]),
                            name=os.path.basename(str(item["source_path"])),
                            size=int(item["file_size"]), mtime=item["file_mtime"],
                            content_hash=str(item["content_hash"]), outcome="imported",
                        )
                        dedup.add(
                            int(cursor.lastrowid), str(item["content_hash"]),
                            item.get("perceptual_hash"), image_embedding,
                        )
                conn.commit()

                if job_id is not None:
                    import_review.update_job(
                        conn, job_id, total=_proc_total, done=_proc_done,
                        imported=_imported, quarantined=_quarantined,
                        message=f"Importadas {_imported} · em revisão {_quarantined}",
                    )

                # Atribui ao álbum — novos indexados + já existentes que foram pulados
                if _collection_id is not None:
                    batch_new_ids = [
                        row[0]
                        for row in conn.execute(
                            "SELECT id FROM memes WHERE id > ?", (_last_assigned_id,)
                        )
                    ]
                    all_for_collection = batch_new_ids + _existing_col_ids
                    if all_for_collection:
                        conn.executemany(
                            "INSERT OR IGNORE INTO media_collections"
                            " (meme_id, collection_id, added_at) VALUES (?,?,?)",
                            [(mid, _collection_id, now_iso()) for mid in all_for_collection],
                        )
                        conn.commit()
                    if batch_new_ids:
                        _last_assigned_id = max(batch_new_ids)

                if config.device == "cuda":
                    torch.cuda.empty_cache()
    finally:
        conn.close()
        # Release all model weights from VRAM now that indexing is done.
        # Critical when indexing runs in a background thread beside the application server.
        del models
        gc.collect()
        if config.device == "cuda":
            torch.cuda.empty_cache()
        if _failed_entries:
            with _failed_log.open("a", encoding="utf-8") as fh:
                fh.write(f"\n# Sessao {dt.datetime.now().isoformat(timespec='seconds')}\n")
                fh.writelines(f"{p}\n" for p in _failed_entries)
            print(f"\n{len(_failed_entries)} arquivo(s) com erro gravado(s) em: {_failed_log}")

    return {
        "imported": _imported,
        "quarantined": _quarantined,
        "processed": _proc_done,
        "skipped": _skipped_ledger,
    }


def media_files_from_config(config: IndexerConfig) -> list[Path]:
    if not config.sample_manifest:
        return iter_media_files(config.media_dir, config.recursive)

    manifest_media_dir, items = read_manifest(config.sample_manifest)
    config_root = config.media_dir.resolve()
    manifest_root = manifest_media_dir.resolve()
    media_root = manifest_root if manifest_root.exists() else config_root
    paths = []
    for item in items:
        path = Path(item.path)
        if not path.exists():
            path = media_root / item.relative_path
        if path.exists():
            paths.append(path)
    return sorted(paths)


def has_audio_stream(path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return True


_AUDIO_ONLY_EXTS = frozenset({".mp3"})
_VIDEO_EXTS = frozenset({".mp4", ".webm", ".mkv", ".mov"})
# Extensions eligible for force-reimport (video + audio — images are excluded by design)
_FORCE_REIMPORT_EXTS = frozenset({
    ".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv", ".ogg", ".og",
    ".mp3", ".opus", ".flac", ".wav", ".aac", ".m4a",
})


def _load_svg_as_image(path: Path) -> Image.Image:
    """Rasteriza SVG para PIL Image usando cairosvg (ou fallback cinza)."""
    try:
        import io as _io

        import cairosvg  # type: ignore[import-untyped]
        png_bytes = cairosvg.svg2png(url=str(path), output_width=512, output_height=512)
        return Image.open(_io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        pass
    return Image.new("RGB", (512, 512), color=(200, 200, 210))


def _audio_placeholder() -> Image.Image:
    """Imagem placeholder 224x224 para arquivos de áudio sem vídeo."""
    img = Image.new("RGB", (224, 224), color=(25, 25, 45))
    return img


def _compute_video_multi_frame_embedding(
    path: Path,
    clip_model: SentenceTransformer,
    n_frames: int = 6,
) -> np.ndarray | None:
    """Average CLIP embedding of N evenly-spaced meaningful frames.

    More robust than single-frame for:
    - Videos where the midpoint is dark/black (ads, intros)
    - Trimmed copies of the same video (shifted timeline)
    - Re-encoded copies (same frames, different container/bitrate)

    Returns None if no meaningful frames are found (video will fall back to placeholder).
    """
    cap = cv2.VideoCapture(str(path))
    total = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 1)
    # Sample evenly, skip first/last 10 % to avoid title cards and fade-outs
    margin = max(int(total * 0.10), 1)
    positions = [
        margin + int((total - 2 * margin) * i / max(n_frames - 1, 1))
        for i in range(n_frames)
    ]
    frames: list[Image.Image] = []
    for pos in positions:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(pos, 0))
        ok, frame = cap.read()
        if ok and _is_meaningful_frame(frame):
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            _cap_image_size(img)
            frames.append(img)
    cap.release()
    if not frames:
        return None
    embeddings = clip_model.encode(frames, batch_size=len(frames), show_progress_bar=False)
    avg = np.array(embeddings, dtype=np.float32).mean(axis=0)
    norm = float(np.linalg.norm(avg))
    if norm > 0:
        avg /= norm
    return avg


def _is_meaningful_frame(frame_bgr: object) -> bool:
    """True if the frame has real visual content — not a blank/uniform/noise frame.
    OGG OPUS audio files often report CAP_PROP_FRAME_COUNT > 0 in cv2 but return
    a blank or garbage frame. Reject those so they fall back to _audio_placeholder(),
    giving them the same CLIP embedding as other audio-only files.
    """
    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        mean = float(gray.mean())
        std = float(gray.std())
        return std > 5.0 and 5.0 < mean < 250.0
    except Exception:
        return False


def _chromaprint_file(path: Path) -> str | None:
    """Generate Chromaprint fingerprint. Returns None if acoustid/fpcalc unavailable."""
    try:
        import acoustid
        _dur, fp = acoustid.fingerprint_file(str(path))
        return fp
    except Exception:
        return None


def _compute_clap_embedding(
    path: Path,
    clap_model: object,
    clap_processor: object,
    device: str,
) -> np.ndarray | None:
    """Compute 512-dim CLAP audio embedding. Uses whisper.load_audio for format support."""
    try:
        import whisper as _whisper
        audio_arr = _whisper.load_audio(str(path))  # mono float32 at 16kHz
        inputs = clap_processor(
            audios=[audio_arr],
            sampling_rate=16000,
            return_tensors="pt",
        ).to(device)
        import torch as _torch
        with _torch.no_grad():
            emb = clap_model.get_audio_features(**inputs)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        return emb.cpu().float().numpy()[0]
    except Exception:
        return None


def _whisper_transcribe(path: Path, models: LoadedModels, config: IndexerConfig) -> str:
    if not models.whisper_model:
        return ""
    try:
        result = models.whisper_model.transcribe(str(path), fp16=(config.device == "cuda"))
        return result["text"].strip()
    except Exception as exc:
        print(f"  ! Whisper falhou em {path.name}: {exc}")
        return ""


def load_media_preview(
    path: Path, models: LoadedModels, config: IndexerConfig
) -> tuple[Image.Image, str]:
    suffix = path.suffix.lower()

    if suffix == ".svg":
        img = _load_svg_as_image(path)
        _cap_image_size(img)
        return img, ""

    if suffix in _AUDIO_ONLY_EXTS:
        text = _whisper_transcribe(path, models, config)
        return _audio_placeholder(), f"[Audio: {text}]" if text else "[Audio]"

    if suffix in _VIDEO_EXTS:
        cap = cv2.VideoCapture(str(path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(total_frames // 2, 0))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            raise ValueError("falha ao ler frame do video")
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        _cap_image_size(image)
        audio_text = ""
        if models.whisper_model and has_audio_stream(path):
            text = _whisper_transcribe(path, models, config)
            if text:
                audio_text = f" [Audio: {text}]"
        return image, audio_text

    if suffix == ".ogg":
        cap = cv2.VideoCapture(str(path))
        has_video = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) > 0
        if has_video:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) // 2, 0))
            ok, frame = cap.read()
            cap.release()
            # OGG OPUS audio files report non-zero frame count but return blank/garbage
            # frames — reject those so all audio-only OGGs use the same placeholder
            if ok and _is_meaningful_frame(frame):
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                _cap_image_size(image)
                text = _whisper_transcribe(path, models, config) if models.whisper_model else ""
                return image, f" [Audio: {text}]" if text else ""
        else:
            cap.release()
        text = _whisper_transcribe(path, models, config)
        return _audio_placeholder(), f"[Audio: {text}]" if text else "[Audio]"

    # Imagens estáticas e GIFs — abre apenas o frame 0
    try:
        img = Image.open(path)
        if hasattr(img, "n_frames") and img.n_frames > 1:
            img.seek(0)
        img = img.convert("RGB")
    except Exception:
        # Fallback: alguns WebP/GIF com codificação incomum abrem via cv2
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f"PIL e cv2 nao conseguiram abrir {path.name}") from None
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    _cap_image_size(img)
    return img, ""


_MAX_DIM = 1024  # limite para evitar OOM — reduzido para suportar GPU compartilhada com o engine


def _cap_image_size(img: Image.Image) -> None:
    """Redimensiona in-place se maior que _MAX_DIM (modifica o objeto PIL)."""
    if max(img.size) > _MAX_DIM:
        img.thumbnail((_MAX_DIM, _MAX_DIM), Image.LANCZOS)


def extract_text(path: Path, image: Image.Image, audio_text: str, models: LoadedModels) -> str:
    suffix = path.suffix.lower()
    if suffix in _AUDIO_ONLY_EXTS:
        return audio_text.strip()
    if suffix == ".ogg" and audio_text.startswith("[Audio"):
        return audio_text.strip()
    # Sempre usa o numpy array da imagem já carregada (RGB, frame único).
    # Isso evita que EasyOCR tente abrir SVG/GIF animado/imagens de 2 canais via PIL.
    text_results = models.reader.readtext(np.array(image), detail=0, paragraph=True)
    return (" ".join(text_results).strip() + audio_text).strip()


def translate_text(text: str) -> str:
    if not text:
        return text
    try:
        return GoogleTranslator(source="auto", target="en").translate(text)
    except Exception:
        return text


def build_visual_profile(
    *,
    image: Image.Image,
    ocr: str,
    ocr_en: str,
    visual: str,
    tags: str,
) -> dict[str, str | int]:
    width, height = image.size
    tags_clean = "" if tags == "N/A" else tags
    visual_clean = "" if visual == "N/A" else visual
    joined = normalize_text(f"{tags_clean} {visual_clean} {ocr} {ocr_en}")

    style = infer_style(joined, width, height)
    humor = infer_humor(joined)
    context = infer_context(joined)
    source_work = infer_source_work(joined)
    objects = infer_objects(joined, tags_clean, visual_clean)

    return {
        "schema_version": SCHEMA_VERSION,
        "width": width,
        "height": height,
        "ocr_normalized": normalize_text(ocr),
        "ocr_translated": ocr_en,
        "caption": visual_clean,
        "tags": tags_clean,
        "objects": objects,
        "style": style,
        "source_work": source_work,
        "humor": humor,
        "context": context,
    }


def infer_style(content: str, width: int, height: int) -> str:
    if any(word in content for word in ["screenshot", "youtube", "shorts", "subscribe"]):
        return "screenshot/social-video"
    if any(word in content for word in ["anime", "manga", "jujutsu", "chainsaw", "gojo"]):
        return "anime/manga"
    if any(word in content for word in ["cartoon", "comic", "illustration"]):
        return "cartoon/comic"
    if width and height and height / max(width, 1) > 1.7:
        return "vertical-phone"
    return "unknown"


def infer_humor(content: str) -> str:
    if any(word in content for word in ["ironia", "sarcasm", "sarcastic"]):
        return "sarcasm"
    if any(word in content for word in ["reaction", "reacao", "disappointed", "angry"]):
        return "reaction"
    if any(word in content for word in ["meme", "kkkk", "lol"]):
        return "meme"
    return "unknown"


def infer_context(content: str) -> str:
    if any(word in content for word in ["politica", "regime", "guerra", "imperio"]):
        return "history/politics"
    if any(word in content for word in ["youtube", "shorts", "subscribe"]):
        return "youtube"
    if any(word in content for word in ["music", "track", "song", "banda"]):
        return "music"
    return "unknown"


def infer_source_work(content: str) -> str:
    candidates = {
        "jujutsu kaisen": ["jujutsu", "sukuna", "gojo"],
        "chainsaw man": ["chainsaw", "makima", "denji", "power"],
        "minecraft": ["minecraft"],
        "youtube": ["youtube", "shorts", "subscribe"],
    }
    for source, needles in candidates.items():
        if any(needle in content for needle in needles):
            return source
    return "unknown"


def infer_objects(content: str, tags: str, visual: str) -> str:
    candidates = [
        "person",
        "text",
        "phone",
        "screenshot",
        "cat",
        "dog",
        "car",
        "character",
        "table",
        "chart",
        "game",
        "weapon",
    ]
    found = [word for word in candidates if word in content]
    if found:
        return ", ".join(found)
    combined = ", ".join(part for part in [tags, visual] if part).strip()
    return combined[:180] if combined else "unknown"


def describe_image(
    image: Image.Image, models: LoadedModels, config: IndexerConfig
) -> tuple[str, str]:
    if not models.florence_model or not models.florence_processor:
        return "N/A", "N/A"

    visual = run_florence_task(
        image,
        "<MORE_DETAILED_CAPTION>",
        models=models,
        config=config,
    )
    tags = run_florence_task(
        image,
        "<VQA>What is the category of this meme (reaction, comic, photo, art)? List 5 keywords separated by comma.",
        task="<VQA>",
        models=models,
        config=config,
    )
    return visual, tags


def run_florence_task(
    image: Image.Image,
    prompt: str,
    models: LoadedModels,
    config: IndexerConfig,
    task: str | None = None,
) -> str:
    task_name = task or prompt
    try:
        inputs = models.florence_processor(text=prompt, images=image, return_tensors="pt").to(
            config.device, models.dtype
        )
        generated_ids = models.florence_model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=512,
            num_beams=3,
            do_sample=False,
        )
        generated_text = models.florence_processor.batch_decode(
            generated_ids, skip_special_tokens=False
        )[0]
        parsed = models.florence_processor.post_process_generation(
            generated_text, task=task_name, image_size=(image.width, image.height)
        )
        return parsed.get(task_name, generated_text)
    except Exception as exc:
        return f"Erro em {task_name}: {exc}"


def create_faiss_indices(db_path: Path, model_name: str | None = None) -> None:
    print(f"Construindo indices FAISS para '{db_path}'")
    if not db_path.exists():
        print(f"Banco de dados {db_path} nao encontrado.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, arquivo, embedding, desc_embedding, model_name FROM memes ORDER BY id"
    ).fetchall()
    conn.close()

    if not rows:
        print("Banco vazio. Nenhum indice criado.")
        return

    image_embeddings = np.stack(
        [np.frombuffer(row["embedding"], dtype=np.float32) for row in rows]
    ).astype("float32")
    dimension = int(image_embeddings.shape[1])
    desc_embeddings = np.stack(
        [
            np.frombuffer(row["desc_embedding"], dtype=np.float32)
            if row["desc_embedding"]
            else np.zeros(dimension, dtype=np.float32)
            for row in rows
        ]
    ).astype("float32")

    prefix = db_path.with_suffix("")
    image_path = prefix.with_name(f"{prefix.name}_image.faiss")
    desc_path = prefix.with_name(f"{prefix.name}_desc.faiss")
    manifest_path = prefix.with_name(f"{prefix.name}_manifest.json")

    faiss.normalize_L2(image_embeddings)
    image_index = faiss.IndexFlatIP(dimension)
    image_index.add(image_embeddings)
    faiss.write_index(image_index, str(image_path))

    faiss.normalize_L2(desc_embeddings)
    desc_index = faiss.IndexFlatIP(dimension)
    desc_index.add(desc_embeddings)
    faiss.write_index(desc_index, str(desc_path))

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "db": str(db_path),
        "image_index": str(image_path),
        "desc_index": str(desc_path),
        "model_name": model_name or rows[0]["model_name"],
        "embedding_dim": dimension,
        "count": len(rows),
        "ids": [row["id"] for row in rows],
        "files": [row["arquivo"] for row in rows],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Indices criados: {image_path}, {desc_path}")


def run_index_pipeline(config: IndexerConfig) -> None:
    process_images(config)
    create_faiss_indices(config.db_path, config.model_name)


def main() -> None:
    config = parse_arguments()
    if config.rebuild_faiss_only:
        create_faiss_indices(config.db_path, config.model_name)
        return
    try:
        run_index_pipeline(config)
    except KeyboardInterrupt:
        print("\nInterrompido. Atualizando indices com o progresso salvo...")
        create_faiss_indices(config.db_path, config.model_name)
        sys.exit(130)


if __name__ == "__main__":
    main()
