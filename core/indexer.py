from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import warnings
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

from core.concepts import create_concept_tables
from core.media_inventory import (
    file_sha256,
    iter_media_files,
    read_manifest,
)
from core.search_engine import DEFAULT_MODEL, normalize_text
from core.taxonomy import (
    build_taxonomy_prompt_rows,
    classify_embedding,
    merge_taxonomy_into_profile,
    values_for_field,
)

os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
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


def parse_arguments() -> IndexerConfig:
    parser = argparse.ArgumentParser(
        description="Indexador de Memes para o Meme Compass",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dir", "-d", default="./media", help="Pasta com imagens e videos.")
    parser.add_argument("--db", "-b", default="meme_compass_v10.db", help="Banco SQLite de saida.")
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
    )


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    create_media_libraries_table(conn)
    create_memes_table(conn)
    rebuild_memes_if_legacy_unique(conn)
    migrate_schema(conn)
    ensure_memes_indexes(conn)
    create_collections_table(conn)
    create_media_collections_table(conn)
    create_concept_tables(conn)
    conn.commit()
    return conn


def create_collections_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )


def create_media_collections_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_collections (
            meme_id INTEGER NOT NULL,
            collection_id INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (meme_id, collection_id),
            FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE,
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
        )
        """
    )


def find_or_create_collection(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO collections (name, description, created_at) VALUES (?, '', ?)",
        (name, now_iso()),
    )
    return int(cursor.lastrowid)


def create_media_libraries_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            root_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def create_memes_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT,
            caminho TEXT,
            relative_path TEXT,
            storage_path TEXT,
            source_path TEXT,
            library_id INTEGER,
            imported_at TEXT,
            file_size INTEGER,
            file_mtime REAL,
            texto_extraido TEXT,
            descricao_ia TEXT,
            tags TEXT,
            content_hash TEXT,
            ocr_normalized TEXT,
            visual_json TEXT,
            objects TEXT,
            style TEXT,
            source_work TEXT,
            humor TEXT,
            context TEXT,
            error_message TEXT,
            model_name TEXT,
            embedding_dim INTEGER,
            schema_version INTEGER,
            embedding BLOB,
            desc_embedding BLOB
        )
        """
    )


def rebuild_memes_if_legacy_unique(conn: sqlite3.Connection) -> None:
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memes'"
    ).fetchone()
    if not table_sql or not table_sql[0]:
        return
    if "ARQUIVO TEXT UNIQUE" not in table_sql[0].upper().replace("\n", " "):
        return

    conn.execute(
        """
        CREATE TABLE memes_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT,
            caminho TEXT,
            relative_path TEXT,
            storage_path TEXT,
            source_path TEXT,
            library_id INTEGER,
            imported_at TEXT,
            file_size INTEGER,
            file_mtime REAL,
            texto_extraido TEXT,
            descricao_ia TEXT,
            tags TEXT,
            content_hash TEXT,
            ocr_normalized TEXT,
            visual_json TEXT,
            objects TEXT,
            style TEXT,
            source_work TEXT,
            humor TEXT,
            context TEXT,
            error_message TEXT,
            model_name TEXT,
            embedding_dim INTEGER,
            schema_version INTEGER,
            embedding BLOB,
            desc_embedding BLOB
        )
        """
    )

    old_columns = [row[1] for row in conn.execute("PRAGMA table_info(memes)")]
    new_columns = [row[1] for row in conn.execute("PRAGMA table_info(memes_new)")]
    common = [column for column in old_columns if column in new_columns]
    if common:
        columns = ", ".join(common)
        conn.execute(f"INSERT INTO memes_new ({columns}) SELECT {columns} FROM memes")
    conn.execute("DROP TABLE memes")
    conn.execute("ALTER TABLE memes_new RENAME TO memes")


def migrate_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memes)")}
    additions = {
        "relative_path": "TEXT",
        "storage_path": "TEXT",
        "source_path": "TEXT",
        "library_id": "INTEGER",
        "imported_at": "TEXT",
        "file_size": "INTEGER",
        "file_mtime": "REAL",
        "tags": "TEXT",
        "content_hash": "TEXT",
        "ocr_normalized": "TEXT",
        "visual_json": "TEXT",
        "objects": "TEXT",
        "style": "TEXT",
        "source_work": "TEXT",
        "humor": "TEXT",
        "context": "TEXT",
        "error_message": "TEXT",
        "model_name": "TEXT",
        "embedding_dim": "INTEGER",
        "schema_version": "INTEGER",
    }
    for column, column_type in additions.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE memes ADD COLUMN {column} {column_type}")


def ensure_memes_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memes_content_hash ON memes(content_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memes_library_storage ON memes(library_id, storage_path)"
    )


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def get_or_create_library(conn: sqlite3.Connection, name: str, root_path: Path) -> int:
    root_path = root_path.resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    row = conn.execute(
        "SELECT id FROM media_libraries WHERE name = ?",
        (name,),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE media_libraries SET root_path = ? WHERE id = ?",
            (str(root_path), int(row[0])),
        )
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO media_libraries (name, root_path, created_at) VALUES (?, ?, ?)",
        (name, str(root_path), now_iso()),
    )
    return int(cursor.lastrowid)


def existing_hashes(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT content_hash FROM memes WHERE content_hash IS NOT NULL")
        if row[0]
    }


def sanitize_storage_name(relative_path: str) -> str:
    candidate = relative_path.strip().replace("\\", "/").lstrip("/")
    if not candidate:
        return "imported.bin"
    parts = [part for part in candidate.split("/") if part not in {"", ".", ".."}]
    return "/".join(parts) if parts else "imported.bin"


