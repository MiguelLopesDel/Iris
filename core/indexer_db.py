"""Database schema — init, migrations, collections, libraries, memes table."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.concepts import create_concept_tables
from core.import_review import ensure_tables as ensure_import_review_tables
from core.web_enrichment import create_web_enrichment_tables


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA temp_store=MEMORY")
    create_memes_table(conn)
    create_collections_table(conn)
    create_media_collections_table(conn)
    create_media_libraries_table(conn)
    create_concept_tables(conn)
    create_web_enrichment_tables(conn)
    ensure_import_review_tables(conn)
    # Order matters: normalise the table structure first (the rebuild drops the
    # legacy UNIQUE plus any extra columns), THEN add missing columns, THEN index.
    # Doing migrate before rebuild would let the rebuild strip freshly-added
    # columns (perceptual_hash, audio_*), leaving a brand-new DB without them.
    rebuild_memes_if_legacy_unique(conn)
    migrate_schema(conn)
    ensure_memes_indexes(conn)
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
            added_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (meme_id, collection_id),
            FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE,
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
        )
        """
    )
    # Older fresh DBs created the table without `added_at`, yet every insert site
    # (UI add, indexer album assignment) writes it. Add it if missing so those paths
    # don't fail with "no column named added_at".
    cols = {row[1] for row in conn.execute("PRAGMA table_info(media_collections)")}
    if "added_at" not in cols:
        conn.execute("ALTER TABLE media_collections ADD COLUMN added_at TEXT NOT NULL DEFAULT ''")