def ensure_unique_destination(
    library_root: Path,
    relative_path: str,
    content_hash: str,
) -> Path:
    preferred = library_root / sanitize_storage_name(relative_path)
    if not preferred.exists():
        preferred.parent.mkdir(parents=True, exist_ok=True)
        return preferred

    try:
        if preferred.is_file() and file_sha256(preferred) == content_hash:
            return preferred
    except Exception:
        pass

    stem = preferred.stem
    suffix = preferred.suffix
    directory = preferred.parent
    for counter in range(1, 10000):
        candidate = directory / f"{stem}_{counter:04d}{suffix}"
        if not candidate.exists():
            candidate.parent.mkdir(parents=True, exist_ok=True)
            return candidate
        try:
            if candidate.is_file() and file_sha256(candidate) == content_hash:
                return candidate
        except Exception:
            continue
    raise RuntimeError(f"Nao foi possivel definir destino unico para {relative_path}")


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

    return LoadedModels(
        reader=reader,
        florence_model=florence_model,
        florence_processor=florence_processor,
        clip_model=clip_model,
        whisper_model=whisper_model,
        dtype=dtype,
        taxonomy_rows=taxonomy_rows,
        taxonomy_embeddings=taxonomy_embeddings,
    )


def already_processed(conn: sqlite3.Connection) -> set[str]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memes)")}
    column = "relative_path" if "relative_path" in columns else "arquivo"
    return {row[0] for row in conn.execute(f"SELECT {column} FROM memes WHERE {column} IS NOT NULL")}


def process_images(config: IndexerConfig) -> None:
    if not config.media_dir.exists():
        print(f"Erro: diretorio '{config.media_dir}' nao encontrado.")
        sys.exit(1)

    conn = init_db(config.db_path)
    processed = already_processed(conn)
    known_hashes = existing_hashes(conn)
    media_files = media_files_from_config(config)
    if config.limit:
        media_files = media_files[: config.limit]

    if config.copy_to_library:
        pending = media_files
    else:
        pending = [
            path
            for path in media_files
            if path.relative_to(config.media_dir).as_posix() not in processed and path.name not in processed
        ]

    print(f"Diretorio: {config.media_dir}")
    print(f"Arquivos encontrados: {len(media_files)}")
    print(f"Arquivos novos: {len(pending)}")

    if not pending:
        conn.close()
        return

    max_id_before = 0
    if config.collection_name:
        row = conn.execute("SELECT MAX(id) FROM memes").fetchone()
        max_id_before = int(row[0]) if row and row[0] is not None else 0

    library_root = (config.library_root / config.library_name).resolve()
    library_id = get_or_create_library(conn, config.library_name, library_root)
    models = load_models(config)
    cursor = conn.cursor()

    try:
        with torch.inference_mode():
            for start in tqdm(range(0, len(pending), config.batch_size), desc="Indexando"):
                batch_files = pending[start : start + config.batch_size]
                batch_images: list[Image.Image] = []
                batch_metadata: list[dict[str, object]] = []

                for path in batch_files:
                    try:
                        source_path = path.resolve()
                        content_hash = file_sha256(source_path)
                        if content_hash in known_hashes:
                            continue

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
                        batch_images.append(image)
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
                            }
                        )
                    except Exception as exc:
                        print(f"\nErro ao processar {path.name}: {exc}")

                if not batch_images:
                    continue

                image_embeddings = models.clip_model.encode(
                    batch_images,
                    batch_size=len(batch_images),
                    show_progress_bar=False,
                )
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
                            embedding_dim, schema_version, embedding, desc_embedding
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        ),
                    )
                    known_hashes.add(str(item["content_hash"]))
                conn.commit()
                if config.device == "cuda":
                    torch.cuda.empty_cache()

        if config.collection_name:
            collection_id = find_or_create_collection(conn, config.collection_name)
            new_ids = [
                row[0]
                for row in conn.execute("SELECT id FROM memes WHERE id > ?", (max_id_before,))
            ]
            if new_ids:
                conn.executemany(
                    "INSERT OR IGNORE INTO media_collections (meme_id, collection_id, added_at) VALUES (?, ?, ?)",
                    [(meme_id, collection_id, now_iso()) for meme_id in new_ids],
                )
                conn.commit()
                print(f"Colecao '{config.collection_name}': {len(new_ids)} arquivo(s) adicionado(s).")
    finally:
        conn.close()


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


def load_media_preview(
    path: Path, models: LoadedModels, config: IndexerConfig
) -> tuple[Image.Image, str]:
    if path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}:
        cap = cv2.VideoCapture(str(path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(total_frames // 2, 0))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            raise ValueError("falha ao ler frame do video")
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        audio_text = ""
        if models.whisper_model and has_audio_stream(path):
            try:
                result = models.whisper_model.transcribe(
                    str(path), fp16=(config.device == "cuda")
                )
                audio_text = f" [Audio: {result['text'].strip()}]"
            except Exception as exc:
                print(f"  ! Whisper falhou em {path.name}: {exc}")
        return image, audio_text

    return Image.open(path).convert("RGB"), ""


def extract_text(path: Path, image: Image.Image, audio_text: str, models: LoadedModels) -> str:
    ocr_target = np.array(image) if path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"} else str(path)
    text_results = models.reader.readtext(ocr_target, detail=0, paragraph=True)
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