def find_or_create_collection(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO collections (name, description, created_at) VALUES (?, '', ?)",
        (name, now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def create_media_libraries_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            root_path TEXT,
            created_at TEXT
        )
        """
    )


# ── Canonical `memes` schema (single source of truth) ──────────────────────────
# create_memes_table, the legacy rebuild, and migrate_schema all derive the column
# set from this list, so they can never drift apart. Order mirrors production DBs.
# `arquivo` is intentionally NOT UNIQUE (same filename may live in different folders
# / libraries) — the legacy rebuild exists only to drop that old constraint.
_MEMES_COLUMNS: list[tuple[str, str]] = [
    ("arquivo", "TEXT"),
    ("caminho", "TEXT"),
    ("relative_path", "TEXT"),
    ("storage_path", "TEXT"),
    ("source_path", "TEXT"),
    ("library_id", "INTEGER"),
    ("imported_at", "TEXT DEFAULT ''"),
    ("file_size", "INTEGER DEFAULT 0"),
    ("file_mtime", "REAL DEFAULT 0"),
    ("texto_extraido", "TEXT DEFAULT ''"),
    ("descricao_ia", "TEXT DEFAULT ''"),
    ("tags", "TEXT DEFAULT ''"),
    ("content_hash", "TEXT"),
    ("ocr_normalized", "TEXT DEFAULT ''"),
    ("visual_json", "TEXT DEFAULT ''"),
    ("objects", "TEXT DEFAULT ''"),
    ("style", "TEXT DEFAULT ''"),
    ("source_work", "TEXT DEFAULT ''"),
    ("humor", "TEXT DEFAULT ''"),
    ("context", "TEXT DEFAULT ''"),
    ("error_message", "TEXT DEFAULT ''"),
    ("model_name", "TEXT DEFAULT ''"),
    ("embedding_dim", "INTEGER DEFAULT 0"),
    ("schema_version", "INTEGER DEFAULT 0"),
    ("embedding", "BLOB"),
    ("desc_embedding", "BLOB"),
    ("width", "INTEGER DEFAULT 0"),
    ("height", "INTEGER DEFAULT 0"),
    ("created_at", "TEXT DEFAULT ''"),
    ("audio_fingerprint", "TEXT DEFAULT NULL"),
    ("audio_embedding", "BLOB DEFAULT NULL"),
    ("perceptual_hash", "TEXT DEFAULT NULL"),
    ("metadata_json", "TEXT DEFAULT ''"),
]
_MEMES_FK = "FOREIGN KEY (library_id) REFERENCES media_libraries(id) ON DELETE SET NULL"


def _memes_create_sql(table: str) -> str:
    cols = ",\n            ".join(f"{name} {decl}" for name, decl in _MEMES_COLUMNS)
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n"
        f"            id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        f"            {cols},\n"
        f"            {_MEMES_FK}\n"
        f"        )"
    )


def create_memes_table(conn: sqlite3.Connection) -> None:
    conn.execute(_memes_create_sql("memes"))


def rebuild_memes_if_legacy_unique(conn: sqlite3.Connection) -> None:
    """Drop the legacy UNIQUE(arquivo) constraint from DBs created by older versions.

    Rebuilds into the canonical schema (same column set as create_memes_table), so it
    can no longer strip newer columns. No-op for current DBs, which carry no UNIQUE.
    """
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memes'"
    ).fetchone()
    if not table_sql or "UNIQUE" not in table_sql[0]:
        return
    conn.execute(_memes_create_sql("memes_new"))
    old_columns = [row[1] for row in conn.execute("PRAGMA table_info(memes)")]
    new_columns = [row[1] for row in conn.execute("PRAGMA table_info(memes_new)")]
    columns = ", ".join(c for c in new_columns if c in old_columns)
    conn.execute(f"INSERT INTO memes_new ({columns}) SELECT {columns} FROM memes")
    conn.execute("DROP TABLE memes")
    conn.execute("ALTER TABLE memes_new RENAME TO memes")


def migrate_schema(conn: sqlite3.Connection) -> None:
    # Add any canonical column missing from an older DB (idempotent).
    existing = {row[1] for row in conn.execute("PRAGMA table_info(memes)")}
    for name, decl in _MEMES_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE memes ADD COLUMN {name} {decl}")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS media_libraries (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, root_path TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS collections (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS media_collections (meme_id INTEGER NOT NULL, collection_id INTEGER NOT NULL, added_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (meme_id, collection_id), FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE, FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE)"
    )
    create_concept_tables(conn)
    create_web_enrichment_tables(conn)


def ensure_memes_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memes_content_hash ON memes(content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memes_library_id ON memes(library_id)")
    # Collections: filter-by-collection queries need collection_id as leading column
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_collections_collection_id ON media_collections(collection_id)")
    # Concepts: get_references(concept_id) needs concept_id indexed
    conn.execute("CREATE INDEX IF NOT EXISTS idx_concept_references_concept_id ON concept_references(concept_id)")


def now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now().isoformat()


def get_or_create_library(conn: sqlite3.Connection, name: str, root_path: Path) -> int:
    row = conn.execute(
        "SELECT id FROM media_libraries WHERE name = ?", (name,)
    ).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO media_libraries (name, root_path, created_at) VALUES (?, ?, ?)",
        (name, str(root_path.resolve()), now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def existing_hashes(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT content_hash FROM memes WHERE content_hash IS NOT NULL")
    }


def sanitize_storage_name(relative_path: str) -> str:
    return relative_path.replace("/", "_").replace(" ", "_")


def ensure_unique_destination(
    library_root: Path,
    relative_path: str,
    content_hash: str = "",
) -> Path:
    """Return a unique destination path under library_root for the given relative_path.

    Creates the parent directory. If a file already exists with identical content
    (same SHA-256), returns the existing path so no copy is needed. Otherwise
    appends a numeric counter to find a free slot.
    """
    import hashlib as _hl

    dest = library_root / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        return dest
    # Same content already in library — reuse path, caller skips the copy.
    if content_hash:
        try:
            h = _hl.sha256()
            with open(dest, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            if h.hexdigest() == content_hash:
                return dest
        except Exception:
            pass
    # Different content — find a unique name.
    ext = dest.suffix
    stem = dest.stem
    for i in range(1, 200):
        candidate = dest.parent / f"{stem}_{i}{ext}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Não foi possível definir destino único para {relative_path}")
